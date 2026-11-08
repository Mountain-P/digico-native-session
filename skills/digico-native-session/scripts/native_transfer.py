"""Pure Category-4 native transfer codec for the exact Quantum1 V2126 profile.

This module does not open sockets, send packets, read/write Session files, retry,
load a Session, or negotiate mirroring. Builders produce research bytes only;
complete Category-4 transfer has not been validated by this codec. Decoded names
and paths are private metadata. Keep them in local results.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct
from typing import Optional

from mirror_codec import Packet, build_packet, decode_packet


CATEGORY = 4
FILE_REQUEST = 1
FILE_BLOCK = 2
FILE_ACK = 3
FILE_STATUS = 4
FILE_COMPLETE = 5
SESSION_VERIFY = 6
SESSION_VERIFIED = 7
BLOCK_BYTES = 16384
RECORD_PREFIX_BYTES = 150
MAX_FRAME_BYTES = 16 + RECORD_PREFIX_BYTES + BLOCK_BYTES + 2
CONSOLE_RECORD_BYTES = 123
FILE_PREFIX = struct.Struct('<iiHHHHHI')
SUBCOMMAND_NAMES = {
    FILE_REQUEST: 'file_request', FILE_BLOCK: 'file_block', FILE_ACK: 'file_ack',
    FILE_STATUS: 'file_status', FILE_COMPLETE: 'file_complete',
    SESSION_VERIFY: 'session_verify', SESSION_VERIFIED: 'session_verified',
}


class TransferCodecError(ValueError):
    """Structurally invalid or unsupported transfer message; no action occurred."""


def _integer(value: int, low: int, high: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise TransferCodecError(f'{label} must be an integer in [{low}, {high}]')
    return value


def _bytes(value: bytes, label: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f'{label} must be bytes-like')
    return bytes(value)


def _short_string(value: bytes, *, maximum: int = 255) -> bytes:
    value = _bytes(value, 'short_string')
    if len(value) > maximum:
        raise TransferCodecError(f'ShortString exceeds {maximum} raw bytes')
    return bytes([len(value)]) + value


def _read_short_string(data: bytes, offset: int, *, maximum: int = 255) -> tuple[bytes, int]:
    if offset >= len(data):
        raise TransferCodecError('truncated ShortString length')
    length = data[offset]
    end = offset + 1 + length
    if length > maximum or end > len(data):
        raise TransferCodecError('truncated or oversized ShortString')
    return data[offset + 1:end], end


def _frame(subcommand: int, payload: bytes, *, file_type: int = 0,
           command_id: int = 0, connection_index: int = 0xFF,
           header_byte9_raw: int = 0) -> bytes:
    _integer(file_type, 0, 255, 'file_type')
    _integer(command_id, 0, 65535, 'command_id')
    _integer(header_byte9_raw, 0, 255, 'header_byte9_raw')
    return build_packet(
        category=CATEGORY,
        control_id=bytes([subcommand, file_type, header_byte9_raw]) + struct.pack('<H', command_id),
        payload=payload, connection_index=connection_index, list_index=0,
        max_packet_size=MAX_FRAME_BYTES,
    )


@dataclass(frozen=True)
class FileRecord:
    """tNetFileRec fixed prefix plus optional file-data bytes.

    n_block is the block carried on the wire. Successful ACK keeps that value;
    the original sender increments its received ACK copy to select the next block.
    timeout_count is a raw peer tick deadline, not a timestamp or retry guarantee.
    The 128-byte filename field is a Pascal ShortString with at most 127 raw bytes.
    Filename padding is retained when decoding and is not interpreted as text.
    """

    n_block: int
    n_blocks: int
    block_size: int
    file_type: int
    command_id: int
    unique_id: int
    error_code: int
    timeout_count: int
    filename: bytes
    data: bytes = b''
    filename_padding: Optional[bytes] = None

    def __post_init__(self) -> None:
        for field in ('n_block', 'n_blocks'):
            _integer(getattr(self, field), 0, 0x7FFFFFFF, field)
        _integer(self.block_size, 0, BLOCK_BYTES, 'block_size')
        for field in ('file_type', 'command_id', 'unique_id', 'error_code'):
            _integer(getattr(self, field), 0, 65535, field)
        _integer(self.timeout_count, 0, 0xFFFFFFFF, 'timeout_count')
        filename = _bytes(self.filename, 'filename')
        data = _bytes(self.data, 'data')
        if len(filename) > 127:
            raise TransferCodecError('file record filename exceeds 127 raw bytes')
        if len(data) > BLOCK_BYTES:
            raise TransferCodecError('file data exceeds 16384 bytes')
        padding = self.filename_padding
        if padding is None:
            padding = bytes(127 - len(filename))
        else:
            padding = _bytes(padding, 'filename_padding')
            if len(padding) != 127 - len(filename):
                raise TransferCodecError('filename padding does not fill its 128-byte field')
        object.__setattr__(self, 'filename', filename)
        object.__setattr__(self, 'data', data)
        object.__setattr__(self, 'filename_padding', padding)

    def prefix_bytes(self) -> bytes:
        return FILE_PREFIX.pack(
            self.n_block, self.n_blocks, self.block_size, self.file_type,
            self.command_id, self.unique_id, self.error_code, self.timeout_count,
        ) + _short_string(self.filename, maximum=127) + self.filename_padding

    def as_dict(self, *, include_data: bool = False) -> dict:
        result = {
            field: getattr(self, field) for field in (
                'n_block', 'n_blocks', 'block_size', 'file_type', 'command_id',
                'unique_id', 'error_code', 'timeout_count',
            )
        }
        result.update(filename_hex=self.filename.hex(),
                      filename_padding_hex=self.filename_padding.hex(),
                      data_bytes=len(self.data),
                      data_sha256=hashlib.sha256(self.data).hexdigest())
        if include_data:
            result['data_hex'] = self.data.hex()
        return result


def decode_file_record(payload: bytes, *, has_data: bool) -> FileRecord:
    payload = _bytes(payload, 'payload')
    if len(payload) < RECORD_PREFIX_BYTES:
        raise TransferCodecError('truncated 150-byte file-record prefix')
    values = FILE_PREFIX.unpack_from(payload)
    filename, end = _read_short_string(payload[:RECORD_PREFIX_BYTES], FILE_PREFIX.size,
                                       maximum=127)
    data = payload[RECORD_PREFIX_BYTES:]
    if not has_data and data:
        raise TransferCodecError('ACK must carry the 150-byte prefix without file data')
    if has_data and len(data) != values[2]:
        raise TransferCodecError('BlockSize differs from wire file-data extent')
    return FileRecord(*values, filename, data, payload[end:RECORD_PREFIX_BYTES])


@dataclass(frozen=True)
class SessionVerifyRecord:
    direction_raw: int
    console_record: bytes
    state_checksum: int
    strings: tuple[bytes, bytes, bytes]

    def __post_init__(self) -> None:
        _integer(self.direction_raw, 0, 255, 'direction_raw')
        console = _bytes(self.console_record, 'console_record')
        if len(console) != CONSOLE_RECORD_BYTES:
            raise TransferCodecError('SessionVerify console record must contain 123 bytes')
        _integer(self.state_checksum, 0, 0xFFFFFFFF, 'state_checksum')
        if not isinstance(self.strings, tuple) or len(self.strings) != 3:
            raise TransferCodecError('SessionVerify requires exactly three raw ShortStrings')
        strings = tuple(_bytes(value, 'verify_string') for value in self.strings)
        for value in strings:
            _short_string(value)
        object.__setattr__(self, 'console_record', console)
        object.__setattr__(self, 'strings', strings)

    def payload_bytes(self) -> bytes:
        return (bytes([self.direction_raw]) + self.console_record +
                struct.pack('<I', self.state_checksum) +
                b''.join(_short_string(value) for value in self.strings))

    def as_dict(self) -> dict:
        return {'direction_raw': self.direction_raw,
                'console_record_hex': self.console_record.hex(),
                'state_checksum': self.state_checksum,
                'strings_hex': [value.hex() for value in self.strings],
                'acceptance_conditions_evaluated': False}


@dataclass(frozen=True)
class TransferMessage:
    packet: Packet
    subcommand: int
    file_type: int
    command_id: int
    file_record: Optional[FileRecord] = None
    string_bytes: Optional[bytes] = None
    verify_record: Optional[SessionVerifyRecord] = None
    accepted_raw: Optional[int] = None
    direction_raw: Optional[int] = None
    verified_reserved_hex: Optional[str] = None

    def as_dict(self, *, include_data: bool = False) -> dict:
        result = {
            'schema_version': 1, 'subsystem': 'native-transfer-codec',
            'protocol_profile': 'Quantum1-V2126',
            'runtime_acceptance_verified': False,
            'category': CATEGORY, 'subcommand': self.subcommand,
            'kind': SUBCOMMAND_NAMES[self.subcommand], 'file_type': self.file_type,
            'command_id': self.command_id, 'connection_index': self.packet.connection_index,
            'size': self.packet.size, 'checksum_valid': True,
            'control_id_hex': self.packet.control_id.raw.hex(),
            'header_byte9_raw': self.packet.control_id.raw[2],
            'contains_private_metadata': True,
        }
        if self.file_record is not None:
            result['file_record'] = self.file_record.as_dict(include_data=include_data)
        if self.string_bytes is not None:
            result['string_hex'] = self.string_bytes.hex()
        if self.verify_record is not None:
            result['verify_record'] = self.verify_record.as_dict()
        if self.accepted_raw is not None:
            result.update(accepted_raw=self.accepted_raw, direction_raw=self.direction_raw,
                          reserved_hex=self.verified_reserved_hex)
        return result


def decode_transfer_packet(raw: bytes) -> TransferMessage:
    packet = decode_packet(raw, max_packet_size=MAX_FRAME_BYTES)
    if packet.category != CATEGORY:
        raise TransferCodecError('expected native Category 4')
    subcommand = packet.control_id.raw[0]
    if subcommand not in SUBCOMMAND_NAMES:
        raise TransferCodecError('unsupported Category-4 subcommand')
    file_type = packet.control_id.raw[1]
    command_id = packet.control_id.idhi
    payload = packet.payload
    fields = {}
    if subcommand == FILE_REQUEST:
        value, end = _read_short_string(payload, 0)
        # Native allocator pads the single empty length byte to a two-byte body.
        if payload[end:] != bytes(max(0, 2-end)):
            raise TransferCodecError('unexpected File Request trailing bytes')
        fields['string_bytes'] = value
    elif subcommand in (FILE_BLOCK, FILE_ACK):
        record = decode_file_record(payload, has_data=subcommand == FILE_BLOCK)
        if record.file_type != file_type or record.command_id != command_id:
            raise TransferCodecError('wire and file-record FileType/CommandID differ')
        fields['file_record'] = record
    elif subcommand == FILE_STATUS:
        if len(payload) < 4:
            raise TransferCodecError('truncated File Status string length')
        length = struct.unpack_from('<I', payload)[0]
        if length > 255 or length != len(payload)-4:
            raise TransferCodecError('invalid File Status string extent')
        fields['string_bytes'] = payload[4:]
    elif subcommand == FILE_COMPLETE:
        if payload != bytes(2):
            raise TransferCodecError('unexpected File Transfer Complete body')
    elif subcommand == SESSION_VERIFY:
        if len(payload) < 131:
            raise TransferCodecError('truncated SessionVerify fixed fields/strings')
        strings = []
        end = 128
        for _ in range(3):
            value, end = _read_short_string(payload, end)
            strings.append(value)
        if end != len(payload):
            raise TransferCodecError('unexpected SessionVerify trailing bytes')
        fields['verify_record'] = SessionVerifyRecord(
            payload[0], payload[1:124], struct.unpack_from('<I', payload, 124)[0],
            tuple(strings),
        )
    elif subcommand == SESSION_VERIFIED:
        if len(payload) != 4:
            raise TransferCodecError('SessionVerified must contain a four-byte value')
        fields.update(accepted_raw=payload[0], direction_raw=payload[1],
                      verified_reserved_hex=payload[2:].hex())
    return TransferMessage(packet, subcommand, file_type, command_id, **fields)


def build_file_request(filename: bytes = b'', *, file_type: int = 0,
                       command_id: int = 0, connection_index: int = 0xFF) -> bytes:
    """Synthetic request. An actual request can cause peer Save and later Load."""
    return _frame(FILE_REQUEST, _short_string(filename), file_type=file_type,
                  command_id=command_id, connection_index=connection_index)


def build_file_block(record: FileRecord, *, connection_index: int = 0xFF,
                     header_byte9_raw: int = 0) -> bytes:
    if len(record.data) != record.block_size:
        raise TransferCodecError('BlockSize differs from file-data extent')
    return _frame(FILE_BLOCK, record.prefix_bytes()+record.data,
                  file_type=record.file_type, command_id=record.command_id,
                  connection_index=connection_index, header_byte9_raw=header_byte9_raw)


def build_file_ack(record: FileRecord, *, connection_index: int = 0xFF,
                   source_control_id: Optional[bytes] = None) -> bytes:
    """Copy prefix and incoming ControlID except subcommand, without n_block++.

    Native BF3FEC leaves packet byte9 unspecified; allocator BEB6E4 does not
    initialize it. BF3B30 copies all incoming five ControlID bytes then changes
    only subcommand to3. Preserve that raw byte when acknowledging a real block.
    A manually constructed synthetic ACK defaults byte9 to deterministic zero.
    """
    byte9 = 0
    if source_control_id is not None:
        control = _bytes(source_control_id, 'source_control_id')
        if (len(control) != 5 or control[0] not in (FILE_BLOCK, FILE_ACK) or
                control[1] != record.file_type or
                struct.unpack_from('<H', control, 3)[0] != record.command_id):
            raise TransferCodecError('ACK source ControlID differs from its file record')
        byte9 = control[2]
    return _frame(FILE_ACK, record.prefix_bytes(), file_type=record.file_type,
                  command_id=record.command_id, connection_index=connection_index,
                  header_byte9_raw=byte9)


def build_ack_for_block(block: TransferMessage, *, connection_index: int = 0xFF) -> bytes:
    """Use a checksum-validated decoded block; no send or file-write is implied."""
    if (not isinstance(block, TransferMessage) or block.subcommand != FILE_BLOCK or
            block.file_record is None):
        raise TransferCodecError('ACK requires a decoded File Block')
    return build_file_ack(block.file_record, connection_index=connection_index,
                          source_control_id=block.packet.control_id.raw)


def build_file_status(value: bytes, *, file_type: int = 0,
                      command_id: int = 0, connection_index: int = 0xFF) -> bytes:
    value = _bytes(value, 'status_value')
    if len(value) > 255:
        raise TransferCodecError('File Status string exceeds native ShortString input')
    return _frame(FILE_STATUS, struct.pack('<I', len(value))+value, file_type=file_type,
                  command_id=command_id, connection_index=connection_index)


def build_file_complete(*, connection_index: int = 0xFF) -> bytes:
    return _frame(FILE_COMPLETE, b'', connection_index=connection_index)


def build_session_verify(record: SessionVerifyRecord, *, connection_index: int = 0xFF) -> bytes:
    return _frame(SESSION_VERIFY, record.payload_bytes(), connection_index=connection_index)


def build_session_verified(*, accepted: int, direction: int,
                           connection_index: int = 0xFF) -> bytes:
    _integer(accepted, 0, 1, 'accepted')
    _integer(direction, 0, 1, 'direction')
    return _frame(SESSION_VERIFIED, bytes([accepted, direction, 0, 0]),
                  connection_index=connection_index)

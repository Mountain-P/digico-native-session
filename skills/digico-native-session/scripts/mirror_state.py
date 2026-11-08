"""Passive, bounded native Mirror decoding and evidence ledger.

No sockets, packet builders, file I/O, UI, or process access. A compatibility
acceptance is not a completed initial sync. Unassigned bytes/flags stay opaque;
unsupported flags never update the raw control observation store. This module
does not implement a console, DSP, session loader, or native flag state machine.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import struct

from mirror_codec import MirrorProtocolError, StreamDecoder, decode_packet
from native_transfer import SessionVerifyRecord, TransferCodecError, decode_transfer_packet

PROFILES = {'quantum1-v2126': 2126}
DIRECTIONS = ('peer_to_client', 'client_to_peer')
MAX_FRAME_BYTES = 65536  # Local decoder bound, not a recovered protocol maximum.
MAX_BATCH_RECORDS = 4096
PROPAGATED_FLAGS_MASK = 0xE0C0  # BF2814 receiver mask, not named flag meanings.


class MirrorStateError(ValueError):
    def __init__(self, message):
        super().__init__(message)
        self.valid_events = ()


def _bytes(value, label):
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise MirrorStateError('%s must be bytes-like' % label)
    return bytes(value)


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise MirrorStateError('%s must be an integer in [%d, %d]' % (label, low, high))
    return value


def _short(data, offset, maximum=255):
    if offset >= len(data):
        raise MirrorStateError('truncated ShortString length')
    size = data[offset]
    end = offset + 1 + size
    if size > maximum or end > len(data):
        raise MirrorStateError('truncated or oversized ShortString')
    return data[offset + 1:end], end


def _profile(profile):
    if profile not in PROFILES:
        raise MirrorStateError('unsupported Mirror wire profile; cross-build equivalence is not assumed')
    return PROFILES[profile]


def _typed_value(raw, raw_type):
    if raw_type in (0, 1):
        value = struct.unpack('<f', raw)[0]
        return value if math.isfinite(value) else None
    if raw_type == 2:
        return struct.unpack('<i', raw)[0]
    return None


def _control_types(types):
    if types is None:
        return {}
    if not isinstance(types, dict):
        raise MirrorStateError('control_types must map exact five-byte hex IDs to native datatypes')
    result = {}
    for key, raw_type in types.items():
        if not isinstance(key, str) or not re.fullmatch(r'[0-9a-fA-F]{10}', key):
            raise MirrorStateError('invalid control_types ID')
        key = key.lower()
        if key in result:
            raise MirrorStateError('duplicate case-normalized control_types ID')
        result[key] = _integer(raw_type, 0, 255, 'native datatype')
    return result


def decode_state_packet(raw, *, control_types=None, profile='quantum1-v2126', strict=True):
    """Decode one checksum-valid frame, preserving uncertain data as raw bytes.

    control_types must come from an independently bound native inventory. A
    Category-8 four-byte payload is never assumed to be float32 without it.
    strict=False retains unsupported message families as opaque events.
    """
    version = _profile(profile)
    types = _control_types(control_types)
    packet = decode_packet(raw, max_packet_size=MAX_FRAME_BYTES)
    body, control = packet.payload, packet.control_id.raw
    event = {'schema_version': 1, 'profile': profile, 'category': packet.category,
             'control_id_hex': control.hex(), 'list_index_raw': packet.list_index,
             'connection_index_raw': packet.connection_index,
             'checksum_valid': True, 'runtime_acceptance_verified': False,
             'contains_private_metadata': True}
    if packet.category == 0 and control[0] == 1:
        if packet.control_id.idhi != version:
            raise MirrorStateError('identity version does not match the selected wire profile')
        if len(body) < 5 or body[0] not in (0, 1, 2):
            raise MirrorStateError('unsupported identity state or truncated identity')
        name, end = _short(body, 4, 30)
        if end != len(body):
            raise MirrorStateError('unexpected identity trailing bytes')
        event.update(kind='identity', state_raw=body[0], flags_raw=body[1],
                     network_id_raw=body[2], integration_raw=body[3],
                     name_hex=name.hex(), protocol_version=version,
                     role_flags_interpreted=False)
    elif packet.category == 0 and control[0] == 2:
        if packet.control_id.idhi != version or len(body) < 10:
            raise MirrorStateError('session information version/extent mismatch')
        path, end = _short(body, 8)
        secondary, end = _short(body, end)
        if end != len(body) or not math.isfinite(struct.unpack_from('<d', body)[0]):
            raise MirrorStateError('invalid session information fields')
        event.update(kind='session_information', timestamp_raw_hex=body[:8].hex(),
                     path_hex=path.hex(), secondary_hex=secondary.hex())
    elif packet.category == 0 and control[0] == 5:
        if len(body) < 4:
            raise MirrorStateError('truncated mirrored-peer list')
        count = struct.unpack_from('<I', body)[0]
        if count > 1024 or len(body) != 4 + 4 * count:
            raise MirrorStateError('invalid mirrored-peer list extent/local bound')
        if packet.list_index not in (0, 1, 2, 3):
            raise MirrorStateError('unverified mirroring mode value')
        event.update(kind='mirrored_peer_list', mode_raw=packet.list_index,
                     peer_ipv4_hex=[body[i:i+4].hex() for i in range(4, len(body), 4)],
                     mode_labels_verified=False)
    elif packet.category == 0 and control[0] == 7:
        if len(body) != 28:
            raise MirrorStateError('unverified option-state extent')
        event.update(kind='option_state', option_bytes_hex=body.hex(), named_flags_verified=False)
    elif packet.category == 4 and control[0] in (6, 7):
        # BF13D8 clears packet bytes7..10, then writes subcommand6, while
        # packet byte11 remains uninitialized. Keep that fifth ID byte opaque.
        if control[1:4] != bytes(3) or packet.list_index != 0:
            raise MirrorStateError('unsupported SessionVerify/Verified header fields')
        try:
            message = decode_transfer_packet(packet.raw)
        except TransferCodecError as error:
            raise MirrorStateError(str(error)) from error
        if control[0] == 6:
            record = message.verify_record
            if record.direction_raw not in (0, 1):
                raise MirrorStateError('unverified SessionVerify direction')
            event.update(kind='session_verify', verify_record=record.as_dict(),
                         header_byte11_opaque=control[4])
        else:
            if message.accepted_raw not in (0, 1) or message.direction_raw not in (0, 1):
                raise MirrorStateError('unsupported SessionVerified boolean value')
            if message.verified_reserved_hex != '0000':
                raise MirrorStateError('unknown SessionVerified reserved flags')
            event.update(kind='session_verified', accepted=bool(message.accepted_raw),
                         direction_raw=message.direction_raw, header_byte11_opaque=control[4])
    elif packet.category == 9:
        discriminator = int.from_bytes(control[:4], 'little')
        if discriminator not in (0, 1) or packet.list_index != 0:
            raise MirrorStateError('unsupported Category-9 discriminator/header')
        if not body or len(body) % 12 or len(body) // 12 > MAX_BATCH_RECORDS:
            raise MirrorStateError('Category-9 body must contain bounded complete 12-byte records')
        records = []
        for offset in range(0, len(body), 12):
            record = body[offset:offset + 12]
            key = record[:5].hex()
            flags = struct.unpack_from('<H', record, 6)[0]
            raw_type = types.get(key)
            value = record[8:12]
            records.append({'id': key, 'byte5_opaque': record[5], 'flags_raw': flags,
                            'unknown_flags_raw': flags & ~PROPAGATED_FLAGS_MASK,
                            'value_raw_hex': value.hex(), 'native_datatype': raw_type,
                            'typed_value': _typed_value(value, raw_type),
                            'semantic_application_supported': False})
        event.update(kind='control_batch' if discriminator == 0 else 'meter_tally_batch',
                     records=records, header_byte11_opaque=control[4],
                     completion_marker=False)
    elif packet.category == 8:
        key, raw_type = control.hex(), types.get(control.hex())
        flags = packet.list_index & 0xFFFFFFFF
        event.update(kind='control_value', id=key, native_datatype=raw_type,
                     flags_raw=flags, unknown_flags_raw=flags & ~PROPAGATED_FLAGS_MASK,
                     semantic_application_supported=False)
        if raw_type == 4:
            if len(body) < 4 or struct.unpack_from('<I', body)[0] != len(body) - 4:
                raise MirrorStateError('invalid native string-control extent')
            event.update(string_value_hex=body[4:].hex())
        elif raw_type in (0, 1, 2):
            if len(body) != 4:
                raise MirrorStateError('numeric native control must carry exactly four bytes')
            event.update(value_raw_hex=body.hex(), typed_value=_typed_value(body, raw_type))
        else:
            # Even four bytes can be an unknown integer, floating value, flags,
            # or special native control. Preserve them without picking a type.
            event.update(value_opaque_hex=body.hex(), typed_value=None)
    else:
        if strict:
            raise MirrorStateError('unsupported message family; no semantic interpretation was made')
        event.update(kind='opaque', payload_hex=body.hex(), semantic_application_supported=False)
    return event


@dataclass(frozen=True)
class VerifyBaseline:
    """Independent local session evidence; this object cannot create its proof.

    session_sha256 binds the caller's already-exported Session provenance.
    checksum must be the actual sum of native subsystem hashes, not count/name
    fingerprints. Flag names intentionally remain unnamed slot observations.
    binary_signature is the complete already-converted wire byte string, without
    its Pascal length prefix. The native RTL codepage roundtrip can change its
    length; the 100 preconversion source codepoints are not this field.
    """
    console_record: bytes
    state_checksum: int
    session_reference: bytes
    binary_signature: bytes
    mode_flag_a: int
    mode_flag_b: int
    baseline_id: str
    session_sha256: str

    def __post_init__(self):
        for field in ('console_record', 'session_reference', 'binary_signature'):
            object.__setattr__(self, field, _bytes(getattr(self, field), field))
        if len(self.console_record) != 123:
            raise MirrorStateError('baseline requires an exact 123-byte console record')
        if len(self.binary_signature) > 255:
            raise MirrorStateError('baseline signature must fit the 0..255-byte Pascal wire string; no truncation is performed')
        if not 2 <= len(self.session_reference) <= 255 or any(byte > 127 for byte in self.session_reference):
            raise MirrorStateError('baseline requires independently observed ASCII session reference')
        _integer(self.state_checksum, 0, 0xFFFFFFFF, 'state_checksum')
        for field in ('mode_flag_a', 'mode_flag_b'):
            _integer(getattr(self, field), 0, 1, field)
        if not isinstance(self.baseline_id, str) or not self.baseline_id:
            raise MirrorStateError('baseline_id must identify independent local session evidence')
        if not isinstance(self.session_sha256, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', self.session_sha256):
            raise MirrorStateError('session_sha256 must bind the independently exported Session')


def evaluate_session_verify(record, baseline):
    """Evaluate the recovered compatibility predicates, never send acceptance.

    V2126 compares console[0:27], console[0x6f:0x74], the path after its first
    byte (local side ASCII-lowercased), aggregate state hash, binary signature,
    and !(mode_flag_a && mode_flag_b). It does not compare every live value.
    The second ShortString is retained/copied by native code, not compared here.
    """
    if not isinstance(record, SessionVerifyRecord) or not isinstance(baseline, VerifyBaseline):
        raise MirrorStateError('requires a decoded SessionVerifyRecord and independent VerifyBaseline')
    if record.direction_raw not in (0, 1):
        raise MirrorStateError('unsupported SessionVerify direction')
    remote_path = record.strings[0]
    path_supported = len(remote_path) >= 2 and all(byte < 128 for byte in remote_path)
    checks = {'console_prefix_27': record.console_record[:27] == baseline.console_record[:27],
              'console_offsets_6f_to_73': record.console_record[0x6F:0x74] == baseline.console_record[0x6F:0x74],
              'session_reference': path_supported and remote_path[1:] == baseline.session_reference.lower()[1:],
              'aggregate_native_state_checksum': record.state_checksum == baseline.state_checksum,
              'binary_signature': record.strings[2] == baseline.binary_signature,
              'local_mode_gate': not (baseline.mode_flag_a and baseline.mode_flag_b)}
    return {'profile': 'quantum1-v2126', 'baseline_id': baseline.baseline_id,
            'session_sha256': baseline.session_sha256.lower(), 'checks': checks,
            'eligible_under_recovered_predicates': all(checks.values()),
            'signature_byte_lengths': {'received': len(record.strings[2]), 'baseline': len(baseline.binary_signature)},
            'secondary_string_compared': False, 'current_surface_values_compared': False,
            'requires_native_mode_recheck': True, 'runtime_acceptance_verified': False,
            'initial_sync_complete': False}


class MirrorTrace:
    """Bounded two-direction passive ledger, reset at each TCP connection epoch.

    Observe an actual sent/received stream in its original order. Unknown flags
    remain diagnostic events and never enter controls. Accepted compatibility
    only schedules the expected current-state direction; no recovered end marker
    permits this class to claim a complete mirror replica.
    """

    def __init__(self, *, control_types=None, profile='quantum1-v2126', baseline=None,
                 max_stream_bytes=32 * 1024 * 1024, max_controls=200000):
        _profile(profile)
        self.types = _control_types(control_types)
        self.profile, self.baseline = profile, baseline
        self.max_stream_bytes = _integer(max_stream_bytes, 20, 1024 * 1024 * 1024, 'max_stream_bytes')
        self.max_controls = _integer(max_controls, 1, 200000, 'max_controls')
        if baseline is not None and not isinstance(baseline, VerifyBaseline):
            raise MirrorStateError('baseline must be VerifyBaseline')
        self.epoch = 0
        self.begin_connection()

    def begin_connection(self):
        self.epoch += 1
        self.decoders = {direction: StreamDecoder(max_packet_size=MAX_FRAME_BYTES) for direction in DIRECTIONS}
        self.phase, self.failed, self.closed = 'awaiting_identity', False, False
        self.byte_count, self.event_count, self.opaque_count, self.unsupported_flag_count = 0, 0, 0, 0
        self.controls, self.pending_verify = {}, {}
        self.expected_state_direction = None
        self.compatibility_accepted = False

    def _observe(self, direction, packet):
        event = decode_state_packet(packet.raw, control_types=self.types, profile=self.profile, strict=False)
        event.update(direction=direction, connection_epoch=self.epoch)
        self.event_count += 1
        kind = event['kind']
        if kind == 'identity':
            if event['state_raw'] == 0:
                self.disconnect()
            elif self.phase == 'awaiting_identity':
                self.phase = 'identity_observed'
        elif kind == 'session_verify':
            if any(key in self.pending_verify for key in DIRECTIONS):
                raise MirrorStateError('overlapping SessionVerify exchanges are not supported')
            record = decode_transfer_packet(packet.raw).verify_record
            self.pending_verify[direction] = record.direction_raw
            self.phase = 'session_verification_observed'
            if direction == 'peer_to_client' and self.baseline is not None:
                event['comparison'] = evaluate_session_verify(record, self.baseline)
                self.pending_verify['local_eligibility'] = event['comparison']['eligible_under_recovered_predicates']
        elif kind == 'session_verified':
            requester = DIRECTIONS[1] if direction == DIRECTIONS[0] else DIRECTIONS[0]
            if self.pending_verify.get(requester) != event['direction_raw']:
                raise MirrorStateError('SessionVerified has no matching opposite-direction verify in this connection epoch')
            if event['accepted']:
                if direction == 'client_to_peer' and not self.pending_verify.get('local_eligibility', False):
                    raise MirrorStateError('observed client acceptance lacks an independently matching local baseline')
                self.compatibility_accepted = True
                self.phase = 'compatibility_accepted'
                self.expected_state_direction = requester if event['direction_raw'] else direction
            else:
                self.compatibility_accepted = False
                self.phase = 'compatibility_rejected'
                self.expected_state_direction = None
            self.pending_verify.pop(requester, None)
            self.pending_verify.pop('local_eligibility', None)
        elif kind in ('control_batch', 'control_value'):
            records = event['records'] if kind == 'control_batch' else [event]
            for record in records:
                if record['unknown_flags_raw']:
                    self.unsupported_flag_count += 1
                    continue
                if 'value_raw_hex' not in record and 'string_value_hex' not in record:
                    continue
                if record.get('native_datatype') not in (0, 1, 2, 4):
                    continue
                if len(self.controls) >= self.max_controls and record['id'] not in self.controls:
                    raise MirrorStateError('control observation count exceeds local bound')
                self.controls[record['id']] = {key: record[key] for key in
                                              ('value_raw_hex', 'string_value_hex', 'native_datatype', 'flags_raw') if key in record}
                self.controls[record['id']].update(direction=direction, connection_epoch=self.epoch)
            if self.compatibility_accepted and direction == self.expected_state_direction:
                self.phase = 'current_state_data_observed'
        elif kind == 'opaque':
            self.opaque_count += 1
        return event

    def feed(self, direction, data):
        if direction not in DIRECTIONS:
            raise MirrorStateError('direction must be peer_to_client or client_to_peer')
        if self.failed or self.closed:
            raise MirrorStateError('trace is failed or disconnected; begin a fresh connection epoch')
        data = _bytes(data, 'stream data')
        events = []
        try:
            if self.byte_count + len(data) > self.max_stream_bytes:
                raise MirrorStateError('stream byte count exceeds local bound')
            self.byte_count += len(data)
            framing_error = None
            try:
                packets = self.decoders[direction].feed(data)
            except MirrorProtocolError as error:
                packets, framing_error = error.valid_packets, error
            for packet in packets:
                events.append(self._observe(direction, packet))
            if framing_error is not None:
                raise MirrorStateError(str(framing_error)) from framing_error
            return events
        except MirrorStateError as error:
            self.failed, self.phase = True, 'failed'
            error.valid_events = tuple(events)
            raise

    def finish(self, direction):
        if direction not in DIRECTIONS:
            raise MirrorStateError('invalid stream direction')
        try:
            self.decoders[direction].finish()
        except MirrorProtocolError as error:
            self.failed, self.phase = True, 'failed'
            raise MirrorStateError(str(error)) from error

    def disconnect(self):
        self.closed, self.compatibility_accepted = True, False
        self.phase, self.expected_state_direction = 'disconnected', None
        self.pending_verify.clear()

    def snapshot(self, *, include_values=False):
        result = {'schema_version': 1, 'profile': self.profile, 'connection_epoch': self.epoch,
                  'phase': self.phase, 'failed': self.failed, 'disconnected': self.closed,
                  'stream_bytes': self.byte_count, 'event_count': self.event_count,
                  'opaque_message_count': self.opaque_count,
                  'unsupported_flag_record_count': self.unsupported_flag_count,
                  'compatibility_acceptance_observed': self.compatibility_accepted,
                  'expected_current_state_direction': self.expected_state_direction,
                  'observed_control_count': len(self.controls),
                  'observations_stale': self.closed or self.failed,
                  'initial_sync_complete': False, 'end_marker_verified': False,
                  'runtime_acceptance_verified': False}
        if include_values:
            result['controls'] = {key: dict(self.controls[key]) for key in sorted(self.controls)}
            result['contains_private_metadata'] = True
        return result

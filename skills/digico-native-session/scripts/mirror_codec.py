"""Passive framing codec; constructed packets are synthetic, never desk-validated.

The meaningful fixed prefix is 16 bytes. The Delphi record's int/float/data union
starts at offset 16, so the apparent 20-byte header overlaps the variable body.
At least two body bytes precede a two-byte additive checksum. No opcode meaning,
session serialization, handshake, retransmission, or transport is implemented.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import struct
from typing import Optional, Tuple

PREFIX = struct.Struct("<IHB5si")
MIN_PACKET_SIZE = 20
MAX_PACKET_SIZE = 16 * 1024 * 1024  # Local decoder resource bound, not a desk limit.


class MirrorProtocolError(ValueError):
    """Invalid/truncated input. The stream decoder fails permanently on this error.

    valid_packets contains independently validated packets earlier in the same
    feed call. They were not returned because the later packet caused an error.
    """

    def __init__(self, message: str, *, offset: int = 0) -> None:
        super().__init__(message)
        self.offset = offset
        self.valid_packets: Tuple[Packet, ...] = ()


class ChecksumError(MirrorProtocolError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            "checksum mismatch: calculated 0x%04x, stored 0x%04x" % (expected, actual)
        )
        self.expected = expected
        self.actual = actual


def _integer(value: int, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError("%s must be an integer in [%d, %d]" % (name, minimum, maximum))
    return value


def _bytes(value: bytes, name: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError("%s must be bytes-like" % name)
    return bytes(value)


def _bound(max_packet_size: int) -> int:
    return _integer(max_packet_size, MIN_PACKET_SIZE, MAX_PACKET_SIZE, "max_packet_size")


def checksum16(data: bytes) -> int:
    """Sum supplied bytes modulo 65536; pass frame[:-2] for the wire checksum."""
    return sum(_bytes(data, "data")) & 0xFFFF


@dataclass(frozen=True)
class ControlID:
    """Five packed raw bytes; names are Delphi aliases, not universal semantics."""

    raw: bytes

    def __post_init__(self) -> None:
        raw = _bytes(self.raw, "control_id")
        if len(raw) != 5:
            raise ValueError("control_id must contain exactly five bytes")
        object.__setattr__(self, "raw", raw)

    @property
    def parameter(self) -> int:
        return self.raw[0]

    @property
    def module(self) -> int:
        return self.raw[1]

    @property
    def band(self) -> int:
        return self.raw[2]

    @property
    def channel(self) -> int:
        return self.raw[3]

    @property
    def channel_type(self) -> int:
        return self.raw[4]

    @property
    def group(self) -> int:
        return self.raw[4]

    @property
    def id32(self) -> int:
        return struct.unpack("<i", self.raw[:4])[0]

    @property
    def idlo(self) -> int:
        return struct.unpack("<H", self.raw[:2])[0]

    @property
    def idhi(self) -> int:
        return struct.unpack("<H", self.raw[3:5])[0]

    def as_dict(self) -> dict:
        return {
            "raw_hex": self.raw.hex(),
            "aliases": {
                "parameter": self.parameter, "module": self.module,
                "band": self.band, "channel": self.channel,
                "channel_type": self.channel_type, "group": self.group,
                "id32": self.id32, "idlo": self.idlo, "idhi": self.idhi,
            },
        }


@dataclass(frozen=True)
class Packet:
    size: int
    connection_index: int
    category: int
    control_id: ControlID
    list_index: int
    payload: bytes
    checksum: int
    raw: bytes
    stream_offset: Optional[int] = None

    @property
    def int_value(self) -> Optional[int]:
        """First four body bytes viewed as signed int32; not a category interpretation."""
        return struct.unpack("<i", self.payload[:4])[0] if len(self.payload) >= 4 else None

    @property
    def float_value(self) -> Optional[float]:
        """First four body bytes viewed as IEEE float32; not a category interpretation."""
        return struct.unpack("<f", self.payload[:4])[0] if len(self.payload) >= 4 else None

    def as_dict(self, preview_bytes: int = 256) -> dict:
        _integer(preview_bytes, 0, MAX_PACKET_SIZE, "preview_bytes")
        float_value = self.float_value
        # Preserve nonfinite IEEE values while emitting standards-compliant JSON.
        if float_value is not None and not math.isfinite(float_value):
            float_value = "NaN" if math.isnan(float_value) else (
                "+Infinity" if float_value > 0 else "-Infinity"
            )
        return {
            "size": self.size, "stream_offset": self.stream_offset,
            "connection_index": self.connection_index, "category": self.category,
            "control_id": self.control_id.as_dict(), "list_index": self.list_index,
            "payload_length": len(self.payload),
            "payload_hex": self.payload[:preview_bytes].hex(),
            "payload_preview_truncated": len(self.payload) > preview_bytes,
            "body_overlay": {"int32": self.int_value, "float32": float_value},
            "checksum": self.checksum, "checksum_valid": True,
        }


def _validate_size(size: int, max_packet_size: int) -> None:
    if not MIN_PACKET_SIZE <= size <= max_packet_size:
        raise MirrorProtocolError(
            "declared packet size %d is outside [%d, %d]" % (
                size, MIN_PACKET_SIZE, max_packet_size
            )
        )


def decode_packet(data: bytes, *, max_packet_size: int = MAX_PACKET_SIZE) -> Packet:
    """Decode one complete frame, requiring exact length and a correct checksum."""
    max_packet_size = _bound(max_packet_size)
    data = _bytes(data, "data")
    if len(data) < 4:
        raise MirrorProtocolError("truncated size field: need four bytes")
    size = struct.unpack_from("<I", data)[0]
    _validate_size(size, max_packet_size)
    if len(data) != size:
        raise MirrorProtocolError(
            "frame length mismatch: declared %d, supplied %d" % (size, len(data))
        )
    stored = struct.unpack_from("<H", data, size - 2)[0]
    calculated = checksum16(data[:-2])
    if calculated != stored:
        raise ChecksumError(calculated, stored)
    size, connection_index, category, control_id, list_index = PREFIX.unpack_from(data)
    return Packet(
        size, connection_index, category, ControlID(control_id), list_index,
        data[PREFIX.size:-2], stored, data,
    )


def build_packet(
    *, category: int, control_id: bytes, payload: bytes,
    connection_index: int = 0xFF, list_index: int = 0,
    max_packet_size: int = MAX_PACKET_SIZE,
) -> bytes:
    """Build synthetic bytes from explicit fields; this does not transmit anything.

    Bodies shorter than two bytes are zero-padded to match the observed native
    allocator. A four-byte int/float body produces a 22-byte frame, not 24 bytes.
    No category, control identifier, or payload is inferred by this helper.
    """
    max_packet_size = _bound(max_packet_size)
    _integer(category, 0, 0xFF, "category")
    _integer(connection_index, 0, 0xFFFF, "connection_index")
    _integer(list_index, -(2 ** 31), 2 ** 31 - 1, "list_index")
    control = ControlID(control_id)
    payload = _bytes(payload, "payload")
    payload += b"\0" * max(0, 2 - len(payload))
    size = PREFIX.size + len(payload) + 2
    _validate_size(size, max_packet_size)
    body = PREFIX.pack(size, connection_index, category, control.raw, list_index) + payload
    return body + struct.pack("<H", checksum16(body))


class StreamDecoder:
    """Incremental decoder for one reassembled TCP direction, with no resync.

    A malformed size/checksum or finish() with trailing partial bytes permanently
    fails the instance. Its next feed is rejected, so bytes after corruption can
    never silently become a new packet. Already validated packets earlier in a
    failing feed are attached to the exception as valid_packets.
    """

    def __init__(self, *, max_packet_size: int = MAX_PACKET_SIZE) -> None:
        self.max_packet_size = _bound(max_packet_size)
        self._buffer = bytearray()
        self.offset = 0
        self.failed = False
        self.finished = False

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, data: bytes) -> list[Packet]:
        if self.failed or self.finished:
            raise MirrorProtocolError("decoder is failed or finished; create a new instance", offset=self.offset)
        self._buffer.extend(_bytes(data, "data"))
        packets = []
        try:
            while len(self._buffer) >= 4:
                size = struct.unpack_from("<I", self._buffer)[0]
                _validate_size(size, self.max_packet_size)
                if len(self._buffer) < size:
                    break
                packet = decode_packet(bytes(self._buffer[:size]), max_packet_size=self.max_packet_size)
                packets.append(replace(packet, stream_offset=self.offset))
                del self._buffer[:size]
                self.offset += size
        except MirrorProtocolError as error:
            self.failed = True
            error.offset = self.offset
            error.valid_packets = tuple(packets)
            raise
        return packets

    def finish(self) -> None:
        if self.failed or self.finished:
            raise MirrorProtocolError("decoder is failed or finished; create a new instance", offset=self.offset)
        if self._buffer:
            self.failed = True
            raise MirrorProtocolError(
                "truncated stream: %d trailing bytes" % len(self._buffer), offset=self.offset
            )
        self.finished = True

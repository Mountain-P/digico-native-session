"""Native C9 channel structure and file-backed D3 routing controls."""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct

from ses_console import CurrentConsole
from ses_snapshot import MAX_CHANNELS, SnapshotFormatError, _Reader


class SesRoutingError(SnapshotFormatError):
    pass


_CHANNEL_WIDTH = {2025: 88, 2126: 120, 2242: 121}
_PORT_WIDTH = 210
_PORT_COUNT = 50
_INPUT_GROUP = 0x1c
_OUTPUT_GROUP = 0x4e
_INPUT_KINDS = {
    'main': (0, 1, 0),
    'alt': (0, 1, 1),
    'key': (0x16, 5, 0),
    'mustard_key': (0x16, 0x1e, 0),
    'insert_return': (0, 8, 0),
}
_ZERO = b'\0' * 4
_NEGATIVE_ZERO = b'\0\0\0\x80'
_ONE = struct.pack('<f', 1)
_SEND_LEVEL_RANGE = (-150, 10)
_AUX_SEND = (1, 2, 2)
_MATRIX_SEND = (0x24, 0x25, 6)


def _bounded_int(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise SesRoutingError(f'{label} must be an integer in {low}..{high}')
    return value


def _short_name(metadata, offset, capacity):
    length = metadata[offset]
    if length > capacity:
        raise SesRoutingError('Stored name exceeds its ShortString capacity')
    return metadata[offset + 1:offset + 1 + length]


@dataclass(frozen=True)
class SocketGroups:
    """C9 stores two SocketGroup TStringLists, with their parallel DWORD keys."""

    text: bytes
    keys: tuple[int, ...]

    @staticmethod
    def _rows(text):
        if not text:
            return ()
        if (not isinstance(text, bytes) or not text.endswith(b'\r\n')
                or b'\0' in text):
            raise SesRoutingError('Socket-group text must contain terminated native CRLF rows')
        rows = text[:-2].split(b'\r\n')
        if len(rows) > MAX_CHANNELS or any(b'\r' in row or b'\n' in row for row in rows):
            raise SesRoutingError('Unreviewed socket-group row boundaries')
        return rows

    @classmethod
    def _read(cls, reader):
        length = _bounded_int(reader.i32(), 'Socket-group text length', 0, 1024 * 1024)
        text = reader.read(length)
        rows = cls._rows(text)
        keys = tuple(key for key, in struct.iter_unpack('<I', reader.read(4 * len(rows))))
        return cls(text, keys)

    def to_bytes(self):
        rows = self._rows(self.text)
        if len(self.text) > 1024 * 1024 or len(rows) != len(self.keys):
            raise SesRoutingError('Socket-group labels and keys disagree')
        for key in self.keys:
            _bounded_int(key, 'Socket-group key', 0, 0xffffffff)
        return (struct.pack('<i', len(self.text)) + self.text
                + b''.join(struct.pack('<I', key) for key in self.keys))

    def bank_name(self, key):
        matches = [row for row, value in zip(self._rows(self.text), self.keys) if value == key and row]
        return matches[0] if len(matches) == 1 else None


@dataclass
class C9Channel:
    metadata: bytes
    buss_matrix: bytes = b''

    @staticmethod
    def _read_buss_matrix(reader):
        start = reader.position
        reader.token(0xe7)
        for _ in range(reader.count(MAX_CHANNELS)):
            reader.token(0xe6)
            reader.read(7)
            reader.block()
        reader.read(1)
        reader.block()
        reader.read(1)
        return reader.data[start:reader.position]

    @property
    def channel_id(self):
        return struct.unpack_from('<H', self.metadata)[0]

    @property
    def logical_channel(self):
        return struct.unpack_from('<H', self.metadata, 2)[0]

    @property
    def stereo_mode(self):
        return self.metadata[0x25]

    @property
    def buss_id(self):
        return struct.unpack_from('<H', self.metadata, 0x32)[0]

    @property
    def name_bytes(self):
        return _short_name(self.metadata, 4, 31)

    def set_name(self, name_bytes):
        if len(self.metadata) not in _CHANNEL_WIDTH.values():
            raise SesRoutingError('C9 channel metadata has an unreviewed width')
        if (not isinstance(name_bytes, bytes) or not 1 <= len(name_bytes) <= 31
                or b'\0' in name_bytes):
            raise SesRoutingError('C9 name requires 1..31 encoded bytes without NUL')
        self.metadata = (self.metadata[:4] + bytes((len(name_bytes),)) + name_bytes
                         + self.metadata[5 + len(name_bytes):])
        return self

    @property
    def preset_index(self):
        return struct.unpack_from('<i', self.metadata, 0x26)[0]

    def set_preset_index(self, index):
        _bounded_int(index, 'Preset index', -1, 0x7fffffff)
        if len(self.metadata) not in _CHANNEL_WIDTH.values():
            raise SesRoutingError('C9 channel metadata has an unreviewed width')
        self.metadata = self.metadata[:0x26] + struct.pack('<i', index) + self.metadata[0x2a:]
        return self

    def _validate(self, version, group_id):
        if not isinstance(self.metadata, bytes) or len(self.metadata) != _CHANNEL_WIDTH[version]:
            raise SesRoutingError('C9 channel metadata has an unreviewed width')
        if self.channel_id >> 8 != group_id:
            raise SesRoutingError('C9 channel ID disagrees with its parent group')
        self.name_bytes
        if self.buss_matrix:
            if group_id != 6:
                raise SesRoutingError('C9 buss-matrix extension requires a Group output')
            reader = _Reader(self.buss_matrix, version)
            self._read_buss_matrix(reader)
            reader.finish()

    def _bytes(self):
        return struct.pack('<HH', 0xcb, len(self.metadata)) + self.metadata + self.buss_matrix


@dataclass
class C9Group:
    tag: int
    metadata: bytes
    channels: list[C9Channel]

    @property
    def group_id(self):
        return self.metadata[0]

    def _validate(self, version):
        if self.tag not in (0xca, 0xd1) or not isinstance(self.metadata, bytes) or len(self.metadata) != 108:
            raise SesRoutingError('Unreviewed C9 group record')
        expected_tag = 0xd1 if 20 <= self.group_id <= 23 else 0xca
        if self.tag != expected_tag:
            raise SesRoutingError('C9 group token disagrees with group ID')
        _short_name(self.metadata, 1, 31)
        if len(self.channels) > 256 or (self.tag == 0xd1 and self.channels):
            raise SesRoutingError('Unreviewed C9 group channel layout')
        for channel in self.channels:
            channel._validate(version, self.group_id)
        if len({channel.channel_id for channel in self.channels}) != len(self.channels):
            raise SesRoutingError('Duplicate C9 channel ID')

    def _bytes(self):
        return (struct.pack('<HH', self.tag, len(self.metadata)) + self.metadata
                + struct.pack('<i', len(self.channels))
                + b''.join(channel._bytes() for channel in self.channels))


@dataclass
class C9Console:
    version: int
    input_groups: SocketGroups
    output_groups: SocketGroups
    groups: list[C9Group]

    @classmethod
    def from_bytes(cls, data, version=2242):
        reader = _Reader(data, version)
        reader.token(0xc9)
        inputs, outputs = SocketGroups._read(reader), SocketGroups._read(reader)
        groups = []
        for _ in range(reader.count(256)):
            tag = reader.u16()
            if tag not in (0xca, 0xd1):
                raise SesRoutingError('Unknown C9 group token')
            group_metadata = reader.block().data
            channels = []
            count = reader.count(256)
            if tag == 0xd1 and count:
                raise SesRoutingError('Nonempty D1 group has an unreviewed channel layout')
            for _ in range(count):
                reader.token(0xcb)
                metadata = reader.block().data
                matrix = b''
                if reader.data[reader.position:reader.position + 2] == b'\xe7\0':
                    matrix = C9Channel._read_buss_matrix(reader)
                channels.append(C9Channel(metadata, matrix))
            groups.append(C9Group(tag, group_metadata, channels))
        reader.finish()
        result = cls(version, inputs, outputs, groups)
        result._validate()
        return result

    def _validate(self):
        if type(self.version) is not int or self.version not in _CHANNEL_WIDTH:
            raise SesRoutingError('Unreviewed C9 section version')
        if len(self.groups) > 256 or sum(len(group.channels) for group in self.groups) > MAX_CHANNELS:
            raise SesRoutingError('C9 channel tree exceeds its bounds')
        for group in self.groups:
            group._validate(self.version)
        if len({group.group_id for group in self.groups}) != len(self.groups):
            raise SesRoutingError('Duplicate C9 group ID')

    def to_bytes(self):
        self._validate()
        return (b'\xc9\0' + self.input_groups.to_bytes() + self.output_groups.to_bytes()
                + struct.pack('<i', len(self.groups))
                + b''.join(group._bytes() for group in self.groups))

    def inspect(self, encoding='ascii'):
        if encoding not in ('ascii', 'cp950', 'utf-8'):
            raise SesRoutingError('Unsupported topology name encoding')
        self._validate()
        return {'version': self.version,
                'groups': [{'group_id': group.group_id, 'tag': group.tag,
                            'name': _short_name(group.metadata, 1, 31).decode(encoding, errors='replace'),
                            'channels': [{'channel_id': channel.channel_id,
                                          'logical_channel': channel.logical_channel,
                                          'name': channel.name_bytes.decode(encoding, errors='replace'),
                                          'stereo_mode': channel.stereo_mode,
                                          'preset_index': channel.preset_index,
                                          'has_buss_matrix': bool(channel.buss_matrix)}
                                         for channel in group.channels]}
                           for group in self.groups]}


@dataclass(frozen=True)
class IODevice:
    metadata: bytes
    input_types: bytes
    output_types: bytes

    @property
    def port_key(self):
        return self.metadata[11], self.metadata[10]

    @property
    def device_type(self):
        return self.metadata[8]


def read_io_devices(data, version=2242):
    """Read the FC list of FB configured device records."""
    reader = _Reader(data, version)
    reader.token(0xfc)
    devices = {}
    for _ in range(reader.count(MAX_CHANNELS)):
        reader.token(0xfb)
        metadata = reader.block().data
        if len(metadata) != 12:
            raise SesRoutingError('Unreviewed I/O device metadata width')
        inputs, outputs = struct.unpack_from('<ii', metadata)
        input_types = reader.read(_bounded_int(inputs, 'Device input count', 0, 65536))
        output_types = reader.read(_bounded_int(outputs, 'Device output count', 0, 65536))
        device = IODevice(metadata, input_types, output_types)
        if device.port_key in devices:
            raise SesRoutingError('Duplicate configured I/O port')
        devices[device.port_key] = device
    reader.finish()
    return devices


def read_ports(data, version=2242):
    """Read the F6 array of 50 fixed-width native port records."""
    reader = _Reader(data, version)
    reader.token(0xf6)
    raw = reader.block().data
    reader.finish()
    if len(raw) != _PORT_COUNT * _PORT_WIDTH:
        raise SesRoutingError('Unreviewed native port table extent')
    return tuple(raw[i:i + _PORT_WIDTH] for i in range(0, len(raw), _PORT_WIDTH))


def build_local_ports(build_profile):
    """Build a local-only F6 from reviewed per-model field assignments."""
    profiles = ('quantum1-v2126', 'quantum1-v2242-4', 'quantum2-v2242-4',
                'quantum3-v2242', 'quantum5-v2242', 'quantum7-v2242', 'quantum8-v2242')
    if build_profile not in profiles:
        raise SesRoutingError('Local port construction is unreviewed for this profile')
    raw = bytearray(_PORT_COUNT * _PORT_WIDTH)
    # Native local socket-port setup writes these bank descriptors, flags,
    # and key to slot zero. Its port number is zero.
    local_fields = {
        0x01: 0x0b, 0x02: 0x10, 0x03: 0x02, 0x04: 0x03,
        0x08: 0x16, 0x0e: 0x04, 0x38: 0x01, 0x3e: 0x03, 0xd0: 0x01,
    }
    if build_profile in ('quantum7-v2242', 'quantum8-v2242'):
        local_fields[0x02] = 0x18
        if build_profile == 'quantum7-v2242':
            local_fields[0x08] = 0x02
        for offset in (0x08, 0x0e, 0x38, 0x3e):
            for bank_offset in (0x0c, 0x18):
                local_fields[offset + bank_offset] = local_fields[offset]
    for offset, value in local_fields.items():
        raw[offset] = value
    return struct.pack('<HH', 0xf6, len(raw)) + raw


@dataclass(frozen=True)
class _StoredControl:
    module: object
    offset: int

    @property
    def raw(self):
        return self.module.controls[self.offset + 4:self.offset + 8]

    @property
    def route(self):
        return struct.unpack('<I', self.raw)[0]

    def replace(self, raw):
        offset = self.offset + 4
        self.module.controls = self.module.controls[:offset] + raw + self.module.controls[offset + 4:]


class SesRouting:
    """Route edits on a current mix with corroborating saved channel topology."""

    def __init__(self, c9, console, ports=None, devices=None):
        if (not isinstance(c9, C9Console) or not isinstance(console, CurrentConsole)
                or console.module_pool is not None or c9.version != console.version):
            raise SesRoutingError('Routing requires matching C9 and current-state D3 records')
        c9._validate()
        self.c9 = c9
        self.console = console
        self.topology = {channel.channel_id: channel for group in c9.groups for channel in group.channels}
        self.mix = {}
        for channel in console.snapshot.channels:
            if channel.channel_id in self.mix:
                raise SesRoutingError('Duplicate D3 channel ID')
            self.mix[channel.channel_id] = channel
        if (self.topology.keys() != self.mix.keys()
                or any(self.mix[channel_id].metadata.effective(39)[38] != channel.stereo_mode
                       for channel_id, channel in self.topology.items())):
            raise SesRoutingError('C9 and D3 channel IDs or stereo modes disagree')
        self.ports = read_ports(ports, c9.version) if ports is not None else None
        self.devices = read_io_devices(devices, c9.version) if devices is not None else {}

    def _channel(self, channel_id, *, groups, modes):
        _bounded_int(channel_id, 'Channel ID', 0, 65535)
        channel = self.topology.get(channel_id)
        if channel is None or channel_id >> 8 not in groups or channel.stereo_mode not in modes:
            raise SesRoutingError('Channel ID or stereo mode is outside the reviewed routing domain')
        return channel

    def _control(self, channel_id, key, *, optional=False):
        channel = self.mix[channel_id]
        if not channel.own_modules.effective(1)[0]:
            raise SesRoutingError('Route control requires an owned D3 module')
        matches = []
        prefix = bytes(key)
        for module in channel.modules:
            module_type = struct.unpack_from('<i', module.header.effective(58))[0]
            for offset in range(0, len(module.controls), 8):
                record = module.controls[offset:offset + 8]
                if record[:3] != prefix:
                    continue
                if module_type != key[1] or module.parent_module_index != -1 or record[3] != 0:
                    raise SesRoutingError('Route control has ambiguous owner, inheritance, or flags')
                matches.append(_StoredControl(module, offset))
        if len(matches) == 1:
            return matches[0]
        if optional and not matches:
            return None
        raise SesRoutingError(f'Missing or ambiguous route control {prefix.hex()} for {channel_id:#06x}')

    def _socket(self, channel_id, direction):
        first = _INPUT_GROUP if direction == 'input' else _OUTPUT_GROUP
        # tPortRec slot zero is the first persisted local socket group.
        self._channel(channel_id, groups=range(first, first + 49), modes=(1,))
        index = (channel_id >> 8) - first
        socket_index = channel_id & 255
        port_type = port_number = socket_type = None
        if self.ports is not None:
            port = self.ports[index]
            port_type, port_number = port[0xd0], port[0xd1]
            if not port_type:
                raise SesRoutingError('Physical socket has no configured port record')
            device = self.devices.get((port_type, port_number))
            if device is not None:
                types = device.input_types if direction == 'input' else device.output_types
                if socket_index >= len(types) or types[socket_index] in (0, 0x1b):
                    raise SesRoutingError('Socket is outside the configured device or is unfitted')
                socket_type = types[socket_index]
        if direction == 'input':
            copy = self._control(channel_id, (0xfe, 0x11, 0))
            listen = self._control(channel_id, (0xeb, 0x11, 0), optional=True)
            if copy.route or (listen is not None and listen.raw not in (_ZERO, _NEGATIVE_ZERO)):
                raise SesRoutingError('Physical input is configured for copy routing')
            source = None
        else:
            source = self._control(channel_id, (0, 0x12, 0)).route
        banks = self.c9.input_groups if direction == 'input' else self.c9.output_groups
        return {'channel_id': channel_id, 'port_index': index, 'port_type': port_type,
                'port_number': port_number, 'socket_index': socket_index, 'socket_type': socket_type,
                'bank_name_raw': banks.bank_name(channel_id & 0xfff8), 'source_route_uint32': source}

    def _endpoint(self, selector, direction):
        if type(selector) is int:
            return self._socket(selector, direction)
        if not isinstance(selector, dict) or set(selector) != {'port_type', 'port_number', 'socket_index'}:
            raise SesRoutingError('Physical endpoint requires a channel ID or port_type/port_number/socket_index')
        port_type = _bounded_int(selector['port_type'], 'Port type', 1, 255)
        port_number = _bounded_int(selector['port_number'], 'Port number', 0, 255)
        socket_index = _bounded_int(selector['socket_index'], 'Socket index', 0, 255)
        if self.ports is None:
            raise SesRoutingError('Port tuple requires a saved F6 port table')
        candidates = [i for i, row in enumerate(self.ports[:-1]) if row[0xd0:] == bytes((port_type, port_number))]
        if len(candidates) != 1:
            raise SesRoutingError('Saved port tuple is missing or ambiguous')
        first = _INPUT_GROUP if direction == 'input' else _OUTPUT_GROUP
        return self._socket(((first + candidates[0]) << 8) | socket_index, direction)

    def set_input_route(self, channel_id, source, route_kind='main', insert_index=0):
        channel = self._channel(channel_id, groups=(1, 0x0f), modes=(1, 2))
        if not isinstance(route_kind, str) or route_kind not in _INPUT_KINDS:
            raise SesRoutingError('Unknown Input route kind')
        if channel_id >> 8 == 0x0f and (channel.stereo_mode != 1 or route_kind not in ('main', 'alt')):
            raise SesRoutingError('Matrix Input routing supports mono main and alt only')
        if channel.stereo_mode == 2 and route_kind not in ('main', 'alt'):
            raise SesRoutingError('Stereo Input routing supports main and alt only')
        band = _bounded_int(insert_index, 'Insert index', 0, 1)
        if route_kind != 'insert_return' and band:
            raise SesRoutingError('Insert index applies only to insert_return')
        key = _INPUT_KINDS[route_kind]
        if route_kind == 'insert_return':
            key = (*key[:2], band)
        control = self._control(channel_id, key)
        value = 0
        if source is not None:
            endpoint = self._endpoint(source, 'input')
            if channel.stereo_mode == 2:
                self._check_stereo_input_pair(endpoint)
            value = endpoint['channel_id'] << 16
        control.replace(struct.pack('<I', value))
        return self

    def _check_stereo_input_pair(self, left):
        # Native ConnectInput saves one base route and derives its right signal
        # through IncrementSourceRoute and the physical channel group's order.
        left_id = left['channel_id']
        right_id = left_id + 1
        group = next(group for group in self.c9.groups if group.group_id == left_id >> 8)
        if ((left_id & 0xfff8) != (right_id & 0xfff8)
                or not any((first.channel_id, second.channel_id) == (left_id, right_id)
                           for first, second in zip(group.channels, group.channels[1:]))):
            raise SesRoutingError('Stereo Input requires consecutive physical sockets in one saved bank')
        right = self._socket(right_id, 'input')
        if left['socket_type'] != right['socket_type']:
            raise SesRoutingError('Stereo Input sockets have different configured types')

    def _stereo_companion(self, channel):
        companion = self.topology.get(channel.buss_id)
        if (channel.channel_id >> 8 not in (5, 6) or channel.stereo_mode != 2
                or companion is None or companion.channel_id >> 8 != 7
                or companion.stereo_mode != 1 or companion.buss_id != channel.channel_id):
            raise SesRoutingError('Stereo buss has no reciprocal mono right-leg record')
        return companion.channel_id

    def _output_source(self, channel_id, leg):
        channel = self._channel(channel_id, groups=(1, 5, 6, 0x10), modes=(1, 2))
        if channel.stereo_mode == 1:
            if leg is not None:
                raise SesRoutingError('leg applies only to stereo Aux or Group buses')
            return channel_id, (channel_id,)
        if leg not in ('left', 'right'):
            raise SesRoutingError('Stereo output routing requires a left or right leg')
        right = self._stereo_companion(channel)
        return (channel_id if leg == 'left' else right), (channel_id, right)

    def _check_output_commands(self, owners, target):
        """Bind saved FE0E commands to their corresponding output-socket state."""
        bound = []
        for owner in owners:
            command = self._control(owner, (0xfe, 0x0e, 0))
            if not command.route:
                continue
            if command.route & 0x8000ffff:
                raise SesRoutingError('Saved output command uses an unreviewed route encoding')
            endpoint = self._socket(command.route >> 16, 'output')
            if endpoint['source_route_uint32'] != owner << 16:
                raise SesRoutingError('Saved output command disagrees with the output socket')
            bound.append(command)
        for owner, channel in self.mix.items():
            if owner in owners or not channel.own_modules.effective(1)[0]:
                continue
            for module in channel.modules:
                if struct.unpack_from('<i', module.header.effective(58))[0] != 0x0e:
                    continue
                for parameter, module_id, band, _flags, value in struct.iter_unpack('<BBBBi', module.controls):
                    if ((parameter, module_id, band) == (0xfe, 0x0e, 0)
                            and value and abs(value) >> 16 == target):
                        raise SesRoutingError('Another channel has a saved command for this output')
        return bound

    def set_output_route(self, channel_id, destination, connected, leg=None):
        if type(connected) is not bool:
            raise SesRoutingError('connected must be Boolean')
        source, owners = self._output_source(channel_id, leg)
        endpoint = self._endpoint(destination, 'output')
        target = endpoint['channel_id']
        control = self._control(target, (0, 0x12, 0))
        old = control.route
        wanted = source << 16
        if connected and old not in (0, wanted):
            raise SesRoutingError('Physical output is occupied by another route')
        if not connected and old != wanted:
            return self
        commands = self._check_output_commands(owners, target)
        if not connected:
            # Stereo FE0E commands can reconnect adjacent sockets. Clear the
            # saved commands for the pair while keeping the other 0012 routes.
            for command in commands:
                if len(owners) == 2 or command.route >> 16 == target:
                    command.replace(_ZERO)
        control.replace(struct.pack('<I', wanted if connected else 0))
        return self

    def set_group_assignment(self, channel_id, group_channel_id, enabled):
        if type(enabled) is not bool:
            raise SesRoutingError('enabled must be Boolean')
        self._channel(channel_id, groups=(1,), modes=(1,))
        self._channel(group_channel_id, groups=(6,), modes=(1, 2))
        self._control(group_channel_id, (0x21, 0x0c, 0))
        control = self._control(channel_id, (9, 3, group_channel_id & 255))
        if control.raw not in (_ZERO, _NEGATIVE_ZERO, _ONE):
            raise SesRoutingError('Group assignment value is outside its Boolean encoding')
        control.replace(_ONE if enabled else _ZERO)
        return self

    def set_aux_send(self, channel_id, aux_channel_id, level, enabled):
        self._channel(channel_id, groups=(1,), modes=(1,))
        self._channel(aux_channel_id, groups=(5,), modes=(1,))
        return self._set_send(channel_id, aux_channel_id, level, enabled, _AUX_SEND)

    def set_matrix_send(self, channel_id, matrix_channel_id, level, enabled):
        self._channel(channel_id, groups=(0x0f,), modes=(1,))
        self._channel(matrix_channel_id, groups=(0x10,), modes=(1,))
        if matrix_channel_id not in self._matrix_send_destinations():
            raise SesRoutingError('Matrix Output has no matching native band position')
        return self._set_send(channel_id, matrix_channel_id, level, enabled, _MATRIX_SEND)

    def _matrix_send_destinations(self):
        group = next((group for group in self.c9.groups if group.group_id == 0x10), None)
        if group is None:
            return []
        return [channel.channel_id for band, channel in enumerate(group.channels)
                if channel.channel_id == 0x1000 + band and channel.logical_channel == band + 1
                and channel.stereo_mode == 1]

    def _set_send(self, channel_id, destination, level, enabled, keys):
        if type(enabled) is not bool:
            raise SesRoutingError('enabled must be Boolean')
        low, high = _SEND_LEVEL_RANGE
        if type(level) not in (int, float) or not low <= level <= high:
            raise SesRoutingError(f'Send level requires finite dB in {low}..{high}')
        level_key, on_key, module = keys
        band = destination & 255
        send_level = self._control(channel_id, (level_key, module, band))
        send_on = self._control(channel_id, (on_key, module, band))
        if send_on.raw not in (_ZERO, _NEGATIVE_ZERO, _ONE):
            raise SesRoutingError('Send on value is outside its Boolean encoding')
        send_level.replace(struct.pack('<f', level))
        send_on.replace(_ONE if enabled else _ZERO)
        return self

    def _inspect_sends(self, channel_id, destinations, keys, id_key):
        sends = []
        level_key, on_key, module = keys
        for destination in destinations:
            band = destination & 255
            level = self._control(channel_id, (level_key, module, band), optional=True)
            enabled = self._control(channel_id, (on_key, module, band), optional=True)
            if level is None or enabled is None:
                continue
            value = struct.unpack('<f', level.raw)[0]
            sends.append({id_key: destination, 'level': value if math.isfinite(value) else None,
                          'enabled': {_ZERO: False, _NEGATIVE_ZERO: False, _ONE: True}.get(enabled.raw)})
        return sends

    def inspect(self, encoding='ascii', channel_id=None):
        if encoding not in ('ascii', 'cp950', 'utf-8'):
            raise SesRoutingError('Unsupported routing name encoding')
        if channel_id is not None:
            _bounded_int(channel_id, 'Channel ID', 0, 65535)
            if channel_id not in self.topology:
                raise SesRoutingError('Channel ID not present in routing topology')
        sockets = {'inputs': [], 'outputs': []}
        for direction, first in (('input', _INPUT_GROUP), ('output', _OUTPUT_GROUP)):
            for endpoint_id in sorted(self.topology):
                if not first <= endpoint_id >> 8 < first + 49:
                    continue
                try:
                    endpoint = self._socket(endpoint_id, direction)
                except SesRoutingError:
                    continue
                raw = endpoint.pop('bank_name_raw')
                endpoint['bank_name'] = raw.decode(encoding, errors='replace') if raw is not None else None
                endpoint['name'] = self.topology[endpoint_id].name_bytes.decode(encoding, errors='replace')
                sockets[direction + 's'].append(endpoint)
        channels = []
        mono_aux_ids = [aux_id for aux_id in sorted(self.topology)
                        if aux_id >> 8 == 5 and self.topology[aux_id].stereo_mode == 1]
        matrix_ids = self._matrix_send_destinations()
        for source_id in sorted(self.topology):
            group_id = source_id >> 8
            if group_id not in (1, 5, 6, 0x0f, 0x10) or (channel_id is not None and channel_id != source_id):
                continue
            row = {'channel_id': source_id, 'stereo_mode': self.topology[source_id].stereo_mode,
                   'input_routes': {}, 'group_assignments': [],
                   'physical_outputs': [e['channel_id'] for e in sockets['outputs']
                                        if e['source_route_uint32'] == source_id << 16]}
            if group_id in (5, 6) and row['stereo_mode'] == 2:
                try:
                    right = self._stereo_companion(self.topology[source_id])
                except SesRoutingError:
                    right = None
                row['output_legs'] = {
                    'left': row['physical_outputs'],
                    'right': [e['channel_id'] for e in sockets['outputs']
                              if right is not None and e['source_route_uint32'] == right << 16],
                    'right_channel_id': right,
                }
            if group_id in (1, 0x0f):
                for kind, key in _INPUT_KINDS.items():
                    if group_id == 0x0f and kind not in ('main', 'alt'):
                        continue
                    bands = (0, 1) if kind == 'insert_return' else (key[2],)
                    for band in bands:
                        control = self._control(source_id, (*key[:2], band), optional=True)
                        if control is not None:
                            label = f'{kind}_{band}' if kind == 'insert_return' else kind
                            row['input_routes'][label] = control.route
            if group_id == 1:
                if row['stereo_mode'] == 1:
                    row['aux_sends'] = self._inspect_sends(source_id, mono_aux_ids, _AUX_SEND, 'aux_channel_id')
                for group_channel_id in sorted(self.topology):
                    if group_channel_id >> 8 != 6:
                        continue
                    control = self._control(source_id, (9, 3, group_channel_id & 255), optional=True)
                    if control is not None and control.raw == _ONE:
                        row['group_assignments'].append(group_channel_id)
            elif group_id == 0x0f and row['stereo_mode'] == 1:
                row['matrix_sends'] = self._inspect_sends(source_id, matrix_ids, _MATRIX_SEND, 'matrix_channel_id')
            channels.append(row)
        return {'version': self.c9.version, 'channels': channels, **sockets,
                'native_load_verified': False, 'physical_audio_verified': False}

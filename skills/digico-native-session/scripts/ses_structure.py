"""Construct a bounded Quantum channel topology and its initial stored mix."""
from __future__ import annotations

from dataclasses import dataclass
import struct

from ses_routing import C9Channel, C9Console, C9Group, SocketGroups
from ses_snapshot import ChannelSnapshot, ConsoleSnapshot, ModuleSnapshot, _Block


class StructureError(ValueError):
    """A channel configuration is outside the recovered construction contract."""


@dataclass(frozen=True)
class _Limits:
    inputs: int
    buss_legs: int
    matrix_inputs: int
    matrix_outputs: int
    control_groups: int
    graphic_eqs: int
    spice_rack: int
    talkback_outputs: int
    analog_inputs: int
    aes_inputs: int
    aes_outputs: int
    led_brightness: int = 255
    bridge_brightness: int = 255
    encoder_macros_unassigned: bool = False
    talkback_at_input_end: bool = False
    video_links: int = 0


# Bounds stay within the native factory topologies inspected for each build.
# They are deliberately tighter than the hardware's advertised DSP capacity.
_LIMITS = {
    'quantum1-v2242-4': _Limits(48, 9, 12, 8, 12, 12, 6, 1, 16, 2, 2,
                                encoder_macros_unassigned=True, talkback_at_input_end=True),
    'quantum2-v2242-4': _Limits(48, 18, 12, 12, 12, 16, 6, 2, 8, 4, 4),
    'quantum3-v2242': _Limits(48, 18, 24, 24, 24, 24, 8, 2, 8, 8, 8, bridge_brightness=128),
    'quantum5-v2242': _Limits(108, 18, 36, 36, 24, 32, 12, 3, 8, 8, 8, led_brightness=127),
    'quantum7-v2242': _Limits(72, 18, 32, 32, 24, 48, 16, 3, 12, 12, 12,
                              led_brightness=127, video_links=3),
    'quantum8-v2242': _Limits(72, 18, 32, 32, 24, 48, 32, 4, 12, 12, 12,
                              encoder_macros_unassigned=True, talkback_at_input_end=True,
                              video_links=12),
}
CREATION_PROFILES = tuple(_LIMITS)


@dataclass(frozen=True)
class _Widths:
    mono: int
    stereo: int

    @property
    def count(self):
        return self.mono + self.stereo

    @property
    def legs(self):
        return self.mono + 2 * self.stereo

    def modes(self):
        return (1,) * self.mono + (2,) * self.stereo


@dataclass(frozen=True)
class _Channel:
    channel_id: int
    logical_number: int
    name: bytes
    stereo_mode: int = 1
    engine_channel: int = 0
    companion_id: int = 0
    group_aux_send_enabled: bool = False


@dataclass(frozen=True)
class BuiltStructure:
    console_record: bytes
    topology: C9Console
    current_mix: ConsoleSnapshot
    master_channel_id: int

    @property
    def c9(self):
        return self.topology.to_bytes()

    @property
    def d3(self):
        return self.current_mix.to_bytes()


# tGroup.Store writes these 108 bytes from object+0x30. The two named module
# scope fields at offsets 33 and 37 are initialized by tGroup.Create.
_GROUP_LABELS_AND_SCOPES = {
    0x01: (b'Input Channels', 0x7C4873BE),
    0x05: (b'Aux Outputs', 0x7C405BB0),
    0x06: (b'Group Outputs', 0x7C485BBC),
    0x07: (b'Busses', 0x7C485BBC),
    0x08: (b'Monitoring', 0),
    0x09: (b'Talkback Outputs', 0),
    0x0A: (b'Control Groups', 0x00010000),
    0x0B: (b'Solo', 0),
    0x0C: (b'Talkback Input', 0),
    0x0E: (b'FX', 0x00200000),
    0x0F: (b'Matrix Inputs', 0x42),
    0x10: (b'Matrix Outputs', 0x7C405BB0),
    0x11: (b'Graphic EQ', 0x8000),
    0x13: (b'Video Link', 0),
    0x14: (b'Nodal Processing', 0x30),
    0x18: (b'Spice Rack', 0x80000000),
    0x1B: (b'INTERNAL', 0),
    0x1C: (b'Local I/O', 0),
    0x4D: (b'INTERNAL', 0x40000),
    0x4E: (b'Local I/O', 0x40000),
}


def _bounded_count(value, name, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise StructureError(f'{name} must be an integer from 0 through {maximum}')
    return value


def _widths(value, name, maximum):
    if not isinstance(value, dict) or value.keys() - {'mono', 'stereo'}:
        raise StructureError(f'{name} must contain mono and/or stereo counts')
    mono = _bounded_count(value.get('mono', 0), name + '.mono', maximum)
    stereo = _bounded_count(value.get('stereo', 0), name + '.stereo', maximum // 2)
    result = _Widths(mono, stereo)
    if result.legs > maximum:
        raise StructureError(f'{name} exceeds its {maximum}-leg construction bound')
    return result


def _console_record(limits, inputs, auxes, groups, matrix_inputs, matrix_outputs,
                    control_groups, graphic_eqs):
    raw = bytearray(123)
    raw[0] = 8  # Factory console stereo mode, separate from channel stereo mode.
    for offset, counts in ((0x02, inputs), (0x12, auxes), (0x22, groups)):
        struct.pack_into('<HH', raw, offset, counts.mono, counts.stereo)
    raw[0x34] = 9  # MiscOptions
    raw[0x38] = groups.mono  # Index of the first stereo Group.
    raw[0x3A] = 1  # TimecodeSource
    raw[0x3B] = 2  # FrameRateIndex for 25 fps
    raw[0x3C] = 28  # OffLineFilters
    struct.pack_into('<Hf', raw, 0x3F, 300, -18.0)  # AutoNullPeriod, OscSurroundIDLevel
    raw[0x45] = 54  # InputAssignment
    raw[0x48] = 16  # ExtraOptions
    raw[0x49:0x4D] = bytes((255, limits.bridge_brightness, 255, limits.led_brightness))
    raw[0x4E] = 1  # TouchKeyboard
    raw[0x4F] = 5  # MeterOversHold
    struct.pack_into('<ffff', raw, 0x53, 48000.0, 25.0, 1000.0, -18.0)
    raw[0x63] = 1  # BussingMode
    raw[0x64] = 5  # RTZRewindTime
    struct.pack_into('<HHHH', raw, 0x66, 0x0344, 10, 600, 500)
    raw[0x6F] = matrix_outputs
    raw[0x70] = control_groups
    raw[0x72] = matrix_inputs
    raw[0x73] = graphic_eqs
    raw[0x76] = 1  # FaderDetents
    return bytes(raw)


def _group_metadata(group_id):
    label, module_scope = _GROUP_LABELS_AND_SCOPES[group_id]
    raw = bytearray(108)
    raw[0] = group_id
    raw[1] = len(label)
    raw[2:2 + len(label)] = label
    struct.pack_into('<II', raw, 0x21, module_scope, module_scope)
    raw[0x29:0x69] = b'\xff' * 64  # Global controller recall and update scopes.
    raw[0x6B] = 4  # RepeatsPerBar
    return bytes(raw)


def _channel_metadata(channel, limits):
    raw = bytearray(121)
    struct.pack_into('<HH', raw, 0, channel.channel_id, channel.logical_number)
    raw[4] = len(channel.name)
    raw[5:5 + len(channel.name)] = channel.name
    raw[0x25] = channel.stereo_mode
    struct.pack_into('<iH', raw, 0x26, -1,
                     0x0130 if channel.channel_id >> 8 in (0x01, 0x0C) else 0x0E40)
    struct.pack_into('<H', raw, 0x32, channel.companion_id)
    struct.pack_into('<iiiihh', raw, 0x40, -1, -1, -1, -1, channel.engine_channel, -1)
    if limits.encoder_macros_unassigned:
        raw[0x58:0x68] = b'\xff' * 16
    raw[0x78] = int(channel.group_aux_send_enabled)
    return bytes(raw)


def _plain_channels(group_id, names, *, stereo_mode=1, engine_start=0, engine_step=1):
    return [_Channel(group_id << 8 | index, index + 1, name, stereo_mode,
                     engine_start + index * engine_step)
            for index, name in enumerate(names)]


def _numbered(prefix, count):
    return [prefix + str(index + 1).encode('ascii') for index in range(count)]


def _local_socket_names(limits):
    if limits.video_links:
        # Q7/Q8 have a four analogue/four AES pattern per eight local sockets.
        def interleaved(analog, aes):
            return [(analog if index % 8 < 4 else aes) + str(index + 1).encode('ascii')
                    for index in range(24)]
        mic = b'0:Mic ' if limits.video_links == 12 else b'0:Mic/Lin '
        return interleaved(mic, b'0:AES in '), interleaved(b'0:Line ', b'0:AES out ')
    return (_numbered(b'0:Mic ', limits.analog_inputs) + _numbered(b'0:AES in ', limits.aes_inputs),
            _numbered(b'0:Line ', 8) + _numbered(b'0:AES out ', limits.aes_outputs))


def _input_channels(counts):
    engine = 0
    result = []
    for index, width in enumerate(counts.modes()):
        result.append(_Channel(0x100 | index, index + 1, f'Ch {index + 1}'.encode('ascii'),
                               width, engine))
        engine += width
    return result


def _output_busses(auxes, groups):
    aux_outputs, group_outputs, right_busses = [], [], []
    next_right = 8
    for group_id, counts, outputs, engine in (
        (0x05, auxes, aux_outputs, groups.legs), (0x06, groups, group_outputs, 0),
    ):
        for index, width in enumerate(counts.modes()):
            channel_id = group_id << 8 | index
            is_master = group_id == 0x06 and index == groups.mono
            prefix = 'Aux' if group_id == 0x05 else 'Grp'
            name = b'Master' if is_master else f'{prefix} {index + 1}'.encode('ascii')
            companion = 0
            if width == 2:
                companion = 0x0700 if is_master else 0x0700 | next_right
                right_busses.append(_Channel(companion, len(right_busses) + 1, name + b' R',
                                             1, engine + 1, channel_id))
                if not is_master:
                    next_right += 1
            outputs.append(_Channel(channel_id, index + 1, name, width, engine, companion,
                                    group_id == 0x06 and not is_master))
            engine += width
    return aux_outputs, group_outputs, right_busses


def _socket_lists(groups, limits):
    # Keys are group ID in the high byte and the first channel's low byte.
    available = {kind: len(channels) for kind, channels in groups}
    processors = ((b'Graphic EQs', 0x11), (b'Effects', 0x0E), (b'Spice Rack', 0x18))
    sources = ((b'Channels', 0x01), (b'Groups', 0x06), (b'Auxes', 0x05),
               (b'Matrix', 0x10), (b'Solos', 0x0B))

    def build(internal, local, signal_groups, bank_names):
        entries = [(b'Misc', internal << 8)]
        entries.extend((name, group_id << 8) for name, group_id in signal_groups
                       if available[group_id] or group_id == 0x0E)
        entries.extend((name, local << 8 | (index * 8)) for index, name in enumerate(bank_names))
        return SocketGroups(b''.join(name + b'\r\n' for name, _ in entries),
                            tuple(key for _, key in entries))

    numbered_banks = (b'Local 1-8', b'Local 9-16', b'Local 17-24')
    if limits.video_links:
        input_names = output_names = numbered_banks
    else:
        input_names = numbered_banks[:limits.analog_inputs // 8] + (b'Local aes',)
        output_names = (b'Local line', b'Local aes')
    return (build(0x1B, 0x1C, processors + sources, input_names),
            build(0x4D, 0x4E, processors, output_names))


def _module(module_type, channel, records):
    header = bytearray(58)
    struct.pack_into('<I', header, 0, module_type)
    header[4] = len(channel.name)
    header[5:5 + len(channel.name)] = channel.name
    struct.pack_into('<I', header, 44, len(records))
    header[53] = 1  # IgnoreScope on the inline current-state module.
    controls = b''.join(bytes((parameter, module_type, band, 0)) + value
                        for parameter, band, value in records)
    return ModuleSnapshot(_Block(bytes(header)), controls, -1, -1, 0)


# Native float32 values for the reviewed mono Input processing records.
# Each tuple starts at band zero; None denotes an absent parameter/band record.
# Six separate v2242 synthetic factory-derived sessions agree on these values.
_EQ_FREQUENCIES = (6300, 1600, 300, 100)
_EQ_BAND_ENABLE = (1,) * 4
_EQ_Q = (0.71,) * 4
_EQ_GAIN = (0,) * 4
_FILTER_DEFAULTS = {0x03: (0, 0), 0x05: (25000, 16)}
_INPUT_PROCESSING_DEFAULTS = {
    4: {0x02: (0,), 0x03: _EQ_BAND_ENABLE, 0x04: _EQ_BAND_ENABLE, 0x05: _EQ_FREQUENCIES,
        0x06: _EQ_Q, 0x07: _EQ_GAIN, 0x0B: (0,) * 4, 0x0E: (-36,) * 4, 0x10: (2,) * 4,
        0x11: (0.01, 0.015, 0.025, 0.05), 0x12: (0.3, 0.35, 0.4, 0.6),
        0x7C: (0,) * 4, 0x7D: (0,) * 4},
    5: {0x02: (0, None, None, 0), 0x03: (1, 1, 1), 0x05: (130, 1000, 20000, 20),
        0x09: (0,) * 4, 0x0A: (None, None, None, 40), 0x0B: (None, None, None, 0),
        0x0E: (-20,) * 4, 0x0F: (0,) * 4, 0x10: (3, 2, 2, 3),
        0x11: (0.01, 0.005, 0.025, 0.002), 0x12: (0.5, 0.2, 0.4, 0.14),
        0x13: (None, None, None, 0.14), 0x14: (0,), 0x37: (0,), 0x3A: (0,),
        0x3B: (0,) * 4},
    7: _FILTER_DEFAULTS,
    9: {0x07: (0,), 0x08: (0,), 0x17: (0,)},
    26: {0x02: (0,), 0x05: (0,), 0x1A: (11,), 0x1B: (0.5,), 0x37: (0,), 0x38: (0,),
         0x39: (0,), 0x3A: (0,), 0x3B: (1,), 0x3C: (-6,), 0x45: (0,)},
    27: _FILTER_DEFAULTS,
    28: {0x02: (0,), 0x04: _EQ_BAND_ENABLE, 0x05: _EQ_FREQUENCIES, 0x06: _EQ_Q, 0x07: _EQ_GAIN},
    29: {0x02: (0,), 0x03: (0,), 0x05: (20000, 20), 0x09: (0,), 0x0B: (0,), 0x0E: (-20,),
         0x0F: (1,), 0x10: (3,), 0x11: (0.01,), 0x12: (0.25,), 0x37: (0,), 0x3F: (0,),
         0x41: (-20,), 0x46: (0,), 0x47: (1,), 0x48: (0,), 0x49: (0,), 0x4A: (1,),
         0x4C: (3,), 0x4D: (0,)},
    30: {0x02: (0,), 0x03: (0,), 0x05: (20000, 20), 0x0A: (40,), 0x0B: (0,), 0x0E: (-20,),
         0x11: (0.002,), 0x12: (0.14,), 0x13: (0.14,), 0x37: (0,), 0x4C: (1,)},
}
_CURRENT_MODULE_ORDER = (1, 9, 7, 8, 4, 5, 12, 2, 3, 14, 17, 18, 26, 27, 28, 29, 30)


def _current_channel(channel, aux_outputs, group_outputs, master_channel_id):
    metadata = bytearray(39)
    struct.pack_into('<H', metadata, 0, channel.channel_id)
    metadata[2] = len(channel.name)
    metadata[3:3 + len(channel.name)] = channel.name
    struct.pack_into('<i', metadata, 34, -1)
    metadata[38] = channel.stereo_mode
    zero = bytes(4)
    one = struct.pack('<f', 1.0)
    kind = channel.channel_id >> 8
    controls = {}
    if kind in (0x01, 0x0C):
        controls[1] = [(0, 0, zero), (0, 1, zero), (1, 0, zero)]
        controls[5] = [(0x16, 0, zero)]
    if kind == 0x01:
        controls[8] = [(0, 0, zero), (0, 1, zero)]
        controls[30] = [(0x16, 0, zero)]
        if aux_outputs:
            level_off = struct.pack('<f', -150.0)
            controls[2] = [(parameter, output.channel_id & 255, value)
                           for output in aux_outputs
                           for parameter, value in ((1, level_off), (2, zero))]
        if channel.stereo_mode == 1:
            for module_type, parameters in _INPUT_PROCESSING_DEFAULTS.items():
                controls.setdefault(module_type, []).extend(
                    (parameter, band, struct.pack('<f', value))
                    for parameter, values in parameters.items()
                    for band, value in enumerate(values) if value is not None)
    if kind in (0x01, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x10):
        controls[12] = [(0x21, 0, struct.pack('<f', -150.0)), (0x22, 0, zero)]
        controls[14] = [(0xFE, 0, zero)]
    if kind in (0x01, 0x06):
        active = one if kind == 0x01 else zero
        routes = [(9, output.channel_id & 255,
                   active if output.channel_id == master_channel_id else zero)
                  for output in group_outputs]
        controls[3] = routes
    if kind in (0x1B, 0x1C):
        controls[17] = [(0xFE, 0, zero)]
    if kind in (0x4D, 0x4E):
        controls[18] = [(0, 0, zero)]
    modules = [_module(module_type, channel, controls[module_type])
               for module_type in _CURRENT_MODULE_ORDER if module_type in controls]
    return ChannelSnapshot(_Block(b'\x01'), modules, _Block(bytes(metadata)))


def _current_mix(channels, aux_outputs, group_outputs, master_channel_id):
    scope = bytearray(b'\xff' * 992)
    scope[288:320] = bytes(32)
    layout = bytearray(807)
    struct.pack_into('<H', layout, 796, master_channel_id)
    timing = bytearray(81)
    timing[8] = 18
    timing[34:37] = b'\xff' * 3
    timing[38:40] = bytes((1, 9))
    timing[41:47] = b'\x05Arial'
    timing[77:] = b'\xff' * 4
    return ConsoleSnapshot(
        [_current_channel(channel, aux_outputs, group_outputs, master_channel_id)
         for channel in channels],
        _Block(b''), bytes(8), _Block(bytes(scope)), _Block(bytes(2)), b'',
        _Block(bytes(8)), _Block(bytes(layout)), _Block(bytes(timing)), _Block(b''), _Block(b''),
    )


def build_structure(profile_id, configuration):
    """Build the header record, C9 topology and D3 state from a JSON configuration.

    Width budgets cover mono/stereo Inputs, Auxes and Groups. The first stereo
    Group is the Master. Physical banks describe the local and internal sockets
    for the selected model. The D3 state stores the small, reviewed set of
    initial controls and routes used by the independent authoring API.
    """
    if not isinstance(profile_id, str) or profile_id not in _LIMITS:
        raise StructureError('Creation requires a reviewed Quantum v2242 profile')
    limits = _LIMITS[profile_id]
    fields = {'inputs', 'auxes', 'groups', 'matrix_inputs', 'matrix_outputs',
              'control_groups', 'graphic_eqs'}
    if not isinstance(configuration, dict) or configuration.keys() - fields:
        raise StructureError('Unknown channel-structure configuration field')
    inputs = _widths(configuration.get('inputs', {'mono': limits.inputs}), 'inputs', limits.inputs)
    default_busses = {'mono': limits.buss_legs // 3, 'stereo': limits.buss_legs // 3}
    auxes = _widths(configuration.get('auxes', default_busses), 'auxes', limits.buss_legs)
    groups = _widths(configuration.get('groups', default_busses), 'groups', limits.buss_legs)
    if groups.stereo < 1:
        raise StructureError('At least one stereo Group is required for the Master')
    matrix_inputs = _bounded_count(configuration.get('matrix_inputs', limits.matrix_inputs),
                                   'matrix_inputs', limits.matrix_inputs)
    matrix_outputs = _bounded_count(configuration.get('matrix_outputs', limits.matrix_outputs),
                                    'matrix_outputs', limits.matrix_outputs)
    control_groups = _bounded_count(configuration.get('control_groups', limits.control_groups),
                                    'control_groups', limits.control_groups)
    graphic_eqs = _bounded_count(configuration.get('graphic_eqs', limits.graphic_eqs),
                                 'graphic_eqs', limits.graphic_eqs)
    master_channel_id = 0x0600 | groups.mono
    aux_outputs, group_outputs, right_busses = _output_busses(auxes, groups)
    local_inputs, local_outputs = _local_socket_names(limits)
    spice_channels = [_Channel(0x1800 | index, index + 1, b'Chilli 6',
                               engine_channel=index if index < 16 else index + 48)
                      for index in range(limits.spice_rack)]
    topology_groups = [
        (0x1B, _plain_channels(0x1B, (b'unused', b'unused', b'oscillator', b'white noise',
                                      b'pink noise', b'talk mic', b'11Hz sawtooth', b'oscillator 2'))),
        (0x4D, _plain_channels(0x4D, (b'unused', b'unused', b'headphone L', b'headphone R'),
                               engine_start=24)),
        (0x1C, _plain_channels(0x1C, local_inputs)),
        (0x4E, _plain_channels(0x4E, local_outputs)),
        (0x11, _plain_channels(0x11, _numbered(b'Graphic ', graphic_eqs))),
        (0x0E, []),
        (0x18, spice_channels),
        (0x0B, _plain_channels(0x0B, (b'Solo 1', b'Solo 2'), stereo_mode=2,
                               engine_start=groups.legs + auxes.legs, engine_step=8)),
        (0x08, _plain_channels(0x08, (b'Monitoring',), stereo_mode=2)),
        (0x0A, _plain_channels(0x0A, _numbered(b'CG ', control_groups))),
        (0x10, _plain_channels(0x10, _numbered(b'Matrix ', matrix_outputs))),
        (0x0F, _plain_channels(0x0F, _numbered(b'mtx In ', matrix_inputs))),
        (0x07, right_busses),
        (0x05, aux_outputs),
        (0x06, group_outputs),
        (0x14, []),
        (0x01, _input_channels(inputs)),
        (0x0C, _plain_channels(0x0C, (b'TB In',),
                               engine_start=inputs.legs if limits.talkback_at_input_end else 254)),
        (0x09, _plain_channels(0x09, (b'',) * limits.talkback_outputs)),
    ]
    if limits.video_links:
        video_names = (b'cam', b'ext 1', b'ext 2') + (b'',) * (limits.video_links - 3)
        topology_groups.append((0x13, _plain_channels(0x13, video_names)))
    input_banks, output_banks = _socket_lists(topology_groups, limits)
    topology = C9Console(2242, input_banks, output_banks, [
        C9Group(0xD1 if kind == 0x14 else 0xCA, _group_metadata(kind),
                [C9Channel(_channel_metadata(channel, limits)) for channel in channels])
        for kind, channels in topology_groups
    ])
    channels = [channel for _, group in topology_groups for channel in group]
    channels.sort(key=lambda channel: channel.channel_id)
    record = _console_record(limits, inputs, auxes, groups, matrix_inputs,
                             matrix_outputs, control_groups, graphic_eqs)
    return BuiltStructure(record, topology,
                          _current_mix(channels, aux_outputs, group_outputs, master_channel_id),
                          master_channel_id)

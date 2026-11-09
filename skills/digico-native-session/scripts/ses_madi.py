"""Add the reviewed Q5 V2242 first MADI port to a new Session."""
from __future__ import annotations

import struct

from ses_routing import C9Channel, C9Group, SocketGroups, _PORT_WIDTH, build_local_ports
from ses_structure import (StructureError, _LIMITS, _channel_metadata, _current_channel,
                           _group_metadata, _module, _numbered, _plain_channels)


_PROFILE = 'quantum5-v2242'
_INPUT_GROUP = 0x1D
_OUTPUT_GROUP = 0x4F
_INPUT_CARDS = (0x16,) * 7
_OUTPUT_CARDS = (1, 1, 1, 3, 3, 8, 8)


def _port_row():
    row = bytearray(_PORT_WIDTH)
    # Q5 V2242 first MADI port header. The native slot includes eight card
    # positions per direction, with the final position unfitted.
    row[:5] = bytes((2, 1, 57, 2, 64))
    for start, cards in ((0x08, _INPUT_CARDS), (0x38, _OUTPUT_CARDS)):
        for offset, card in enumerate((*cards, 0)):
            struct.pack_into('<BxI', row, start + offset * 6, card, 0x10000)
    row[0x90:0xB0] = bytes([255]) * 32
    row[0xD0] = 2  # MADI port type. The first port number is zero.
    return bytes(row)


def _socket_group(group_id, channels):
    metadata = bytearray(_group_metadata(group_id - 1))
    label = b'Rack 1'
    metadata[:0x21] = bytes((group_id, len(label))) + label.ljust(31, b'\0')
    return C9Group(0xCA, bytes(metadata), [
        C9Channel(_channel_metadata(channel, _LIMITS[_PROFILE])) for channel in channels
    ])


def _bank_labels(direction):
    banks = (('Mic', 7),) if direction == 'input' else (('Line', 3), ('AES', 2), ('Aviom', 2))
    return [f'{prefix} {index * 8 + 1}-{index * 8 + 8}'.encode('ascii')
            for prefix, count in banks for index in range(count)]


def _add_banks(existing, group_id, direction):
    names = [*_bank_labels(direction), b'']
    return SocketGroups(existing.text + b''.join(name + b'\r\n' for name in names),
                        existing.keys + tuple(group_id << 8 | (i * 8) for i in range(8)))


def _mix_socket(channel, direction):
    # Native inline tInputSocket/tOutputSocket defaults. Route selectors are
    # initially cleared so all destinations are available to the new Session.
    if direction == 'input':
        kind = 0x11
        values = [(0x86, 0), (0x81, 1), (0x83, 0), (0x84, 1), (0x8B, 0),
                  (0x80, 0), (0x89, 0), (0xFF, 0), (0xFE, 0), (0xC8, 0), (0xC9, 0)]
    else:
        kind = 0x12
        values = [(0x90, 0), (0x93, 0), (0x8A, 0), (0x9C, 0), (0, 0),
                  (0xE2, 0), (0xFF, 0)]
    result = _current_channel(channel, (), (), 0)
    result.modules = [_module(kind, channel, [
        (parameter, 0, struct.pack('<f', value)) for parameter, value in values
    ])]
    return result


def add_q5_madi_port1(structure, local_ports):
    """Add the stock Rack 1 sockets to a freshly constructed Q5 V2242 topology."""
    topology = structure.topology
    if (local_ports != build_local_ports(_PROFILE)
            or [group.group_id for group in topology.groups[:4]] != [0x1B, 0x4D, 0x1C, 0x4E]
            or any(group.group_id in (_INPUT_GROUP, _OUTPUT_GROUP) for group in topology.groups)):
        raise StructureError('MADI port 1 requires a new Q5 topology with local-only ports')

    inputs = _plain_channels(_INPUT_GROUP, _numbered(b'1:Mic ', 56))
    outputs = _plain_channels(_OUTPUT_GROUP, (
        _numbered(b'1:Line out ', 24) + _numbered(b'1:AES out ', 16)
        + _numbered(b'1:Aviom ', 16)
    ))
    topology.groups[4:4] = [_socket_group(_INPUT_GROUP, inputs), _socket_group(_OUTPUT_GROUP, outputs)]
    topology.input_groups = _add_banks(topology.input_groups, _INPUT_GROUP, 'input')
    topology.output_groups = _add_banks(topology.output_groups, _OUTPUT_GROUP, 'output')
    channels = structure.current_mix.channels
    channels.extend(_mix_socket(channel, 'input') for channel in inputs)
    channels.extend(_mix_socket(channel, 'output') for channel in outputs)
    channels.sort(key=lambda channel: channel.channel_id)
    start = 4 + _PORT_WIDTH
    return local_ports[:start] + _port_row() + local_ports[start + _PORT_WIDTH:]

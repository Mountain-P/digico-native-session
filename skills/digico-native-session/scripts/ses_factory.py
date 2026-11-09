"""Construct new vO Sessions from a topology request and typed defaults."""
from __future__ import annotations

import struct

from ses_file import ENCODINGS, SesFile, SesFormatError, _fields, _sha256
from ses_routing import build_local_ports
from ses_snapshot import SnapshotSection
from ses_structure import CREATION_PROFILES, build_structure


# tChannelBankRec stores 12 channel IDs. The layout assigns bank keys to
# physical strip/layer slots observed in each saved vO factory layout. Banks
# without an observed slot still have an F3 list entry.
_SURFACE_SLOTS = {
    'quantum1-v2242-4': ((2, (0, 1, 2, 3, 6, 7, 8, 9)),),
    'quantum2-v2242-4': ((2, (0, 1, 6, 7)), (3, (0, 1, 6, 7, 8))),
    'quantum3-v2242': ((2, (0, 1)), (3, (0, 1)), (4, (0, 1, 2, 3, 6, 7, 8))),
    'quantum5-v2242': ((2, (0, 1, 2, 3, 4, 6, 7, 8, 9)),
                       (3, (0, 1, 2, 3, 6, 7, 8, 9)), (4, (0, 1, 2, 3, 6, 7, 8, 9))),
    'quantum7-v2242': ((0, (0, 1, 2, 3)), (1, (0, 1)),
                       (2, (0, 1, 2, 6, 7, 8, 9)), (3, (0, 1, 2, 6, 7, 8, 9))),
    'quantum8-v2242': ((1, (0, 1, 2, 3)), (2, (0, 1, 2)), (3, (0, 1, 2)),
                       (4, tuple(range(8)))),
}
_BANK_GROUPS = ((1, 'Input', 'Input Channels'), (5, 'Aux', 'Aux Outputs'),
                (6, 'Group', 'Group Outputs'), (16, 'Matrix', 'Matrix Outputs'),
                (10, 'CG', 'Control Groups'), (12, 'TB', 'Talkback Input'))


def _bank_string(record, offset, text):
    raw = text.encode('ascii')
    if len(raw) > 15:
        raise SesFormatError('Generated bank label exceeds the 15-byte field')
    record[offset:offset + len(raw) + 1] = bytes((len(raw),)) + raw


def _surface(profile_id, channels, master_id):
    """Build tChannelBanks and tConsoleLayoutRec using channel IDs and keys."""
    banks = []
    for channel_type, label, description in _BANK_GROUPS:
        ids = [channel.channel_id for channel in channels if channel.channel_id >> 8 == channel_type]
        for start in range(0, len(ids), 12):
            chunk = ids[start:start + 12]
            bank = bytearray(361)
            struct.pack_into('<HH', bank, 0, len(bank), len(banks))
            _bank_string(bank, 4, f'{label} {start+1}-{start+len(chunk)}')
            _bank_string(bank, 0x137, description)
            struct.pack_into('<12H', bank, 0x23, *chunk, *([0] * (12 - len(chunk))))
            banks.append(bytes(bank))

    layout = bytearray((bytes([255]) * 84 + bytes([1]) * 7 + bytes(8)) * 8 + bytes(15))
    slots = [strip * 99 + 2 * slot for strip, positions in _SURFACE_SLOTS[profile_id]
             for slot in positions]
    for key, offset in enumerate(slots[:len(banks)]):
        struct.pack_into('<H', layout, offset, key)
    if profile_id in ('quantum7-v2242', 'quantum8-v2242'):
        ids = {channel.channel_id for channel in channels}
        struct.pack_into('<4H', layout, 792, master_id, master_id,
                         0x1000 if 0x1000 in ids else 0, 0x1001 if 0x1001 in ids else 0)
    else:
        struct.pack_into('<H', layout, 796, master_id)
        if profile_id == 'quantum3-v2242':
            struct.pack_into('<H', layout, 798, 0x0b00)
    bank_section = struct.pack('<Hi', 0xf3, len(banks)) + b''.join(banks)
    return bank_section, bytes(layout)


def create_session(request):
    """Produce a new Session from counts and operations, with no source file."""
    _fields(request, ('schema_version', 'build_profile', 'encoding', 'structure'),
            ('operations', 'madi_port1'), label='new Session request')
    if type(request['schema_version']) is not int or request['schema_version'] != 1:
        raise SesFormatError('schema_version must be integer 1')
    profile_id = request['build_profile']
    if profile_id not in CREATION_PROFILES:
        raise SesFormatError('New Session construction requires a reviewed v2242 profile')
    madi_port1 = request.get('madi_port1', False)
    if type(madi_port1) is not bool or (madi_port1 and profile_id != 'quantum5-v2242'):
        raise SesFormatError('madi_port1 must be Boolean and is supported for Quantum5 V2242 only')
    encoding = request['encoding']
    if encoding not in ENCODINGS:
        raise SesFormatError('Unsupported text encoding')
    operations = request.get('operations', [])
    if not isinstance(operations, list) or len(operations) > 1000:
        raise SesFormatError('operations must be an array with at most1000 entries')

    structure = build_structure(profile_id, request['structure'])
    ports = build_local_ports(profile_id)
    if madi_port1:
        from ses_madi import add_q5_madi_port1
        ports = add_q5_madi_port1(structure, ports)
    banks, layout = _surface(profile_id, structure.current_mix.channels,
                             structure.master_channel_id)
    structure.current_mix.layout.data = layout
    # DB and FC use the native empty list representation. F6 configures the
    # on-board port. The loader reads these before C9 and then seeks by tag.
    sections = [
        (0, bytes(4)),
        (0xdb, struct.pack('<Hi', 0xdb, 0)),
        (0xf6, ports),
        (0xfc, struct.pack('<Hi', 0xfc, 0)),
        (0xc9, structure.c9),
        (0xd2, SnapshotSection.empty(2242).to_bytes()),
        (0x73, struct.pack('<Hii', 0x73, 0, -1)),
        (0xd3, structure.d3),
        (0xf3, banks),
        (0xd4, struct.pack('<H', len(layout)) + layout),
    ]
    session = SesFile.assemble(profile_id, structure.console_record, sections)
    session._routing(session._console())
    if operations:
        session = session.edit({'schema_version': 1, 'encoding': encoding,
                                'expected_sha256': _sha256(session.to_bytes()),
                                'operations': operations})
        topology = session._topology()
        names = {}
        for channel in session._console().snapshot.channels:
            raw = channel.metadata.effective(39)
            names[channel.channel_id] = raw[3:3 + raw[2]]
        for group in topology.groups:
            for channel in group.channels:
                name = names[channel.channel_id]
                if name != channel.name_bytes:
                    channel.set_name(name)
        session = SesFile.assemble(profile_id, structure.console_record, [
            (tag, topology.to_bytes() if tag == 0xc9 else data)
            for tag, data in zip(session.section_tags, session.sections)
        ])
    return session

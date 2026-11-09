"""Compare saved input, insert, Aux and Group controls without simulating recall."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import struct

from ses_console import CurrentConsole
from ses_routing import C9Console
from ses_snapshot import SnapshotFormatError, SnapshotSection


_CHANNEL_TYPES = {1: 'input', 5: 'aux', 6: 'group', 0x0f: 'matrix_input', 0x10: 'matrix_output'}
_MODULES = {1: (1, 2, 3, 8), 5: (8,), 6: (3, 8), 0x0f: (1,), 0x10: (8,)}
_SECTIONS = {8: 'insert', 1: 'main_alt', 3: 'group_send', 2: 'aux_send'}
_FIELDS = {1: {0: ('route', 'route'), 0x0e: ('selection', 'input_selection')},
           2: {1: ('level_db', 'float'), 2: ('enabled', 'boolean'), 4: ('tap', 'aux_tap')},
           3: {9: ('enabled', 'boolean')},
           8: {0: ('return', 'route'), 2: ('enabled', 'boolean'),
               0xe1: ('tap', 'insert_tap'), 0xfe: ('send', 'route')}}
# The controller-bound Q5 V2126 and V2242 form captions agree on these enum orders.
_ENUMS = {
    'input_selection': ('main', 'alt'),
    'aux_tap': ('post_fader', 'pre_fader', 'pre_mute', 'mid_eq_dynamics', 'pre_eq_dynamics', 'pre_processing'),
    'insert_tap': ('post_fader', 'pre_fader', 'pre_fader', 'pre_dynamics', 'pre_eq', 'pre_processing'),
}
_MAX_RECORDS_SCANNED = 4_000_000
_MAX_CURRENT_SENDS = 8_192
_MAX_CUE_DETAILS = 32
_MAX_TOTAL_CUE_DETAILS = 1_024


@dataclass(frozen=True)
class _SavedControl:
    raw: bytes
    flags: int
    parent: int


def _saved_modules(channel, pool):
    if channel.own_modules.effective(1)[0]:
        yield from channel.modules
    else:
        for index in channel.modules:
            if type(index) is not int or not -1 <= index < len(pool):
                raise SnapshotFormatError('Signal analysis found an invalid shared module reference')
            if index >= 0:
                yield pool[index]


def _read_state(snapshot, pool, channels, selected, work_left):
    state = {}
    work_left -= len(snapshot.channels)
    if work_left < 0:
        raise SnapshotFormatError('Signal analysis exceeds its record budget; filter by channel_id')
    for channel in snapshot.channels:
        channel_id = channel.channel_id
        modules = _MODULES.get(channel_id >> 8)
        if modules is None or (selected is not None and channel_id != selected):
            continue
        if channel_id in state:
            raise SnapshotFormatError('Signal analysis found a duplicate channel ID')
        records = state[channel_id] = {}
        work_left -= len(channel.modules)
        if work_left < 0:
            raise SnapshotFormatError('Signal analysis exceeds its record budget; filter by channel_id')
        for module in _saved_modules(channel, pool):
            # Shared modules are counted once per use to bound expanded work.
            work_left -= len(module.controls) // 8
            if work_left < 0:
                raise SnapshotFormatError('Signal analysis exceeds its record budget; filter by channel_id')
            module_id = struct.unpack_from('<i', module.header.effective(58))[0]
            if module_id not in modules:
                continue
            for parameter, saved_module, band, flags, raw in struct.iter_unpack('<BBBB4s', module.controls):
                if parameter not in _FIELDS[module_id]:
                    continue
                if module_id != saved_module:
                    raise SnapshotFormatError('Signal controller disagrees with its enclosing module')
                if module_id in (1, 8) and (band > 1 or (module_id == 1 and parameter == 0x0e and band)):
                    continue
                if module_id in (2, 3) and ((0x0500 if module_id == 2 else 0x0600) | band) not in channels:
                    continue
                key = module_id, band, parameter
                if key in records:
                    raise SnapshotFormatError('Signal analysis found a duplicate saved control')
                records[key] = _SavedControl(raw, flags, module.parent_module_index)
    return state, work_left


def _value(control, kind, names):
    if kind == 'route':
        packed = struct.unpack('<I', control.raw)[0]
        channel_id = packed >> 16 if packed else None
        value = {'raw_uint32': packed, 'channel_id': channel_id, 'name': names.get(channel_id)}
    else:
        number = struct.unpack('<f', control.raw)[0]
        value = number if math.isfinite(number) else None
        if kind == 'boolean':
            value = bool(number) if number in (0, 1) else None
        elif kind in _ENUMS:
            labels = _ENUMS[kind]
            value = labels[int(number)] if 0 <= number < len(labels) and number.is_integer() else None
    return {'value': value, 'value_raw_hex': control.raw.hex(), 'flags': control.flags,
            'parent_module_index': control.parent}


def _field(key):
    module, band, parameter = key
    name, kind = _FIELDS[module][parameter]
    if module == 1 and parameter == 0:
        name = 'alt_route' if band else 'main_route'
    return name, kind


def _current_channels(state, channels, names):
    rows = []
    send_count = 0
    for channel_id, records in sorted(state.items()):
        if not records:
            continue
        row = {'channel_id': channel_id, 'name': names.get(channel_id),
               'channel_type': _CHANNEL_TYPES[channel_id >> 8],
               'main_alt': {}, 'inserts': [], 'aux_sends': [], 'group_sends': []}
        inserts, auxes, groups = {}, {}, {}
        for key, control in sorted(records.items()):
            module, band, _ = key
            field, kind = _field(key)
            if module == 1:
                target = row['main_alt']
            elif module == 8:
                target = inserts.setdefault(band, {'slot': 'AB'[band]})
            else:
                on = records.get((module, band, 2 if module == 2 else 9))
                if on is None or struct.unpack('<f', on.raw)[0] != 1.0:
                    continue
                dest_id = (0x0500 if module == 2 else 0x0600) | band
                dests = auxes if module == 2 else groups
                if dest_id not in dests:
                    dests[dest_id] = {'destination_channel_id': dest_id, 'destination_name': names[dest_id],
                                     'destination_stereo_mode': channels[dest_id].stereo_mode}
                target = dests[dest_id]
            target[field] = _value(control, kind, names)
        row['inserts'] = list(inserts.values())
        row['aux_sends'] = list(auxes.values())
        row['group_sends'] = list(groups.values())
        send_count += len(row['aux_sends']) + len(row['group_sends'])
        if send_count > _MAX_CURRENT_SENDS:
            raise SnapshotFormatError('Signal analysis exceeds its current send limit; filter by channel_id')
        rows.append(row)
    return rows


def _describe_change(channel_id, key, before, after, names):
    module, band, _ = key
    field, kind = _field(key)
    change = {'channel_id': channel_id, 'name': names.get(channel_id),
              'section': _SECTIONS[module], 'field': field,
              'before': _value(before, kind, names), 'after': _value(after, kind, names)}
    if module == 8:
        change['slot'] = 'AB'[band]
    if module in (2, 3):
        destination = (0x0500 if module == 2 else 0x0600) | band
        change.update(destination_channel_id=destination, destination_name=names[destination])
    return change


def _compare(before, after, names, detail_limit):
    counts = Counter()
    detail_candidates = {section: [] for section in _SECTIONS.values()}
    before_only = after_only = compared = 0
    for channel_id in sorted(before.keys() | after.keys()):
        left, right = before.get(channel_id, {}), after.get(channel_id, {})
        common = left.keys() & right.keys()
        before_only += len(left) - len(common)
        after_only += len(right) - len(common)
        compared += len(common)
        for key in sorted(common):
            if left[key].raw == right[key].raw:
                continue
            section = _SECTIONS[key[0]]
            counts[section] += 1
            bucket = detail_candidates[section]
            if len(bucket) < detail_limit:
                bucket.append((channel_id, key, left[key], right[key]))
    # Prioritize routing and inserts over large sets of Aux level changes.
    details = []
    for bucket in detail_candidates.values():
        details.extend(_describe_change(*candidate, names) for candidate in bucket[:detail_limit - len(details)])
    return {'compared_field_count': compared, 'before_only_field_count': before_only,
            'after_only_field_count': after_only, 'change_count_by_section': dict(counts),
            'changes': details, 'omitted_change_details': sum(counts.values()) - len(details)}


def inspect_signal(snapshots, console, topology, encoding='cp950', *, channel_id=None):
    """Describe stored signal controls and changes on comparable saved records.

    Only explicit saved controls are compared. Shared references are resolved,
    while scope, inheritance, safes and physical patch validation are separate.
    A stereo input route names the stored base socket, not a synthesized pair.
    """
    if not (isinstance(snapshots, SnapshotSection) and isinstance(console, CurrentConsole)
            and isinstance(topology, C9Console) and snapshots.version == console.version == topology.version):
        raise SnapshotFormatError('Signal analysis requires matching Snapshot, console and topology versions')
    if encoding not in ('ascii', 'cp950', 'utf-8'):
        raise SnapshotFormatError('Unsupported signal analysis name encoding')
    channels = {channel.channel_id: channel for group in topology.groups for channel in group.channels}
    if channel_id is not None and (type(channel_id) is not int or channel_id not in channels
                                   or channel_id >> 8 not in _CHANNEL_TYPES):
        raise SnapshotFormatError('Signal analysis channel_id must identify an Input, Aux, Group or Matrix channel')
    names = {key: channel.name_bytes.decode(encoding, errors='replace') for key, channel in channels.items()}
    pool = snapshots.modules
    current, work_left = _read_state(console.snapshot, pool, channels, channel_id, _MAX_RECORDS_SCANNED)
    selected_index = struct.unpack_from('<i', snapshots.options.effective(237))[0]
    previous, current_vs_selected = None, None
    remaining_details = _MAX_TOTAL_CUE_DETAILS
    cue_deltas = []
    for index, cue in enumerate(snapshots.snapshots):
        state, work_left = _read_state(cue, pool, channels, channel_id, work_left)
        metadata = cue.inspect(encoding, include_channels=False)
        ref = {'index': index, 'number': metadata['number'], 'name': metadata['name']}
        if previous is not None:
            diff = _compare(previous, state, names, min(remaining_details, _MAX_CUE_DETAILS))
            remaining_details -= len(diff['changes'])
            cue_deltas.append({'cue': ref, 'previous_index': index - 1, **diff})
        if index == selected_index:
            current_vs_selected = {'cue': ref, 'cue_reference': 'd2_current_snapshot_index',
                                   'direction': 'stored_cue_to_current_console',
                                   **_compare(state, current, names, _MAX_CUE_DETAILS)}
        previous = state
    return {'version': snapshots.version, 'record_semantics': 'stored_signal_controls',
            'scope_inheritance_applied': False, 'effective_recall_verified': False,
            'physical_audio_verified': False, 'current_send_filter': 'enabled_only',
            'change_comparison': 'common_saved_field_value_bytes', 'channel_name_source': 'c9_topology',
            'current_channels': _current_channels(current, channels, names),
            'cue_deltas': cue_deltas, 'current_vs_selected_cue': current_vs_selected}

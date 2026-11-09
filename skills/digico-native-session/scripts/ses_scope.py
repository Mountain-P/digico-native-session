"""Explain saved Quantum5 Recall Scope and Safe gates per Cue and controller."""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
import struct

from ses_console import CurrentConsole
from ses_file import ENCODINGS, LEGACY_READ_PROFILE, SesFile, SesFormatError, _sha256
from ses_scope_masks import ScopeMasks
from ses_scope_safe import SavedSafeState


_Q5_FILE_PROFILES = (LEGACY_READ_PROFILE, 'quantum5-v2242')
_MAX_EXPANDED_RECORDS = 20_000_000
_MAX_DETAIL_ENTRIES = 1_000_000
_STATUS = ('allowed', 'scope_blocked', 'safe_blocked', 'unknown')
_MODULE_NAMES = {
    1: 'Input', 2: 'Aux Sends', 3: 'To Groups', 4: 'EQ', 5: 'Dynamics',
    6: 'Matrix Sends', 7: 'Filters', 8: 'Inserts', 9: 'Delay', 10: 'Channel Groups',
    11: 'Output Trim', 12: 'Fader/Mute', 13: 'Panner', 14: 'Outputs', 15: 'Graphic EQ',
    16: 'Control Group', 17: 'Input Socket', 18: 'Output Socket', 19: 'External',
    28: 'Mustard EQ', 29: 'Mustard Dynamics 1', 30: 'Mustard Dynamics 2',
}
_PARAMETER_NAMES = {
    1: {0: 'Source Route', 1: 'Digital Trim', 2: 'Polarity', 0x0a: 'Balance',
        0x0e: 'Main/Alt Input', 0x14: 'Gain Tracking', 0x86: 'Analog Gain', 0x83: 'Phantom Power'},
    2: {1: 'Aux Send Level', 2: 'Aux Send On', 3: 'Aux Pan', 4: 'Aux Tap'},
    3: {9: 'Group Assignment'},
    4: {2: 'EQ In', 3: 'EQ Band On', 5: 'EQ Frequency', 6: 'EQ Q', 7: 'EQ Gain',
        0x7d: 'Dynamic EQ On'},
    5: {2: 'Dynamics In', 0x0e: 'Threshold', 0x10: 'Ratio', 0x11: 'Attack',
        0x12: 'Release', 0x16: 'Key Input Route'},
    6: {0x24: 'Matrix Send Level', 0x25: 'Matrix Send On'},
    7: {3: 'Filter In', 5: 'Filter Frequency'},
    8: {0: 'Insert Return Route', 1: 'Insert Return Gain', 2: 'Insert In', 3: 'Insert Return Polarity',
        0x3c: 'Insert Send Gain', 0xe1: 'Insert Position', 0xfe: 'Insert Send Route'},
    9: {7: 'Delay On', 8: 'Delay'},
    12: {4: 'Control Group Membership', 5: 'Gang', 0x21: 'Fader', 0x22: 'Mute'},
    13: {0x23: 'Pan'},
    14: {0xfe: 'Output Route'},
    17: {0x83: 'Phantom Power', 0x86: 'Analog Gain', 0xfe: 'Input Copy Route'},
    18: {0: 'Socket Source Route'},
    28: {2: 'Mustard EQ In', 5: 'Mustard EQ Frequency', 6: 'Mustard EQ Q', 7: 'Mustard EQ Gain'},
}


def _parameter(parameter, module, band):
    return {'parameter': parameter, 'module': module, 'band': band,
            'module_name': _MODULE_NAMES.get(module),
            'name': _PARAMETER_NAMES.get(module, {}).get(parameter)}


def _native_key(parameter, module, band):
    return f'{parameter:02x}{module:02x}{band:02x}'


def _current_controls(console):
    channels = defaultdict(Counter)
    channel_counts = Counter(ch.channel_id for ch in console.snapshot.channels)
    extent = 0
    for channel in console.snapshot.channels:
        if channel_counts[channel.channel_id] != 1:
            continue
        counts = channels[channel.channel_id]
        for module in console._modules(channel):
            extent += 1 + len(module.controls) // 8
            if extent > _MAX_EXPANDED_RECORDS:
                raise SesFormatError('Recall Scope exceeds its current-state record budget')
            module_type = struct.unpack_from('<i', module.header.effective(58))[0]
            counts.update((parameter, kind, band) for parameter, kind, band, _, _ in
                          struct.iter_unpack('<BBBB4s', module.controls) if kind == module_type)
    return channels


def _classify(masks, safes, cue, channel_id, key, module_type, module_flags, flags, ignore_scope,
              sections_enabled, current):
    parameter, kind, band = key
    if kind != module_type:
        return 'unknown', ('controller_module_mismatch',)
    # Native ConsoleSnapshot.Recall dispatches these saved channel families.
    channel_type = channel_id >> 8
    if not (1 <= channel_type <= 0x18 or 0x1b <= channel_type <= 0x86):
        return 'unknown', ('unreviewed_channel_recall_path',)
    if sections_enabled and kind == 12 and parameter == 0x21 and (
            1 <= channel_type <= 7 or channel_type == 0x10):
        return 'scope_blocked', ('section_mode_fader_exclusion',)

    blocked, uncertain = [], []
    if not ignore_scope:
        layer = masks.evaluate(cue, channel_id, kind, parameter)
        for name in ('c9_group', 'd2_selected_section', 'cue'):
            if layer[name] is False:
                blocked.append(name)
            elif layer[name] is None:
                uncertain.append(name)
        if uncertain:
            uncertain.extend(layer['opaque_reasons'])
        # Output socket parameter zero bypasses both flags. Other socket
        # parameters apply the module flag, but not the controller flag.
        if (kind != 0x12 or parameter != 0) and module_flags & 4:
            blocked.append('module_excluded')
        if kind != 0x12 and flags & 4:
            blocked.append('controller_excluded')
        safe = safes.evaluate(channel_id, parameter, kind, band)
        safe_reasons = tuple(safe['reasons'])
        if blocked:
            return 'scope_blocked', tuple(blocked) + (safe_reasons if safe['safe'] is True else ())
        if safe['safe'] is True:
            return 'safe_blocked', safe_reasons
        if safe['safe'] is None:
            uncertain.extend(safe_reasons or ('unresolved_safe',))
    if current.get(key, 0) != 1:
        uncertain.append('current_controller_missing_or_ambiguous')
    if uncertain:
        return 'unknown', tuple(uncertain)
    return 'allowed', ('ignore_scope',) if ignore_scope else ()


def inspect_scope(snapshots, console, topology, structure, *, encoding='cp950',
                  snapshot_index=None, channel_id=None, include_parameters=True):
    """Classify every matching stored record against the reviewed saved gates."""
    if snapshots.version not in (2025, 2242) or not snapshots.version == topology.version == console.version:
        raise SesFormatError('Recall Scope inspection requires matching Q5 V2025 or V2242 sections')
    if encoding not in ENCODINGS:
        raise SesFormatError('Unsupported scope name encoding')
    if snapshot_index is not None and (type(snapshot_index) is not int or
                                       not 0 <= snapshot_index < len(snapshots.snapshots)):
        raise SesFormatError('Snapshot index is outside the saved cue list')
    if channel_id is not None and (type(channel_id) is not int or not 0 <= channel_id <= 0xffff):
        raise SesFormatError('Scope channel ID must be a uint16')

    snapshots.validate_references()
    SesFile._validate_current_references(console, snapshots)
    if console.module_pool is None:
        console = CurrentConsole(console.snapshot, console.version, module_pool=snapshots.modules)
    sections_enabled = structure['snapshot_sections_enabled']
    masks = ScopeMasks(snapshots, topology, section_scopes_enabled=sections_enabled)
    safes = SavedSafeState(console, snapshots)
    current = _current_controls(console)
    catalog, parameter_sets, set_indices = {}, [], {}
    extent, detail_entries = 0, 0
    cues = []
    policies, cue_by_policy, module_by_id = {}, [], {}
    matched_channel = False

    @lru_cache(maxsize=8192)
    def module_decisions(policy, target, identity):
        module = module_by_id[identity]
        header = module.header.effective(58)
        module_type = struct.unpack_from('<i', header)[0]
        current_controls = current.get(target, {})
        return tuple((key, _classify(masks, safes, cue_by_policy[policy], target, key, module_type,
                                     header[48], flags, header[53], sections_enabled, current_controls))
                     for parameter, kind, band, flags, _ in struct.iter_unpack('<BBBB4s', module.controls)
                     for key in ((parameter, kind, band),))

    for index, cue in enumerate(snapshots.snapshots):
        if snapshot_index is not None and index != snapshot_index:
            continue
        cue_metadata = cue.inspect(encoding, include_channels=False)
        policy_key = (cue.controller_scope.data, cue_metadata['flags'], cue_metadata['group'])
        if policy_key not in policies:
            policies[policy_key] = len(cue_by_policy)
            cue_by_policy.append(cue)
        policy = policies[policy_key]
        counts, input_faders = Counter(), Counter()
        channel_rows = []
        for channel in cue.channels:
            target = channel.channel_id
            if channel_id is not None and target != channel_id:
                continue
            matched_channel = True
            grouped = defaultdict(list)
            for module in console._modules(channel):
                extent += 1 + len(module.controls) // 8
                if extent > _MAX_EXPANDED_RECORDS:
                    raise SesFormatError('Recall Scope exceeds its record budget; select a Cue or channel')
                identity = id(module)
                module_by_id[identity] = module
                for key, (status, reasons) in module_decisions(policy, target, identity):
                    counts[status] += 1
                    if target >> 8 == 1 and key == (0x21, 12, 0):
                        input_faders[status] += 1
                    if include_parameters:
                        code = _native_key(*key)
                        if code not in catalog:
                            catalog[code] = _parameter(*key)
                        grouped[status, reasons].append(code)
            if not include_parameters or not grouped:
                continue
            signature = tuple((status, reasons, tuple(sorted(codes)))
                              for (status, reasons), codes in sorted(grouped.items()))
            if signature not in set_indices:
                detail_entries += sum(len(codes) for _, _, codes in signature)
                if detail_entries > _MAX_DETAIL_ENTRIES:
                    raise SesFormatError('Recall Scope exceeds its detail budget; select a Cue or channel')
                set_indices[signature] = len(parameter_sets)
                parameter_sets.append([{'status': status, 'reasons': reasons, 'parameters': codes}
                                       for status, reasons, codes in signature])
            meta = channel.metadata.effective(39)
            name = meta[3:3 + min(meta[2], 31)]
            channel_rows.append({'channel_id': target, 'name': name.decode(encoding, errors='replace'),
                                 'parameter_set': set_indices[signature]})
        row = {'index': index, 'number': cue_metadata['number'], 'name': cue_metadata['name'],
               'snapshot_group_number': cue_metadata['group'],
               'status_counts': {key: counts[key] for key in _STATUS},
               'input_fader_status_counts': {key: input_faders[key] for key in _STATUS}}
        if include_parameters:
            row['channels'] = channel_rows
        cues.append(row)
    if channel_id is not None and not matched_channel:
        raise SesFormatError('Channel ID is not present in the selected saved Cue records')
    report = {'version': snapshots.version, 'interpretation_basis': f'quantum5-v{snapshots.version}-static',
              'snapshot_sections_enabled': sections_enabled,
              'snapshot_index_filter': snapshot_index, 'channel_id_filter': channel_id,
              'effective_recall_verified': False, 'cues': cues}
    if include_parameters:
        report.update(parameters=catalog, parameter_sets=parameter_sets)
    return report


def inspect_scope_file(session, *, encoding='cp950', snapshot_index=None, channel_id=None):
    if session.build_profile not in _Q5_FILE_PROFILES:
        raise SesFormatError('Recall Scope inspection supports Quantum5 V2025 and V2242 files')
    report = inspect_scope(session._snapshots(), session._console(), session._topology(), session.structure,
                           encoding=encoding, snapshot_index=snapshot_index, channel_id=channel_id)
    return {'schema_version': 1, 'subsystem': 'q5-saved-recall-scope',
            'format_profile': session.build_profile, 'sha256': _sha256(session.to_bytes()),
            'editor_used': False, 'network_used': False, **report}

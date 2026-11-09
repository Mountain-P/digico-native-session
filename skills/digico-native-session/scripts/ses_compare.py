"""Compare the saved state of two Quantum5 Sessions without a console."""
from __future__ import annotations

from collections import Counter, defaultdict

from ses_file import ENCODINGS, LEGACY_READ_PROFILE, SesFile, SesFormatError, _sha256


_COMPARE_PROFILES = (LEGACY_READ_PROFILE, 'quantum5-v2242')


def _inspect(session, encoding, channel_id):
    from ses_automation import inspect_automation
    from ses_recall import inspect_recall
    from ses_scope import inspect_scope
    from ses_signal import inspect_signal

    if len(set(session.section_tags)) != len(session.section_tags):
        raise SesFormatError('Session comparison requires unambiguous section tags')
    sections = dict(zip(session.section_tags, session.sections))
    topology = session._topology()
    snapshots = session._snapshots()
    console = session._console()
    SesFile._validate_current_references(console, snapshots)
    structure = session.structure
    recall = inspect_recall(snapshots, console, structure['video_frame_rate'], encoding)
    scope = inspect_scope(snapshots, console, topology, structure,
                          encoding=encoding, include_parameters=False)
    recall['scope']['scope_masks_decoded'] = True
    recall['scope']['mask_interpretation_basis'] = scope['interpretation_basis']
    for cue, eligibility in zip(recall['cues'], scope['cues']):
        cue['saved_scope_status_counts'] = eligibility['status_counts']
        cue['saved_scope_input_fader_status_counts'] = eligibility['input_fader_status_counts']
    return {
        'format_profile': session.build_profile,
        'release': session.release,
        'sha256': _sha256(session.to_bytes()),
        'structure': structure,
        'sections': {f'{tag:02x}': {'bytes': len(data), 'sha256': _sha256(data)}
                     for tag, data in sections.items()},
        'topology': topology.inspect(encoding),
        'recall': recall,
        'signal': inspect_signal(snapshots, console, topology, encoding, channel_id=channel_id),
        'automation': inspect_automation(sections, topology, structure, encoding),
    }, topology, snapshots


def _pairs(left, right):
    """Return unique exact matches, side-only keys, and all ambiguous keys."""
    common = left.keys() & right.keys()
    pairs = {key: (left[key][0], right[key][0]) for key in common
             if len(left[key]) == len(right[key]) == 1}
    ambiguous = {key for key, rows in left.items() if len(rows) > 1}
    ambiguous.update(key for key, rows in right.items() if len(rows) > 1)
    return pairs, left.keys() - right.keys() - ambiguous, right.keys() - left.keys() - ambiguous, ambiguous


def _cue_keys(cues):
    rows = defaultdict(list)
    for index, row in enumerate(cues):
        rows[row['number_raw'], row['name_raw_hex']].append(index)
    return rows


def _named_channels(topology):
    rows = defaultdict(list)
    for group in topology.groups:
        for channel in group.channels:
            if channel.name_bytes:
                rows[group.group_id, channel.name_bytes].append(channel.channel_id)
    return rows


def _channel_pairing(left, right, encoding):
    left_rows, right_rows = _named_channels(left), _named_channels(right)
    pairs, left_only, right_only, ambiguous = _pairs(left_rows, right_rows)

    def describe(key):
        group, name = key
        return {'group_id': group, 'name': name.decode(encoding, errors='replace'),
                'name_raw_hex': name.hex()}

    return {
        'match_key': 'group_id_and_unique_exact_nonempty_name_bytes',
        'matched': [{**describe(key), 'left_id': pairs[key][0], 'right_id': pairs[key][1]}
                    for key in sorted(pairs)],
        'left_only': [{**describe(key), 'id': left_rows[key][0]} for key in sorted(left_only)],
        'right_only': [{**describe(key), 'id': right_rows[key][0]} for key in sorted(right_only)],
        'ambiguous': [{**describe(key), 'left_ids': left_rows.get(key, []),
                       'right_ids': right_rows.get(key, [])} for key in sorted(ambiguous)],
    }


def _cue_comparison(left_snapshots, right_snapshots, left_recall, right_recall, encoding):
    left_metadata = [cue.inspect(encoding, include_channels=False) for cue in left_snapshots.snapshots]
    right_metadata = [cue.inspect(encoding, include_channels=False) for cue in right_snapshots.snapshots]
    left_keys = _cue_keys(left_metadata)
    right_keys = _cue_keys(right_metadata)
    pairs, left_only, right_only, ambiguous = _pairs(left_keys, right_keys)
    left_cues, right_cues = left_recall['cues'], right_recall['cues']
    fields = ('controller_scope', 'crossfade', 'input_fader_record_count',
              'input_fader_crossfade_counts', 'input_fader_exclusion_count',
              'input_fader_candidate_after_saved_safes_count', 'saved_scope_status_counts',
              'saved_scope_input_fader_status_counts')
    matched = []
    for left_index, right_index in sorted(pairs.values()):
        before = left_cues[left_index]
        after = right_cues[right_index]
        changes = {key: {'left': before.get(key), 'right': after.get(key)} for key in fields
                   if before.get(key) != after.get(key)}
        # Compare the native timing flags as well as the module-derived view.
        for key in ('recall_at', 'recall_at_active', 'duration', 'locked', 'skip_previous_next'):
            a, b = left_metadata[left_index][key], right_metadata[right_index][key]
            if a != b:
                changes[key] = {'left': a, 'right': b}
        matched.append({'left_index': left_index, 'right_index': right_index, 'changes': changes})
    return {
        'match_key': 'unique_exact_native_number_and_name_bytes',
        'matched': matched,
        'left_only_indices': sorted(left_keys[key][0] for key in left_only),
        'right_only_indices': sorted(right_keys[key][0] for key in right_only),
        'ambiguous': [{'number_raw': key[0], 'name_raw_hex': key[1],
                       'left_indices': left_keys.get(key, []), 'right_indices': right_keys.get(key, [])}
                      for key in sorted(ambiguous)],
    }


def _different(left, right):
    return {key: {'left': left.get(key), 'right': right.get(key)}
            for key in sorted(left.keys() | right.keys()) if left.get(key) != right.get(key)}


def _group_counts(topology):
    return {f'{group.group_id:02x}': dict(Counter(ch.stereo_mode for ch in group.channels))
            for group in topology.groups}


def _without_parents(value):
    # Pool indices refer to different pools and cannot be compared across files.
    if isinstance(value, dict):
        return {key: _without_parents(item) for key, item in value.items()
                if key != 'parent_module_index'}
    if isinstance(value, list):
        return [_without_parents(item) for item in value]
    return value


def _signal_differences(matches, left, right):
    left_by_id = {row['channel_id']: row for row in left['current_channels']}
    right_by_id = {row['channel_id']: row for row in right['current_channels']}
    result = []
    for match in matches:
        a = left_by_id.get(match['left_id'])
        b = right_by_id.get(match['right_id'])
        if a is None or b is None:
            continue
        fields = [key for key in ('main_alt', 'inserts', 'aux_sends', 'group_sends')
                  if _without_parents(a[key]) != _without_parents(b[key])]
        if fields:
            result.append({**match, 'different_stored_fields': fields})
    return result


def _safe_differences(matches, left, right):
    a = {row['channel_id']: row['enabled_safes'] for row in left['current_safes']['channels']}
    b = {row['channel_id']: row['enabled_safes'] for row in right['current_safes']['channels']}
    return [{**match, 'left_reported_enabled': a.get(match['left_id'], []),
             'right_reported_enabled': b.get(match['right_id'], [])}
            for match in matches if a.get(match['left_id'], []) != b.get(match['right_id'], [])]


def _macro_targets(record, library):
    targets = []
    for command in record['commands']:
        effect = command['effect']
        if effect is None:
            continue
        extent = effect['input_logical_range']
        inputs = [library['input_channels'][number]
                  for number in range(extent['first'], extent['last'] + 1)]
        targets.append((inputs, library['aux_bands'].get(effect.get('aux_band'))))
    return targets


def _macro_comparison(left, right):
    if left is None or right is None:
        return {'left_section_present': left is not None, 'right_section_present': right is not None,
                'compared': False}
    before, after = defaultdict(list), defaultdict(list)
    for library, target in ((left, before), (right, after)):
        for index, macro in enumerate(library['records']):
            if macro['name_raw_hex']:
                target[macro['name_raw_hex']].append(index)
    pairs, left_only, right_only, ambiguous = _pairs(before, after)
    fields = ('trigger', 'commands', 'trigger_records')
    return {
        'left_section_present': True, 'right_section_present': True, 'compared': True,
        'match_key': 'unique_exact_nonempty_name_bytes',
        'matched': [
            {'left_index': a, 'right_index': b, 'name': left['records'][a]['name'],
             'different_stored_fields': [key for key in fields
                                         if left['records'][a][key] != right['records'][b][key]],
             'resolved_targets_differ': (_macro_targets(left['records'][a], left)
                                         != _macro_targets(right['records'][b], right))}
            for a, b in sorted(pairs.values())
        ],
        'left_only_indices': sorted(before[key][0] for key in left_only),
        'right_only_indices': sorted(after[key][0] for key in right_only),
        'ambiguous': [{'name_raw_hex': key, 'left_indices': before.get(key, []),
                       'right_indices': after.get(key, [])} for key in sorted(ambiguous)],
    }


def compare_sessions(left, right, encoding='cp950', *, left_channel_id=None, right_channel_id=None):
    if encoding not in ENCODINGS:
        raise SesFormatError('Unsupported comparison name encoding')
    if any(session.build_profile not in _COMPARE_PROFILES for session in (left, right)):
        raise SesFormatError('Deep comparison supports Quantum5 V2025 and V2242 files')
    a, a_topology, a_snapshots = _inspect(left, encoding, left_channel_id)
    b, b_topology, b_snapshots = _inspect(right, encoding, right_channel_id)
    pairing = _channel_pairing(a_topology, b_topology, encoding)
    return {
        'schema_version': 1,
        'subsystem': 'q5-saved-session-comparison',
        'encoding': encoding,
        'editor_used': False,
        'network_used': False,
        'effective_recall_verified': False,
        'physical_audio_verified': False,
        'signal_channel_filter': {'left': left_channel_id, 'right': right_channel_id},
        'left': a,
        'right': b,
        'differences': {
            'structure': _different(a['structure'], b['structure']),
            'section_bytes': _different(a['sections'], b['sections']),
            'channel_counts_by_stereo_mode': _different(_group_counts(a_topology), _group_counts(b_topology)),
            'channel_pairing': pairing,
            'current_signal_rows': _signal_differences(pairing['matched'], a['signal'], b['signal']),
            'current_safe_rows': _safe_differences(pairing['matched'], a['recall'], b['recall']),
            'scope_records': _different(a['recall']['scope'], b['recall']['scope']),
            'snapshot_list_options': _different(a['recall']['snapshot_list_options'],
                                                b['recall']['snapshot_list_options']),
            'cues': _cue_comparison(a_snapshots, b_snapshots, a['recall'], b['recall'], encoding),
            'clock': _different(a['automation']['clock'], b['automation']['clock']),
            'macros': _macro_comparison(a['automation']['macros'], b['automation']['macros']),
        },
    }

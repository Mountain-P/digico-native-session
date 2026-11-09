"""Inspect saved DiGiCo recall settings without simulating a recall."""
from __future__ import annotations

from collections import Counter
import hashlib
import math
import struct
import sys

from ses_console import CurrentConsole
from ses_snapshot import SnapshotFormatError, SnapshotSection


_INPUT, _FADER_MODULE, _FADER_PARAMETER = 1, 12, 0x21
_SAFE_NAMES = {
    0xf0: 'full_safe', 0xf1: 'input_safe', 0xf2: 'aux_safe', 0xf3: 'buss_safe',
    0xf4: 'eq_safe', 0xf5: 'dynamics_safe', 0xf6: 'fade_mute_safe', 0xf7: 'pan_safe',
    0xf8: 'inserts_safe', 0xf9: 'output_safe', 0xfa: 'external_safe',
}
# Q5 V2242 tModuleSnapshot.Recall checks bit 2 in the module and controller
# scope (0xAF856A and 0xAF86BE), unless IgnoreScope is set. Other bits stay opaque.
_RECALL_EXCLUDED = 0x04
_MAX_NAMED_EXCLUSIONS = 100_000
_MAX_EXPANDED_RECORDS = 20_000_000
# tSnapshotList.Store writes the options record from object offset 0x24.
_OPTIONS = {
    'auto_safe': 0x38, 'midi_fires_snapshots': 0x3c, 'fire_snapshot_sends_midi': 0x3d,
    'touch_to_recall': 0x3f, 'midi_safe': 0x47, 'relay_safe': 0x48, 'waves_safe': 0x91,
    'record_recall_times': 0x10e, 'klang_positions_safe': 0x10f, 'fourier_safe': 0x110,
}


def _scope(block):
    return {'bytes': len(block.data), 'sha256': hashlib.sha256(block.data).hexdigest()}


def _seconds(quarter_frames, frame_rate):
    if frame_rate is None or quarter_frames < 0:
        return None
    seconds = quarter_frames / 4 / frame_rate
    return seconds if math.isfinite(seconds) else None


def _channel_name(channel, encoding):
    metadata = channel.metadata.effective(39)
    if metadata[2] > 31:
        raise SnapshotFormatError('Channel name exceeds its ShortString31 byte bound')
    raw = metadata[3:3 + metadata[2]]
    row = {'channel_id': channel.channel_id}
    try:
        row['name'] = raw.decode(encoding, errors='strict')
    except UnicodeError:
        row.update(name=None, name_raw_hex=raw.hex())
    return row


def _charge_records(remaining, module):
    remaining -= 1 + len(module.controls) // 8
    if remaining < 0:
        raise SnapshotFormatError('Recall inspection exceeds its expanded module/controller record limit')
    return remaining


def _current_safes(console, encoding, remaining):
    channels = []
    unresolved = 0
    safe_counts = Counter()
    for channel in console.snapshot.channels:
        if not channel.own_modules.effective(1)[0] and console.module_pool is None:
            unresolved += 1
        states = {}
        for module in console._modules(channel):
            remaining = _charge_records(remaining, module)
            if struct.unpack_from('<i', module.header.effective(58))[0] != _FADER_MODULE:
                continue
            for parameter, kind, band, _flags, value in struct.iter_unpack('<BBBBf', module.controls):
                if kind == _FADER_MODULE and band == 0 and parameter in _SAFE_NAMES:
                    states.setdefault(_SAFE_NAMES[parameter], set()).add(bool(value) if value in (0, 1) else None)
        resolved = {key: next(iter(values)) for key, values in states.items() if len(values) == 1}
        enabled = [name for name in _SAFE_NAMES.values() if resolved.get(name) is True]
        if enabled:
            channels.append({**_channel_name(channel, encoding),
                             'full_safe': resolved.get('full_safe'), 'fade_mute_safe': resolved.get('fade_mute_safe'),
                             'enabled_safes': enabled})
            safe_counts.update(enabled)
    report = {'full_safe_count': safe_counts['full_safe'], 'fade_mute_safe_count': safe_counts['fade_mute_safe'],
              'safe_counts': {name: safe_counts[name] for name in _SAFE_NAMES.values()},
              'unresolved_channel_count': unresolved, 'channels': channels}
    return report, remaining


def _cue_recall(snapshot, index, pool, frame_rate, encoding, named_limit, safe_channel_ids, remaining):
    metadata = snapshot.inspect(encoding, include_channels=False)
    timing = snapshot.timing.effective(81)
    crossfade = struct.unpack_from('<H', timing, 4)[0]
    # CrossfadeWaitTime is at +0x7b0 relative to a timing block starting at +0x765.
    waits = struct.unpack_from('<H', timing, 75)[0]
    module_fades = Counter()
    controller_exclusions = Counter()
    fader_fades = Counter()
    fader_exclusions = []
    fader_exclusion_count = 0
    candidates = 0
    parent_references = 0
    for channel in snapshot.channels:
        channel_type = channel.channel_id >> 8
        modules = (channel.modules if channel.own_modules.effective(1)[0]
                   else (pool[i] for i in channel.modules if i != -1))
        for module in modules:
            remaining = _charge_records(remaining, module)
            header = module.header.effective(58)
            module_type = struct.unpack_from('<i', header)[0]
            scope_flags, fade, ignore_scope = struct.unpack_from('<BiB', header, 48)
            if fade:
                module_fades[(channel_type, module_type, fade)] += 1
            parent_references += module.parent_module_index != -1
            is_input_fader = (channel_type, module_type) == (_INPUT, _FADER_MODULE)
            # The output-socket module (0x12) has its own recall path.
            normal_scope = not ignore_scope and 0 <= module_type <= 0x1f and module_type != 0x12
            if not is_input_fader and not normal_scope:
                continue
            for parameter, kind, band, flags, _ in struct.iter_unpack('<BBBB4s', module.controls):
                excluded = normal_scope and kind == module_type and bool((flags | scope_flags) & _RECALL_EXCLUDED)
                if excluded:
                    controller_exclusions[module_type] += 1
                if not is_input_fader or (parameter, kind, band) != (_FADER_PARAMETER, _FADER_MODULE, 0):
                    continue
                fader_fades[fade] += 1
                if excluded:
                    fader_exclusion_count += 1
                    if len(fader_exclusions) < named_limit:
                        fader_exclusions.append({**_channel_name(channel, encoding),
                                                'controller_flags_raw': flags,
                                                'module_scope_flags_raw': scope_flags})
                elif ignore_scope or channel.channel_id not in safe_channel_ids:
                    candidates += 1
    report = {
        'index': index, **metadata, 'duration_active': bool(metadata['flags'] & 0x08),
        'controller_scope': _scope(snapshot.controller_scope),
        'crossfade': {'quarter_frames': crossfade, 'seconds': _seconds(crossfade, frame_rate),
                      'wait_raw': waits},
        'module_crossfades': [
            {'channel_type': channel_type, 'module_type': module_type,
             'quarter_frames': fade, 'seconds': _seconds(fade, frame_rate), 'record_count': count}
            for (channel_type, module_type, fade), count in sorted(module_fades.items())
        ],
        'controller_exclusions_by_module_type': [
            {'module_type': kind, 'record_count': count} for kind, count in sorted(controller_exclusions.items())
        ],
        'input_fader_record_count': sum(fader_fades.values()),
        'input_fader_crossfade_counts': [
            {'quarter_frames': fade, 'seconds': _seconds(fade, frame_rate), 'record_count': count}
            for fade, count in sorted(fader_fades.items())
        ],
        'input_fader_exclusion_count': fader_exclusion_count,
        'input_fader_exclusions': fader_exclusions,
        'omitted_input_fader_exclusion_details': fader_exclusion_count - len(fader_exclusions),
        'input_fader_candidate_after_saved_safes_count': candidates,
        'module_parent_reference_count': parent_references,
    }
    return report, remaining


def inspect_recall(snapshots, console, frame_rate, encoding='cp950'):
    """Report saved recall settings using Q5 V2242 layout evidence.

    Candidate counts apply known per-record scope flags and the saved D3 Safe
    values. They are an upper bound with those safes in effect because opaque
    scope masks and console state can further restrict recall.
    """
    if not isinstance(snapshots, SnapshotSection) or not isinstance(console, CurrentConsole):
        raise SnapshotFormatError('Recall inspection requires parsed D2 and D3 sections')
    if snapshots.version != console.version:
        raise SnapshotFormatError('Recall inspection requires matching D2 and D3 versions')
    if snapshots.version not in (2025, 2242):
        raise SnapshotFormatError('Recall inspection requires a V2025 or V2242 layout')
    if encoding not in ('ascii', 'cp950', 'utf-8'):
        raise SnapshotFormatError('Unsupported text encoding')
    snapshots.validate_references()
    frame_rate = (float(frame_rate) if type(frame_rate) in (int, float) and 0 < frame_rate <= sys.float_info.max
                  else None)
    options = snapshots.options.data
    current_safes, remaining = _current_safes(console, encoding, _MAX_EXPANDED_RECORDS)
    channel_counts = Counter(channel.channel_id for channel in console.snapshot.channels)
    safe_channel_ids = {row['channel_id'] for row in current_safes['channels']
                        if channel_counts[row['channel_id']] == 1 and (row['full_safe'] or row['fade_mute_safe'])}
    cues = []
    named_limit = _MAX_NAMED_EXCLUSIONS
    for index, snapshot in enumerate(snapshots.snapshots):
        cue, remaining = _cue_recall(snapshot, index, snapshots.modules, frame_rate, encoding,
                                     named_limit, safe_channel_ids, remaining)
        named_limit -= len(cue['input_fader_exclusions'])
        cues.append(cue)
    distribution = Counter(cue['input_fader_exclusion_count'] for cue in cues)
    return {
        'version': snapshots.version, 'frame_rate': frame_rate,
        'interpretation_basis': 'quantum5-v2242-static', 'effective_recall_verified': False,
        'scope': {
            'section_scope': _scope(snapshots.section_scope),
            'section_auto_update_exclusions': _scope(snapshots.section_exclusions),
            'current_controller_scope': _scope(console.snapshot.controller_scope),
            'distinct_cue_controller_scope_count': len({cue['controller_scope']['sha256'] for cue in cues}),
            'scope_masks_decoded': False,
        },
        'snapshot_list_options': {
            name: options[offset - 0x24] if len(options) > offset - 0x24 else None
            for name, offset in _OPTIONS.items()
        },
        'current_safes': current_safes,
        'shared_pool_parent_reference_count': sum(module.parent_module_index != -1 for module in snapshots.modules),
        'fader_exclusion_distribution': [
            {'excluded_count': excluded, 'snapshot_count': count}
            for excluded, count in sorted(distribution.items())
        ],
        'cues': cues,
    }

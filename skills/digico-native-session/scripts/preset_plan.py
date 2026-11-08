"""Pure native plans for ordinary input-channel processing presets, not Cues.

Exact Quantum1 V2126 static callback contracts only. No I/O, transport, process
access, UI, or target execution. A runner must bind live channel/preset objects,
check count/locks/scope and verify actual processing/persistence before success.
"""

from __future__ import annotations

import re
import struct

from mirror_codec import build_packet


TARGET_SHA256 = '535097f38bb459e6e299b5da50b62fa811511d92af6d7215a13d703a416f177d'
GROUP = 0x1B6
# TPresetsForm.SetPresetScope B7AF48, component offsets from Delphi RTTI.
# These are native UI checkbox semantics, not a guessed contiguous module list.
SCOPE_BITS = {
    'input': (1, 9, 11), 'input_gain': (17,), 'input_route': (128,),
    'eq': (4, 7), 'dynamics': (5,), 'aux': (2, 6), 'group': (3, 12, 13),
    'tidal_preamp': (26,), 'tidal_eq': (27, 28),
    'tidal_comp': (29,), 'tidal_gate': (30,),
}


class PresetPlanError(ValueError):
    pass


def _integer(value, minimum, maximum, label):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PresetPlanError(f'{label} must be an integer in [{minimum}, {maximum}]')
    return value


def _count(count):
    # Local planning resource bound, not an asserted native library limit.
    return _integer(count, 0, 10000, 'preset_count')


def _index(index, count):
    _count(count)
    return _integer(index, 0, count-1, 'preset_index')


def _input_channel(channel_id):
    value = _integer(channel_id, 0x100, 0x1FF, 'input_channel_id')
    return value


def _text(value, encoding, label):
    if encoding not in ('ascii', 'cp950'):
        raise PresetPlanError('preset encoding must be ascii or cp950')
    if not isinstance(value, str) or '\0' in value:
        raise PresetPlanError(f'{label} must be text without NUL')
    try:
        raw = value.encode(encoding, errors='strict')
    except UnicodeError as error:
        raise PresetPlanError(f'{label} is not representable in {encoding}') from error
    if len(raw) > 31:
        raise PresetPlanError(f'{label} exceeds the native 31-byte field')
    return raw


def _step(op, category, control, index, body, callback, **extra):
    identifier = struct.pack('<HBH', control, 0, GROUP)
    packet = build_packet(category=category, control_id=identifier,
                          payload=body, list_index=index)
    return {'op': op, 'category': category, 'control_id_hex': identifier.hex(),
            'list_index': index, 'body_hex': body.hex(), 'frame_hex': packet.hex(),
            'callback_va': callback, **extra}


def _plan(steps, count, **extra):
    return {'schema_version': 1, 'subsystem': 'native-input-preset-plan',
            'executable_sha256': TARGET_SHA256, 'runtime_acceptance_verified': False,
            'expected_preset_count': count, 'steps': steps,
            'execution_preconditions': [
                'exact executable/process and ordinary native peer binding',
                'fresh count, list VMT and selected object/channel ID readback',
                'stop on uncertain result; do not replay store/recall blindly',
            ], **extra}


def scope_bytes(scopes):
    """Translate proved native checkbox names into the exact32-byte tSet256."""
    if not isinstance(scopes, (list, tuple)) or not scopes:
        raise PresetPlanError('recall requires an explicit nonempty scope list')
    result = bytearray(32)
    seen = set()
    for name in scopes:
        if not isinstance(name, str) or name not in SCOPE_BITS or name in seen:
            raise PresetPlanError('unknown or duplicated preset scope')
        seen.add(name)
        for bit in SCOPE_BITS[name]:
            result[bit // 8] |= 1 << (bit % 8)
    return bytes(result)


def plan_store_input_preset(*, channel_id, preset_count, group_name='', name=None,
                            force_mono=False, encoding='ascii'):
    channel_id = _input_channel(channel_id)
    preset_count = _count(preset_count)
    if preset_count == 10000:
        raise PresetPlanError('store would exceed the local preset inventory bound')
    if not isinstance(force_mono, bool):
        raise PresetPlanError('force_mono must be boolean')
    group = _text(group_name, encoding, 'group_name')
    # B753BC uses low16 as channel ID, high16==1 for ForceMono; ordinary
    # non-mix input preset. Its string argument is GROUP, not preset name.
    index = channel_id | (int(force_mono) << 16)
    steps = [_step('store_input_preset', 3, 0xC9, index,
                   struct.pack('<I', len(group))+group, '0xB753BC',
                   expected_preset_count_after=preset_count+1,
                   created_preset_index=preset_count,
                   group_name_hex=group.hex(), name_source='current_channel_title')]
    if name is not None:
        raw = _text(name, encoding, 'name')
        steps.append(_step('rename_preset', 3, 0xCE, preset_count,
                           struct.pack('<I', len(raw))+raw, '0xB7594C', name_hex=raw.hex(),
                           expected_preset_count_after=preset_count+1))
    return _plan(steps, preset_count, final_expected_preset_count=preset_count+1,
                 encoding=encoding)


def plan_rename_preset(*, preset_index, preset_count, name, encoding='ascii'):
    index = _index(preset_index, preset_count)
    raw = _text(name, encoding, 'name')
    return _plan([_step('rename_preset', 3, 0xCE, index,
                        struct.pack('<I', len(raw))+raw, '0xB7594C', name_hex=raw.hex())],
                 preset_count, final_expected_preset_count=preset_count, encoding=encoding)


def plan_lock_preset(*, preset_index, preset_count, locked):
    index = _index(preset_index, preset_count)
    if not isinstance(locked, bool):
        raise PresetPlanError('locked must be boolean')
    return _plan([_step('lock_preset', 1, 0xD2, index,
                        struct.pack('<i', int(locked)), '0xB75F50', expected_lock_flag=int(locked))],
                 preset_count, final_expected_preset_count=preset_count)


def plan_delete_preset(*, preset_index, preset_count, group_name, lock_flag,
                        encoding='ascii'):
    index = _index(preset_index, preset_count)
    if _integer(lock_flag, 0, 255, 'lock_flag') != 0:
        raise PresetPlanError('this non-Cue delete requires an unlocked preset')
    group = _text(group_name, encoding, 'group_name')
    if not group:
        raise PresetPlanError('delete requires an explicit nonempty lab group')
    return _plan([_step('delete_preset', 1, 0xD1, index, bytes(4), '0xB75DA4',
                        expected_group_name_hex=group.hex(),
                        verify='fresh RPM must show zero Cue dependencies before send')],
                 preset_count, final_expected_preset_count=preset_count-1,
                 encoding=encoding)


def plan_update_input_preset(*, preset_index, preset_count, channel_id, lock_flag):
    index = _index(preset_index, preset_count)
    channel_id = _input_channel(channel_id)
    if _integer(lock_flag, 0, 255, 'lock_flag') != 0:
        raise PresetPlanError('native UpdateFrom rejects a locked preset')
    # High16+1 is channel count in B757F8. Zero highword selects one channel.
    return _plan([_step('update_input_preset', 1, 0xCC, index,
                        struct.pack('<i', channel_id), '0xB757F8', expected_lock_flag=0)],
                 preset_count, final_expected_preset_count=preset_count)


def plan_recall_input_preset(*, preset_index, preset_count, channel_id,
                             scopes, prior_scope_hex):
    index = _index(preset_index, preset_count)
    channel_id = _input_channel(channel_id)
    if not isinstance(prior_scope_hex, str) or not re.fullmatch('[0-9a-fA-F]{64}', prior_scope_hex):
        raise PresetPlanError('prior_scope_hex must be the copied32-byte native scope')
    prior = bytes.fromhex(prior_scope_hex)
    selected = scope_bytes(scopes)
    scope_body = struct.pack('<I', 32)
    steps = [
        _step('set_preset_recall_scope', 2, 0xD3, 0, scope_body+selected,
              '0xB7605C', expected_scope_hex=selected.hex()),
        # B7569C low16 payload resolves actual target channel; upper flags zero.
        _step('recall_input_preset', 1, 0xC8, index, struct.pack('<i', channel_id),
              '0xB7569C', target_channel_id=channel_id,
              verify='processing values must change as stored, not just preset pointer'),
        _step('restore_preset_recall_scope', 2, 0xD3, 0, scope_body+prior,
              '0xB7605C', expected_scope_hex=prior.hex()),
    ]
    return _plan(steps, preset_count, final_expected_preset_count=preset_count,
                 scopes=list(scopes), prior_scope_hex=prior.hex(),
                 recovery='on partial failure inspect scope/state before any replay or restore')

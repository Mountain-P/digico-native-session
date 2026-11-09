"""Synthetic D2/D3 fixtures for read-only recall inspection."""
from hashlib import sha256
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/digico-native-session/scripts'))

from ses_console import CurrentConsole
from ses_recall import inspect_recall
from ses_snapshot import ConsoleSnapshot, ChannelSnapshot, ModuleSnapshot, SnapshotFormatError, SnapshotSection, _Block


def module(controls, *, fade=0, scope=0, ignore=0, parent=-1, kind=12):
    header = bytearray(58)
    struct.pack_into('<i', header, 0, kind)
    struct.pack_into('<iBiB', header, 44, len(controls), scope, fade, ignore)
    records = b''.join(struct.pack('<BBBBf', parameter, kind, 0, flags, value)
                       for parameter, flags, value in controls)
    return ModuleSnapshot(_Block(bytes(header)), records, parent, -1, 0)


def channel(channel_id, name, modules, *, owned=True):
    metadata = bytearray(39)
    struct.pack_into('<H', metadata, 0, channel_id)
    metadata[2] = len(name)
    metadata[3:3 + len(name)] = name
    return ChannelSnapshot(_Block(bytes((owned,))), modules, _Block(bytes(metadata)))


def snapshot(channels, name=b'Test', number=100, fade=0, wait=0, scope=b'scope'):
    timing = bytearray(81)
    struct.pack_into('<iH', timing, 0, number, fade)
    struct.pack_into('<H', timing, 75, wait)
    empty = _Block(b'')
    return ConsoleSnapshot(channels, _Block(name), bytes(8), _Block(scope), empty, b'',
                           empty, empty, _Block(bytes(timing)), empty, empty)


class RecallInspectTests(unittest.TestCase):
    def test_cues_resolve_shared_module_exclusions_and_crossfade(self):
        included = module([(0x21, 0, -5.0)], fade=100)
        excluded = module([(0x21, 4, -20.0)], fade=100, parent=0)
        ignore = module([(0x21, 4, -10.0)], scope=4, ignore=1)
        excluded_module = module([(0x21, 0, -10.0)], scope=4)
        eq = module([(0x07, 4, 3.0)], fade=200, kind=4)
        output_socket = module([(0, 4, 0)], kind=0x12)
        channels = [channel(0x0100, b'Included', [0, -1], owned=False),
                    channel(0x0101, b'Excluded', [1], owned=False),
                    channel(0x0102, b'Bypass', [ignore]),
                    channel(0x0103, b'Module', [excluded_module]),
                    channel(0x0500, b'Aux EQ', [eq]),
                    channel(0x4e00, b'Socket', [output_socket])]
        d2 = SnapshotSection.empty(2242)
        d2.modules = [included, excluded]
        d2.snapshots = [snapshot(channels, number=450, fade=100, wait=11), snapshot([], number=500)]
        raw = d2.to_bytes()
        d2 = SnapshotSection.from_bytes(raw)
        saved_safe = module([(0xf0, 0, 1), (0xf6, 0, 1)])
        d3 = CurrentConsole(snapshot([channel(0x0100, b'Included', [saved_safe]),
                                      channel(0x0102, b'Bypass', [saved_safe])]), 2242)
        report = inspect_recall(d2, d3, 25)
        cue = report['cues'][0]
        self.assertEqual(cue['number'], 4.5)
        self.assertEqual(cue['crossfade'], {'quarter_frames': 100, 'seconds': 1.0, 'wait_raw': 11})
        self.assertEqual(cue['input_fader_record_count'], 4)
        self.assertEqual(cue['input_fader_crossfade_counts'], [
            {'quarter_frames': 0, 'seconds': 0.0, 'record_count': 2},
            {'quarter_frames': 100, 'seconds': 1.0, 'record_count': 2},
        ])
        self.assertEqual(cue['input_fader_exclusion_count'], 2)
        self.assertEqual(cue['input_fader_candidate_after_saved_safes_count'], 1)
        self.assertEqual(cue['recall_at'], 0)
        self.assertFalse(cue['duration_active'])
        self.assertEqual(cue['input_fader_exclusions'], [
            {'channel_id': 0x0101, 'name': 'Excluded', 'controller_flags_raw': 4, 'module_scope_flags_raw': 0},
            {'channel_id': 0x0103, 'name': 'Module', 'controller_flags_raw': 0, 'module_scope_flags_raw': 4},
        ])
        self.assertEqual(cue['module_crossfades'][-1], {
            'channel_type': 5, 'module_type': 4, 'quarter_frames': 200,
            'seconds': 2.0, 'record_count': 1,
        })
        self.assertEqual(cue['controller_exclusions_by_module_type'], [
            {'module_type': 4, 'record_count': 1}, {'module_type': 12, 'record_count': 2},
        ])
        self.assertEqual(cue['module_parent_reference_count'], 1)
        self.assertEqual(report['shared_pool_parent_reference_count'], 1)
        self.assertEqual(report['fader_exclusion_distribution'], [
            {'excluded_count': 0, 'snapshot_count': 1}, {'excluded_count': 2, 'snapshot_count': 1},
        ])
        self.assertFalse(report['effective_recall_verified'])
        self.assertEqual(d2.to_bytes(), raw)

    def test_current_safes_are_separate_from_snapshot_flags(self):
        safe = module([(0xf0, 0, 1), (0xf6, 0, 1)])
        partial = module([(0xf0, 0, 0), (0xf4, 0, 1), (0xf6, 0, 1)])
        ambiguous = module([(0xf0, 0, 1), (0xf0, 0, 0), (0xf6, 0, 1)])
        invalid = module([(0xf0, 0, float('nan')), (0xf6, 0, 0)])
        channels = [channel(0x0100, b'\xa4\xa4', [safe]),
                    channel(0x0101, b'Partial', [partial]),
                    channel(0x0102, b'Ambiguous', [ambiguous]),
                    channel(0x0103, b'Invalid', [invalid]),
                    channel(0x0104, b'Unresolved', [0], owned=False)]
        raw = snapshot(channels).to_bytes()
        console = CurrentConsole.from_bytes(raw)
        report = inspect_recall(SnapshotSection.empty(2242), console, 25)
        safes = report['current_safes']
        self.assertEqual(safes['full_safe_count'], 1)
        self.assertEqual(safes['fade_mute_safe_count'], 3)
        self.assertEqual(safes['unresolved_channel_count'], 1)
        self.assertEqual(safes['safe_counts']['eq_safe'], 1)
        self.assertEqual(safes['safe_counts']['dynamics_safe'], 0)
        self.assertEqual(safes['channels'], [
            {'channel_id': 0x0100, 'name': '中', 'full_safe': True, 'fade_mute_safe': True,
             'enabled_safes': ['full_safe', 'fade_mute_safe']},
            {'channel_id': 0x0101, 'name': 'Partial', 'full_safe': False, 'fade_mute_safe': True,
             'enabled_safes': ['eq_safe', 'fade_mute_safe']},
            {'channel_id': 0x0102, 'name': 'Ambiguous', 'full_safe': None, 'fade_mute_safe': True,
             'enabled_safes': ['fade_mute_safe']},
        ])
        self.assertEqual(console.to_bytes(), raw)

    def test_opaque_scope_fingerprints_and_raw_options(self):
        d2 = SnapshotSection.empty(2242)
        d2.snapshots = [snapshot([], scope=b'abc'), snapshot([], scope=b'abc'), snapshot([], scope=b'def')]
        d2.options.data = bytes(0x3c - 0x24) + b'\1'
        report = inspect_recall(d2, CurrentConsole(snapshot([], scope=b'def'), 2242), 0)
        self.assertIsNone(report['frame_rate'])
        self.assertIsNone(report['cues'][0]['crossfade']['seconds'])
        self.assertEqual(report['scope']['distinct_cue_controller_scope_count'], 2)
        self.assertEqual(report['cues'][0]['controller_scope'], {'bytes': 3, 'sha256': sha256(b'abc').hexdigest()})
        self.assertEqual(report['scope']['current_controller_scope'], report['cues'][2]['controller_scope'])
        self.assertFalse(report['scope']['scope_masks_decoded'])
        self.assertEqual(report['snapshot_list_options']['midi_fires_snapshots'], 1)
        self.assertIsNone(report['snapshot_list_options']['fourier_safe'])
        with self.assertRaisesRegex(SnapshotFormatError, 'matching D2 and D3'):
            inspect_recall(d2, CurrentConsole(snapshot([]), 2126), 25)
        with self.assertRaisesRegex(SnapshotFormatError, 'V2025 or V2242'):
            inspect_recall(SnapshotSection.empty(2126), CurrentConsole(snapshot([]), 2126), 25)

    def test_named_exclusion_detail_bound_keeps_exact_counts(self):
        d2 = SnapshotSection.empty(2242)
        excluded = module([(0x21, 4, -15.0)])
        d2.snapshots = [snapshot([channel(0x100, b'\xff', [excluded])]),
                        snapshot([channel(0x101, b'Later', [excluded])])]
        with patch('ses_recall._MAX_NAMED_EXCLUSIONS', 1):
            report = inspect_recall(d2, CurrentConsole(snapshot([]), 2242), 25, 'ascii')
        first, second = report['cues']
        self.assertEqual(first['input_fader_exclusions'][0]['name_raw_hex'], 'ff')
        self.assertEqual(first['omitted_input_fader_exclusion_details'], 0)
        self.assertEqual(second['input_fader_exclusions'], [])
        self.assertEqual(second['input_fader_exclusion_count'], 1)
        self.assertEqual(second['omitted_input_fader_exclusion_details'], 1)

    def test_expanded_shared_controller_limit_is_bounded_across_cues(self):
        d2 = SnapshotSection.empty(2242)
        d2.modules = [module([(0x21, 0, -15.0)])]
        d2.snapshots = [snapshot([channel(0x100, b'Shared', [0], owned=False)]) for _ in range(2)]
        with patch('ses_recall._MAX_EXPANDED_RECORDS', 3):
            with self.assertRaisesRegex(SnapshotFormatError, 'expanded module/controller'):
                inspect_recall(d2, CurrentConsole(snapshot([]), 2242), 25)


if __name__ == '__main__':
    unittest.main()

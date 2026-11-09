"""Regression checks for channel counts, bus companions and new-session processing."""
from pathlib import Path
import struct
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/digico-native-session/scripts'
sys.path.insert(0, str(SCRIPTS))

from ses_presets import PRESET_SCOPES, SessionPresets
from ses_routing import C9Console
from ses_snapshot import ConsoleSnapshot
from ses_structure import CREATION_PROFILES, StructureError, build_structure


def _c9_groups(built):
    parsed = C9Console.from_bytes(built.c9, 2242)
    return {group.group_id: group.channels for group in parsed.groups}


class SesStructureTests(unittest.TestCase):
    def test_new_mixed_width_topology_has_matching_counts_and_buss_companions(self):
        built = build_structure('quantum5-v2242', {
            'inputs': {'mono': 2, 'stereo': 2},
            'auxes': {'mono': 1, 'stereo': 1},
            'groups': {'mono': 1, 'stereo': 2},
            'matrix_inputs': 3, 'matrix_outputs': 2,
            'control_groups': 1, 'graphic_eqs': 0,
        })
        groups = _c9_groups(built)
        self.assertEqual(struct.unpack_from('<HH', built.console_record, 0x02), (2, 2))
        self.assertEqual(struct.unpack_from('<HH', built.console_record, 0x12), (1, 1))
        self.assertEqual(struct.unpack_from('<HH', built.console_record, 0x22), (1, 2))
        self.assertEqual([channel.stereo_mode for channel in groups[1]], [1, 1, 2, 2])
        self.assertEqual([struct.unpack_from('<h', c.metadata, 0x50)[0] for c in groups[1]],
                         [0, 1, 2, 4])
        self.assertEqual([len(groups[key]) for key in (0x0F, 0x10, 0x0A, 0x11)], [3, 2, 1, 0])
        self.assertEqual(built.master_channel_id, 0x0601)
        self.assertEqual([channel.channel_id for channel in groups[7]], [0x0708, 0x0700, 0x0709])
        all_channels = {channel.channel_id: channel for rows in groups.values() for channel in rows}
        for right in groups[7]:
            left_id, = struct.unpack_from('<H', right.metadata, 0x32)
            self.assertEqual(struct.unpack_from('<H', all_channels[left_id].metadata, 0x32)[0],
                             right.channel_id)
        d3 = ConsoleSnapshot.from_bytes(built.d3, 2242)
        self.assertEqual({channel.channel_id for channel in d3.channels}, set(all_channels))

    def test_default_local_channel_counts_and_mono_preset_processing_across_profiles(self):
        local_counts = {'quantum1-v2242-4': (18, 10), 'quantum2-v2242-4': (12, 12),
                        'quantum3-v2242': (16, 16), 'quantum5-v2242': (16, 16),
                        'quantum7-v2242': (24, 24), 'quantum8-v2242': (24, 24)}
        self.assertEqual(set(CREATION_PROFILES), set(local_counts))
        for profile, local in local_counts.items():
            with self.subTest(profile=profile):
                built = build_structure(profile, {'inputs': {'mono': 1}})
                groups = _c9_groups(built)
                self.assertEqual((len(groups[0x1C]), len(groups[0x4E])), local)
                inputs = built.topology.input_groups
                outputs = built.topology.output_groups
                self.assertEqual(inputs.keys[:9], (0x1B00, 0x1100, 0x0E00, 0x1800, 0x100,
                                                   0x600, 0x500, 0x1000, 0xB00))
                self.assertEqual(outputs.keys[:4], (0x4D00, 0x1100, 0x0E00, 0x1800))
                self.assertTrue(all(key >> 8 == 0x4E for key in outputs.keys[4:]))
                self.assertEqual(b'Local aes\r\n' in inputs.text, local[0] < 24)
                self.assertEqual(b'Local line\r\n' in outputs.text, local[1] < 24)
                mix = ConsoleSnapshot.from_bytes(built.d3, 2242)
                library = SessionPresets.from_bytes(b'\xdb\0' + bytes(4), 2242)
                library.capture(mix, 0x0100, name=b'Input default', group=b'Synthetic',
                                scopes=list(PRESET_SCOPES))
                capture = library.presets[0].channels[0]
                self.assertEqual(sum(len(module.controls) // 8 for module in capture.modules), 167)

    def test_invalid_width_missing_master_and_unknown_structure_are_rejected(self):
        cases = (
            ({'inputs': {'mono': 47, 'stereo': 1}}, '48-leg'),
            ({'groups': {'mono': 4}}, 'stereo Group'),
            ({'matrix_outputs': True}, 'must be an integer'),
            ({'sample_rate': 96000}, 'Unknown'),
        )
        for configuration, detail in cases:
            with self.subTest(configuration=configuration), self.assertRaisesRegex(StructureError, detail):
                build_structure('quantum1-v2242-4', configuration)


if __name__ == '__main__':
    unittest.main()

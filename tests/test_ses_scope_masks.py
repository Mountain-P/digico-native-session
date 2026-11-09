"""Synthetic stored recall masks. No original Session is a test fixture."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/digico-native-session/scripts'))

from ses_routing import C9Channel, C9Console, C9Group, SocketGroups
from ses_scope_masks import ScopeMasks
from ses_snapshot import ConsoleSnapshot, SnapshotSection, _Block
from ses_structure import build_structure


def _group(version, group_id, *, modules=0, parameters=(), section=0, buss_id=0):
    group = bytearray(108)
    group[0] = group_id
    struct.pack_into('<I', group, 0x21, modules)
    for parameter in parameters:
        group[0x29 + parameter // 8] |= 1 << (parameter % 8)
    channel = bytearray(88 if version == 2025 else 121)
    struct.pack_into('<HH', channel, 0, group_id << 8, 1)
    struct.pack_into('<H', channel, 0x32, buss_id)
    channel[0x25] = 1
    channel[0x54] = section
    return C9Group(0xca, bytes(group), [C9Channel(bytes(channel))])


def _topology(version, *groups):
    topology = C9Console(version, SocketGroups(b'', ()), SocketGroups(b'', ()), list(groups))
    return C9Console.from_bytes(topology.to_bytes(), version)


def _cue(*, bits=(), flags=0, group_number=0, empty_scope=False):
    scope = bytearray(0 if empty_scope else 992)
    for module, parameter in bits:
        scope[(module - 1) * 32 + parameter // 8] |= 1 << (parameter % 8)
    timing = bytearray(81)
    timing[6] = group_number
    empty = _Block(b'')
    return ConsoleSnapshot([], _Block(b'Synthetic'), bytes(8), _Block(bytes(scope)),
                           _Block(struct.pack('<H', flags)), b'', empty, empty,
                           _Block(bytes(timing)), empty, empty)


class RecallScopeMaskTests(unittest.TestCase):
    def test_saved_group_and_cue_bits_use_distinct_indexes(self):
        for release in (2025, 2242):
            with self.subTest(release=release):
                d2 = SnapshotSection.from_bytes(SnapshotSection.empty(2242).to_bytes(), release)
                c9 = _topology(release, _group(release, 1, modules=1 << 12, parameters=(33,)))
                cue = _cue(bits=((12, 33), (12, 34), (4, 33)))
                scopes = ScopeMasks(d2, c9)
                fader = scopes.evaluate(cue, 0x0100, 12, 33)
                self.assertTrue(fader['c9_group'])
                self.assertTrue(fader['cue'])
                self.assertEqual(fader['section_id'], 0)
                self.assertEqual(fader['section_source'], 'unassigned')
                self.assertTrue(fader['d2_selected_section'])
                self.assertEqual(fader['opaque_reasons'], [])
                mute = scopes.evaluate(cue, 0x0100, 12, 34)
                self.assertFalse(mute['c9_group'])
                self.assertTrue(mute['cue'])
                eq = scopes.evaluate(cue, 0x0100, 4, 33)
                self.assertFalse(eq['c9_group'])
                self.assertTrue(eq['cue'])
                parameter_zero = scopes.evaluate(cue, 0x0100, 12, 0)
                self.assertTrue(parameter_zero['c9_group'])
                self.assertFalse(parameter_zero['cue'])

    def test_stereo_right_uses_left_group_and_its_own_snapshot_section(self):
        d2 = SnapshotSection.empty(2242)
        root = bytearray(288)
        # Only root section 2 admits Fader. Snapshot group 1 replaces it.
        struct.pack_into('<I', root, 72, 1 << 12)
        root[72 + 8 + 33 // 8] = 1 << (33 % 8)
        d2.section_scope.data = bytes(root)
        d2.groups = [_Block(bytes(336))]
        c9 = _topology(2242, _group(2242, 5, modules=1 << 12, parameters=(33,), section=1, buss_id=0x0700),
                       _group(2242, 7, section=2, buss_id=0x0500))
        cue = _cue(group_number=1)
        state = ScopeMasks(d2, c9, section_scopes_enabled=True).evaluate(cue, 0x0700, 12, 33)
        self.assertEqual(state['group_id'], 5)
        self.assertEqual(state['section_id'], 2)
        self.assertTrue(state['c9_group'])
        self.assertTrue(state['d2_global_section'])
        self.assertFalse(state['d2_snapshot_group_section'])
        self.assertEqual(state['section_source'], 'snapshot_group')
        self.assertFalse(state['d2_selected_section'])
        unknown = ScopeMasks(d2, c9).evaluate(cue, 0x0700, 12, 33)
        self.assertIsNone(unknown['d2_selected_section'])
        self.assertIn('section_feature_unverified', unknown['opaque_reasons'])
        disabled = ScopeMasks(d2, c9, section_scopes_enabled=False).evaluate(cue, 0x0700, 12, 33)
        self.assertTrue(disabled['d2_selected_section'])
        self.assertTrue(ScopeMasks(d2, c9).evaluate(_cue(), 0x0700, 12, 33)['d2_selected_section'])
        missing = ScopeMasks(d2, c9, section_scopes_enabled=True).evaluate(_cue(group_number=2), 0x0700, 12, 33)
        self.assertIn('missing_d2_section_scope', missing['opaque_reasons'])

    def test_native_misc_scope_overrides_four_individual_bits(self):
        d2 = SnapshotSection.empty(2242)
        scopes = ScopeMasks(d2, None)
        for module, parameter, flag in ((12, 4, 0x0100), (16, 255, 0x0100),
                                        (12, 5, 0x0200), (15, 5, 0x0200)):
            with self.subTest(module=module, parameter=parameter):
                admitted = scopes.evaluate(_cue(flags=flag), 0x0100, module, parameter)
                blocked = scopes.evaluate(_cue(bits=((module, parameter),)), 0x0100, module, parameter)
                self.assertFalse(admitted['cue_raw'])
                self.assertTrue(admitted['cue'])
                self.assertTrue(blocked['cue_raw'])
                self.assertFalse(blocked['cue'])

    def test_native_physical_mask_defaults_and_output_socket_exceptions(self):
        d2 = SnapshotSection.empty(2242)
        c9 = _topology(2242, _group(2242, 0x1b, modules=1 << 0x11, parameters=(4,)),
                       _group(2242, 0x1c, modules=1 << 0x11),
                       _group(2242, 0x4e))
        scopes = ScopeMasks(d2, c9)
        internal = scopes.evaluate(_cue(), 0x1b00, 0x11, 4)
        local_input = scopes.evaluate(_cue(), 0x1c00, 0x11, 4)
        direct = scopes.evaluate(_cue(), 0x4e00, 0x12, 0)
        other = scopes.evaluate(_cue(), 0x4e00, 0x12, 1)
        self.assertTrue(internal['c9_group_saved'])
        self.assertFalse(internal['c9_group'])
        self.assertFalse(local_input['c9_group_saved'])
        self.assertTrue(local_input['c9_group'])
        self.assertTrue(direct['c9_group'])
        self.assertTrue(direct['d2_selected_section'])
        self.assertFalse(direct['cue_raw'])
        self.assertTrue(direct['cue'])
        self.assertFalse(other['c9_group'])
        self.assertTrue(other['cue'])

    def test_unknown_masks_and_q5_factory_defaults(self):
        d2 = SnapshotSection.empty(2242)
        structure = build_structure('quantum5-v2242', {})
        topology = C9Console.from_bytes(structure.c9, 2242)
        input_scope = next(group.metadata for group in topology.groups if group.group_id == 1)
        self.assertEqual(struct.unpack_from('<II', input_scope, 0x21), (0x7c4873be, 0x7c4873be))
        self.assertEqual(input_scope[0x29:0x69], b'\xff' * 64)
        scopes = ScopeMasks(d2, topology)
        fader = scopes.evaluate(_cue(empty_scope=True), 0x0100, 12, 33)
        self.assertTrue(fader['c9_group'])
        self.assertIsNone(fader['cue'])
        self.assertIn('missing_cue_scope_bit', fader['opaque_reasons'])
        missing = scopes.evaluate(_cue(), 0xffff, 12, 33)
        self.assertIsNone(missing['c9_group'])
        self.assertIn('missing_c9_channel', missing['opaque_reasons'])
        self.assertEqual(scopes.evaluate(_cue(), 0x0100, 0, 33)['opaque_reasons'], ['unreviewed_module_id'])


if __name__ == '__main__':
    unittest.main()

"""Synthetic native Store streams only; no vendor files, editor or network."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/digico-native-session/scripts'))
import ses_snapshot
from ses_snapshot import (ConsoleSnapshot, SnapshotSection, SnapshotFormatError,
                          PresetSection, SnapshotSetlists, assert_no_external_snapshot_refs)


def record(data):
    return struct.pack('<H', len(data)) + data


def module(origin=0, parent=-1, user_count=0):
    header = bytearray(58)
    struct.pack_into('<i', header, 0, 12)
    header[4:10] = b'\x05fader'
    struct.pack_into('<i', header, 44, 1)
    struct.pack_into('<i', header, 54, user_count)
    return (b'\xda\0' + record(header) + bytes.fromhex('210c00000000a0c1')
            + struct.pack('<iii', parent, origin, 0))


def channel(owned=False, origin=0):
    metadata = bytearray(39)
    struct.pack_into('<H', metadata, 0, 0x0100)
    metadata[2:7] = b'\x04Test'
    struct.pack_into('<i', metadata, 34, -1)
    item = module(origin) if owned else struct.pack('<i', 0)
    return b'\xd5\0' + record(bytes([int(owned)])) + struct.pack('<i', 1) + item + record(metadata)


def cue(name=b'Cue A', owned=False, origin=0, extra=False):
    timing = bytearray(81)
    struct.pack_into('<i', timing, 0, 100)
    struct.pack_into('<i', timing, 10, 6000)
    # Arbitrary opaque scope/layout/notes/allocation data must survive exactly.
    return (b'\xd3\0' + struct.pack('<i', 1) + channel(owned, origin)
            + record(name) + bytes.fromhex('0102030405060708')
            + record(b'\x4a' * 992) + record(b'\x82\x04' + (b'extra' if extra else b''))
            + struct.pack('<i', 3) + b'\x90\x3c\x7f'
            + record(b'\x0b' * 8) + record(b'\x0c' * 807)
            + record(timing + (b'future' if extra else b''))
            + record(b'opaque notes') + record(bytes.fromhex('0102030405060708')))


def section(two=True):
    options = bytearray(237)
    struct.pack_into('<4i', options, 0, 1 if two else 0, -1, 1 if two else 0, 0)
    cues = [cue(extra=True)] + ([cue(b'Cue B')] if two else [])
    return (b'\xd2\0\xd2\0' + struct.pack('<i', 1) + module(1 if two else 0, user_count=len(cues))
            + struct.pack('<i', 0) + struct.pack('<i', 1) + record(b'\x45' * 336)
            + struct.pack('<i', len(cues)) + b''.join(cues)
            + record(options) + record(b'\x46' * 288) + record(b'\x47' * 264))


def presets(origin=-1):
    return (b'\xdb\0' + struct.pack('<i', 1) + b'\xd7\0' + struct.pack('<i', 1)
            + channel(owned=True, origin=origin) + record(b'Preset A')
            + record(b'Synthetic') + record(b'Notes') + record(bytes(14)))


def setlists():
    return (b'\x73\0' + struct.pack('<i', 2)
            + b'\x01A' + bytes(30) + struct.pack('<iii', 2, 0, 2)
            + b'\x01B' + bytes(30) + struct.pack('<ii', 1, 1) + struct.pack('<i', 1))


class SnapshotStoreTests(unittest.TestCase):
    def test_vn_vo_lossless_shared_pool_and_unknown_excess(self):
        raw = section()
        for version in (2126, 2242):
            parsed = SnapshotSection.from_bytes(raw, version)
            self.assertEqual(parsed.to_bytes(), raw)
            self.assertEqual(parsed.modules[0].inspect()['controllers'][0]['value_raw_hex'], '0000a0c1')
            self.assertEqual(parsed.inspect()['snapshots'][0]['name'], 'Cue A')
            self.assertEqual(parsed.snapshots[0].channels[0].modules, [0])
            self.assertFalse(parsed.inspect()['native_load_verified'])

    def test_current_d3_owned_module_roundtrip(self):
        raw = cue(owned=True, origin=-1)
        parsed = ConsoleSnapshot.from_bytes(raw)
        self.assertEqual(parsed.to_bytes(), raw)
        self.assertEqual(parsed.channels[0].modules[0].inspect()['type_id'], 12)

    def test_compact_inspect_does_not_build_channel_dictionaries(self):
        parsed = SnapshotSection.from_bytes(section())
        with patch.object(ses_snapshot.ChannelSnapshot, 'inspect', side_effect=AssertionError('must not build channels')):
            result = parsed.inspect(include_channels=False)
        self.assertNotIn('channels', result['snapshots'][0])
        self.assertEqual(result['snapshots'][0]['channel_count'], 1)

    def test_number_is_display_value_with_separate_raw_and_unique_whole_setter(self):
        parsed = SnapshotSection.from_bytes(section())
        self.assertEqual(parsed.inspect()['snapshots'][0]['number'], 1)
        self.assertEqual(parsed.inspect()['snapshots'][0]['number_raw'], 100)
        parsed.set_number(1, 2)
        with self.assertRaises(SnapshotFormatError):
            parsed.set_number(0, 2)
        parsed.duplicate(0, 1)
        self.assertEqual(parsed.inspect()['snapshots'][1]['number'], 3)

    def test_locked_edit_delete_rejected_without_mutation(self):
        parsed = SnapshotSection.from_bytes(section())
        parsed.snapshots[0].flags.data = struct.pack('<H', 0x2000)
        before = parsed.to_bytes()
        for action in (lambda: parsed.delete(0), lambda: parsed.rename(0, b'X'),
                       lambda: parsed.timecode(0, 4), lambda: parsed.set_number(0, 5)):
            with self.assertRaisesRegex(SnapshotFormatError, 'locked'):
                action()
        self.assertEqual(parsed.to_bytes(), before)

    def test_name_and_timecode_change_only_authorized_record_bytes(self):
        parsed = SnapshotSection.from_bytes(section())
        original = parsed.to_bytes()
        original_pool = parsed.modules[0].to_bytes()
        original_other = parsed.snapshots[1].to_bytes()
        timing = parsed.snapshots[0].timing.data
        scope = parsed.snapshots[0].controller_scope.data
        parsed.rename(0, b'\xa4\xa4 Cue').timecode(0, 123456, active=False)
        updated = SnapshotSection.from_bytes(parsed.to_bytes())
        self.assertEqual(updated.snapshots[0].name.data, b'\xa4\xa4 Cue')
        self.assertEqual(updated.snapshots[0].timing.data[:10], timing[:10])
        self.assertEqual(updated.snapshots[0].timing.data[14:], timing[14:])
        self.assertEqual(updated.snapshots[0].controller_scope.data, scope)
        self.assertEqual(updated.modules[0].to_bytes(), original_pool)
        self.assertEqual(updated.snapshots[1].to_bytes(), original_other)
        self.assertEqual(updated.snapshots[0].flags.data, b'\x02\x04extra')
        self.assertNotEqual(updated.to_bytes(), original)

    def test_deleted_origins_clear_and_later_origins_remap(self):
        parsed = SnapshotSection.from_bytes(section())
        parsed.delete(0)
        self.assertEqual(parsed.modules[0].original_snapshot_index, 0)
        self.assertEqual(parsed.inspect()['current_snapshot_index'], 0)
        self.assertEqual(parsed.inspect()['surface_snapshot_index'], 0)
        parsed.delete(0)
        self.assertEqual(parsed.modules[0].original_snapshot_index, -1)
        self.assertEqual(parsed.inspect()['current_snapshot_index'], -1)
        self.assertEqual(parsed.inspect()['surface_snapshot_index'], -1)
        self.assertEqual(len(SnapshotSection.from_bytes(parsed.to_bytes()).snapshots), 0)

    def test_duplicate_preserves_pool_and_remaps_origins(self):
        parsed = SnapshotSection.from_bytes(section())
        controls = parsed.modules[0].controls
        parsed.duplicate(0, 0)
        self.assertEqual(len(parsed.snapshots), 3)
        self.assertEqual(parsed.modules[0].original_snapshot_index, 2)
        self.assertEqual(parsed.modules[0].controls, controls)
        self.assertEqual(parsed.inspect()['current_snapshot_index'], 2)
        self.assertEqual(parsed.snapshots[0].name.data, b'Cue A')
        parsed.rename(0, b'New')
        self.assertEqual(parsed.snapshots[1].name.data, b'Cue A')
        self.assertEqual(SnapshotSection.from_bytes(parsed.to_bytes()).to_bytes(), parsed.to_bytes())

    def test_duplicate_delete_adjust_shared_user_counts_without_reordering_pool(self):
        parsed = SnapshotSection.from_bytes(section())
        def users():
            return struct.unpack_from('<i', parsed.modules[0].header.effective(58), 54)[0]
        self.assertEqual(users(), 2)
        parsed.duplicate(0, 2)
        self.assertEqual(users(), 3)
        for expected in (2, 1, 0):
            parsed.delete(0)
            self.assertEqual(users(), expected)
        self.assertEqual(len(parsed.modules), 1)
        self.assertEqual(SnapshotSection.from_bytes(parsed.to_bytes()).to_bytes(), parsed.to_bytes())

    def test_shared_user_underflow_refused_before_any_delete_mutation(self):
        parsed = SnapshotSection.from_bytes(section())
        parsed.modules[0].header.patch(54, bytes(4), 58)
        before = parsed.to_bytes()
        with self.assertRaisesRegex(SnapshotFormatError, 'UserCount'):
            parsed.delete(0)
        self.assertEqual(parsed.to_bytes(), before)

    def test_create_uses_owned_current_state_and_refuses_unowned(self):
        parsed = SnapshotSection.from_bytes(section(False))
        parsed.create_from_current_console(cue(b'Current', owned=True, origin=-1))
        self.assertEqual(len(parsed.snapshots), 2)
        self.assertTrue(parsed.snapshots[1].channels[0].own_modules.effective(1)[0])
        before = parsed.to_bytes()
        with self.assertRaises(SnapshotFormatError):
            parsed.create_from_current_console(cue(owned=False))
        self.assertEqual(parsed.to_bytes(), before)

    def test_failed_creation_does_not_partially_mutate_existing_references(self):
        parsed = SnapshotSection.from_bytes(section())
        before = parsed.to_bytes()
        with self.assertRaises(SnapshotFormatError):
            parsed.create_from_current_console(cue(owned=True, origin=999), index=0)
        self.assertEqual(parsed.to_bytes(), before)

    def test_bad_versions_tokens_references_and_truncations_rejected(self):
        for version in (2125, 2241, True, '2242'):
            with self.assertRaises(SnapshotFormatError):
                SnapshotSection.from_bytes(section(), version)
        for raw in (b'', section()[:-1], section() + b'\0', b'\xd1\0' + section()[2:]):
            with self.assertRaises(SnapshotFormatError):
                SnapshotSection.from_bytes(raw)
        raw = bytearray(section())
        struct.pack_into('<i', raw, 4, -1)
        with self.assertRaises(SnapshotFormatError):
            SnapshotSection.from_bytes(raw)
        parsed = SnapshotSection.from_bytes(section())
        parsed.modules[0].parent_module_index = 10
        with self.assertRaises(SnapshotFormatError):
            parsed.to_bytes()

    def test_name_time_and_index_bounds(self):
        parsed = SnapshotSection.from_bytes(section())
        before = parsed.to_bytes()
        for value in (b'x'*32, b'bad\0name', 'text'):
            with self.assertRaises(SnapshotFormatError):
                parsed.rename(0, value)
        for value in (-1, 0x80000000, True, 1.5):
            with self.assertRaises(SnapshotFormatError):
                parsed.timecode(0, value)
        for index in (-1, 2, True):
            with self.assertRaises(SnapshotFormatError):
                parsed.delete(index)
        self.assertEqual(parsed.to_bytes(), before)

    def test_native_short_records_zero_fill_without_destroying_original_bytes(self):
        parsed = SnapshotSection.from_bytes(section())
        parsed.snapshots[0].timing.data = b'\0'
        parsed.snapshots[0].flags.data = b''
        raw = parsed.to_bytes()
        updated = SnapshotSection.from_bytes(raw)
        self.assertEqual(updated.to_bytes(), raw)
        updated.timecode(0, 4)
        self.assertEqual(updated.inspect()['snapshots'][0]['recall_at'], 4)
        self.assertTrue(updated.inspect()['snapshots'][0]['recall_at_active'])

    def test_typed_memoryview_byte_bound_precedes_copy_and_parse(self):
        view = memoryview(bytes(16)).cast('I')
        self.assertEqual(len(view), 4)
        with patch.object(ses_snapshot, 'MAX_STREAM_BYTES', 8):
            with self.assertRaisesRegex(SnapshotFormatError, 'byte bound'):
                SnapshotSection.from_bytes(view)

    def test_insert_count_and_prospective_byte_budget_before_mutation(self):
        parsed = SnapshotSection.from_bytes(section())
        before = parsed.to_bytes()
        self.assertEqual(parsed.byte_size(), len(before))
        with patch.object(ses_snapshot, 'MAX_STREAM_BYTES', parsed.byte_size() + 1):
            with self.assertRaisesRegex(SnapshotFormatError, 'insertion exceeds'):
                parsed.duplicate(0, 2)
        with patch.object(ses_snapshot, 'MAX_SNAPSHOTS', 2):
            with self.assertRaisesRegex(SnapshotFormatError, 'insertion exceeds'):
                parsed.duplicate(0, 2)
        self.assertEqual(parsed.to_bytes(), before)

    def test_shared_parent_cannot_reference_self_or_later_pool_item(self):
        parsed = SnapshotSection.from_bytes(section())
        parsed.modules[0].parent_module_index = 0
        with self.assertRaisesRegex(SnapshotFormatError, 'earlier shared parent'):
            parsed.to_bytes()

    def test_preset_lossless_and_external_origin_guard(self):
        raw = presets()
        parsed = PresetSection.from_bytes(raw)
        self.assertEqual(parsed.to_bytes(), raw)
        self.assertEqual(parsed.byte_size(), len(raw))
        self.assertEqual(parsed.assert_no_snapshot_origin_refs(), 1)
        with self.assertRaisesRegex(SnapshotFormatError, 'external Snapshot origin'):
            PresetSection.from_bytes(presets(0)).assert_no_snapshot_origin_refs()

    def test_setlist_lossless_insert_delete_reorder_reference_remap(self):
        raw = setlists()
        parsed = SnapshotSetlists.from_bytes(raw)
        self.assertEqual(parsed.to_bytes(), raw)
        self.assertEqual(parsed.byte_size(), len(raw))
        parsed.insert_snapshot(1, 3)
        self.assertEqual([x.snapshot_indices for x in parsed.lists], [[0, 3], [2]])
        parsed.delete_snapshot(0, 4)
        self.assertEqual([x.snapshot_indices for x in parsed.lists], [[2], [1]])
        parsed.reorder_snapshots([2, 1, 0])
        self.assertEqual([x.snapshot_indices for x in parsed.lists], [[0], [1]])
        self.assertEqual(parsed.current_setlist_index, 1)
        self.assertEqual(SnapshotSetlists.from_bytes(parsed.to_bytes()).to_bytes(), parsed.to_bytes())
        before = parsed.to_bytes()
        with self.assertRaises(SnapshotFormatError):
            parsed.remap({0: 0})
        self.assertEqual(parsed.to_bytes(), before)

    def test_structural_guard_requires_empty_setlist_and_independent_d3_db(self):
        arguments = dict(version=2242, current_section=cue(owned=True, origin=-1),
                         preset_section=presets(), setlist_section=b'\x73\0' + struct.pack('<ii', 0, -1))
        self.assertTrue(assert_no_external_snapshot_refs(**arguments)['external_snapshot_references_absent'])
        for override in (dict(setlist_section=setlists()), dict(preset_section=presets(0)),
                         dict(current_section=cue(owned=True, origin=0)), dict(setlist_section=None)):
            with self.assertRaises(SnapshotFormatError):
                assert_no_external_snapshot_refs(**{**arguments, **override})


if __name__ == '__main__':
    unittest.main()

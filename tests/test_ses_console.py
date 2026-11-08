import math
from pathlib import Path
import struct
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'digico-native-session' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from ses_console import CurrentConsole, ConsoleFormatError


def block(raw):
    return struct.pack('<H', len(raw)) + raw


def module(kind, controls, *, parent=-1):
    header = bytearray(58)
    struct.pack_into('<i', header, 0, kind)
    struct.pack_into('<i', header, 44, len(controls))
    return b'\xda\0' + block(header) + b''.join(controls) + struct.pack('<iii', parent, -1, 0)


def record(prefix, value, flags=0):
    return bytes.fromhex(prefix) + bytes((flags,)) + struct.pack('<f', value)


def fixture(*, flags=0, parent=-1, duplicate=False, channel_id=0x100, own=True):
    fader = record('210c00', -12.25, flags)
    controls = [fader, record('220c00', 0), record('aa0c05', 123.5)]
    if duplicate:
        controls.append(fader)
    modules = [module(12, controls, parent=parent), module(1, [record('010100', 1.5)])]
    metadata = bytearray(range(39))
    struct.pack_into('<H', metadata, 0, channel_id)
    metadata[2:7] = b'\x04Desk'
    channel = (b'\xd5\0' + block(bytes((int(own),))) + struct.pack('<i', 2)
               + (b''.join(modules) if own else struct.pack('<ii', 0, 1)) + block(metadata + b'opaque'))
    return (b'\xd3\0' + struct.pack('<i', 1) + channel + block(b'Synthetic') + bytes(8)
            + block(bytes(992)) + block(bytes(2)) + struct.pack('<i', 0)
            + block(bytes(8)) + block(bytes(807)) + block(bytes(81)) + block(b'') + block(b''))


class CurrentConsoleTests(unittest.TestCase):
    def test_roundtrip_and_numeric_mapping(self):
        raw = fixture()
        console = CurrentConsole.from_bytes(raw, build_profile='quantum5-v2242')
        self.assertEqual(console.to_bytes(), raw)
        state = console.inspect(channel_id=0x100)
        self.assertEqual(state['channels'][0]['name'], 'Desk')
        controls = {row['parameter']: row for row in state['channels'][0]['basic_controls']}
        self.assertEqual(controls['fader']['id'], '210c000001')
        self.assertEqual(controls['fader']['value'], -12.25)
        self.assertEqual(controls['trim']['value'], 1.5)
        self.assertEqual(controls['mute']['value'], 0)
        self.assertFalse(state['live_definition_verified'])
        self.assertFalse(state['native_load_verified'])

    def test_exact_limited_float_and_name_patch_preserves_unknown_bytes(self):
        original = fixture()
        console = CurrentConsole.from_bytes(original)
        console.set_basic_control('210c000001', -32)
        console.set_basic_control('0101000001', .5)
        console.set_basic_control('220c000001', 1)
        console.rename_channel(0x100, b'New')
        expected = original.replace(record('210c00', -12.25), record('210c00', -32))
        expected = expected.replace(record('010100', 1.5), record('010100', .5))
        expected = expected.replace(record('220c00', 0), record('220c00', 1))
        expected = expected.replace(b'\x04Desk', b'\x03Newk')
        self.assertEqual(console.to_bytes(), expected)
        self.assertIn(b'opaque', console.to_bytes())
        self.assertIn(record('aa0c05', 123.5), console.to_bytes())
        self.assertIn(bytes.fromhex('220c00000000803f'), console.to_bytes())

    def test_raw_inspection_retains_unknown_ids_without_float_type_guess(self):
        c = CurrentConsole.from_bytes(fixture())
        self.assertNotIn('raw_controls', c.inspect(channel_id=0x100)['channels'][0])
        report = c.inspect(channel_id=0x100, include_raw_controls=True)['channels'][0]
        rows = report['raw_controls']
        self.assertEqual(len(rows), 4)
        unknown = next(row for row in rows if row['id'] == 'aa0c050001')
        self.assertEqual(unknown['value_raw_hex'], struct.pack('<f', 123.5).hex())
        self.assertEqual(unknown['value_type'], 'opaque_native_4bytes')
        self.assertNotIn('value', unknown)
        with self.assertRaises(ConsoleFormatError):
            c.inspect(include_raw_controls=1)
        shared = CurrentConsole.from_bytes(fixture(own=False)).inspect(include_raw_controls=True)['channels'][0]
        self.assertEqual(shared['raw_controls'], [])
        self.assertEqual(shared['module_pool_indices'], [0, 1])
        from unittest.mock import patch
        import ses_console
        with patch.object(ses_console, 'MAX_RAW_CONTROL_ROWS', 3):
            with self.assertRaises(ConsoleFormatError):
                c.inspect(include_raw_controls=True)

    def test_snapshot_pool_view_resolves_stored_basic_and_raw_records_without_mutation(self):
        owned = CurrentConsole.from_bytes(fixture(flags=1, parent=0))
        shared = CurrentConsole.from_bytes(fixture(own=False)).snapshot
        shared.channels[0].modules = [0, -1, 1]
        pool = owned.snapshot.channels[0].modules
        source_before = shared.to_bytes()
        pool_before = [item.to_bytes() for item in pool]
        view = CurrentConsole(shared, 2242, 'quantum5-v2242', module_pool=pool)
        result = view.inspect(channel_id=0x100, include_raw_controls=True)
        channel = result['channels'][0]
        self.assertFalse(channel['own_modules'])
        self.assertTrue(channel['shared_module_references_resolved'])
        self.assertEqual(channel['module_pool_indices'], [0, -1, 1])
        self.assertEqual({row['parameter']: row['value'] for row in channel['basic_controls']},
                         {'fader': -12.25, 'mute': 0.0, 'trim': 1.5})
        self.assertTrue(all(not row['file_write_supported'] for row in channel['basic_controls']))
        self.assertEqual(len(channel['raw_controls']), 4)
        unknown = next(row for row in channel['raw_controls'] if row['id'] == 'aa0c050001')
        self.assertEqual(unknown['value_raw_hex'], struct.pack('<f', 123.5).hex())
        self.assertNotIn('value', unknown)
        self.assertFalse(result['scope_inheritance_applied'])
        self.assertFalse(result['effective_recall_verified'])
        self.assertEqual(view.to_bytes(), source_before)
        self.assertEqual([item.to_bytes() for item in pool], pool_before)

    def test_snapshot_pool_view_is_readonly_and_bad_references_fail_closed(self):
        owned = CurrentConsole.from_bytes(fixture())
        pool = owned.snapshot.channels[0].modules
        view = CurrentConsole.from_bytes(fixture(own=False), module_pool=pool)
        before = view.to_bytes()
        pool_before = [item.to_bytes() for item in pool]
        with self.assertRaises(ConsoleFormatError):
            view.set_basic_control('210c000001', -10)
        with self.assertRaises(ConsoleFormatError):
            view.rename_channel(0x100, b'Changed')
        self.assertEqual(view.to_bytes(), before)
        self.assertEqual([item.to_bytes() for item in pool], pool_before)
        for index in (-2, len(pool), True):
            view.snapshot.channels[0].modules = [index]
            with self.assertRaises(ConsoleFormatError):
                view.inspect(include_raw_controls=True)
        readonly_owned = CurrentConsole(owned.snapshot, 2242, module_pool=[])
        with self.assertRaises(ConsoleFormatError):
            readonly_owned.set_basic_control('210c000001', -10)
        from unittest.mock import patch
        import ses_console
        view = CurrentConsole.from_bytes(fixture(own=False), module_pool=pool)
        with patch.object(ses_console, 'MAX_RAW_CONTROL_ROWS', 3):
            with self.assertRaises(ConsoleFormatError):
                view.inspect(include_raw_controls=True)

    def test_profile_trim_bounds_and_unbound_intersection(self):
        for profile in (None, 'quantum2-v2242-4', 'quantum3-v2242', 'quantum5-v2242', 'quantum7-v2242'):
            c = CurrentConsole.from_bytes(fixture(), build_profile=profile)
            c.set_basic_control('0101000001', -20)
            c.set_basic_control('0101000001', 60)
            with self.assertRaises(ConsoleFormatError):
                c.set_basic_control('0101000001', -20.01)
        for profile in ('quantum1-v2242-4', 'quantum8-v2242'):
            CurrentConsole.from_bytes(fixture(), build_profile=profile).set_basic_control('0101000001', -40)
        CurrentConsole.from_bytes(fixture(), version=2126, build_profile='quantum1-v2126').set_basic_control('0101000001', -40)
        with self.assertRaises(ConsoleFormatError):
            CurrentConsole.from_bytes(fixture(), build_profile='quantum1-v2126')

    def test_float32_range_checks_on_encoded_value(self):
        c = CurrentConsole.from_bytes(fixture())
        # Same representable value as 10.0 is accepted; next float32 is outside.
        c.set_basic_control('210c000001', 10 + 1e-8)
        with self.assertRaises(ConsoleFormatError):
            c.set_basic_control('210c000001', struct.unpack('<f', struct.pack('<I', 0x41200001))[0])
        for value in (True, None, '0', math.inf, -math.inf, math.nan, 1e300):
            with self.subTest(value=value), self.assertRaises(ConsoleFormatError):
                c.set_basic_control('210c000001', value)
        for value in (.5, 1.00000001, -1):
            with self.assertRaises(ConsoleFormatError):
                c.set_basic_control('220c000001', value)

    def test_rejects_flags_inheritance_duplicate_and_shared_records(self):
        for raw in (fixture(flags=1), fixture(parent=0), fixture(duplicate=True), fixture(own=False)):
            c = CurrentConsole.from_bytes(raw)
            with self.assertRaises(ConsoleFormatError):
                c.set_basic_control('210c000001', 0)

    def test_duplicate_channel_or_missing_control_is_not_writable(self):
        c = CurrentConsole.from_bytes(fixture())
        c.snapshot.channels.append(c.snapshot.channels[0])
        self.assertTrue(all(not row['file_write_supported']
                            for channel in c.inspect()['channels']
                            for row in channel['basic_controls']))
        with self.assertRaises(ConsoleFormatError):
            c.rename_channel(0x100, b'New')
        with self.assertRaises(ConsoleFormatError):
            c.set_basic_control('210c000001', 0)
        c = CurrentConsole.from_bytes(fixture())
        c.snapshot.channels[0].modules.pop(0)
        with self.assertRaises(ConsoleFormatError):
            c.set_basic_control('210c000001', 0)
        c = CurrentConsole.from_bytes(fixture(duplicate=True))
        flags = [x['file_write_supported'] for x in c.inspect()['channels'][0]['basic_controls']
                 if x['parameter'] == 'fader']
        self.assertEqual(flags, [False, False])

    def test_scope_and_strict_ids(self):
        c = CurrentConsole.from_bytes(fixture())
        for key in ('210c00', '210c00000100', 'zz0c000001', ' 210c000001', '0704000001', '0101010001', '210c000002'):
            with self.subTest(key=key), self.assertRaises(ConsoleFormatError):
                c.set_basic_control(key, 0)
        for channel in (True, -1, 65536, 0x200, 0x101):
            with self.assertRaises(ConsoleFormatError):
                c.rename_channel(channel, b'New')

    def test_names_are_encoded_bytes_without_truncation_or_guessing(self):
        c = CurrentConsole.from_bytes(fixture())
        raw = '\u526f\u6b4c'.encode('cp950')
        c.rename_channel(0x100, raw)
        self.assertEqual(c.inspect(encoding='cp950', channel_id=0x100)['channels'][0]['name'], '\u526f\u6b4c')
        self.assertIsNone(c.inspect(encoding='ascii', channel_id=0x100)['channels'][0]['name'])
        for value in (b'', b'a' * 32, b'a\0b', 'New'):
            with self.assertRaises(ConsoleFormatError):
                c.rename_channel(0x100, value)
        with self.assertRaises(ConsoleFormatError):
            c.inspect(encoding='latin-1')


if __name__ == '__main__':
    unittest.main()

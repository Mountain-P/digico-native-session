import struct
import unittest

from test_ses_routing import _fixture, _module

from ses_scope_safe import SavedSafeState
from ses_snapshot import SnapshotFormatError, SnapshotSection


def _bits(value):
    return struct.unpack('<I', struct.pack('<f', value))[0]


def _flags(console, channel_id, **values):
    channel = console._channel(channel_id)
    rows = [(int(parameter[1:], 16), 0, _bits(value)) for parameter, value in values.items()]
    channel.modules = [module for module in channel.modules
                       if struct.unpack_from('<i', module.header.effective(58))[0] != 0x0c]
    channel.modules.append(_module(0x0c, rows))


class SavedSafeStateTests(unittest.TestCase):
    def test_grouped_safe_map_and_partial_state(self):
        console = _fixture()[0].console
        _flags(console, 0x100, pf0=0, pf1=1, pf3=1, pf4=1, pf5=1,
               pf6=0, pf7=1, pf8=1, pf9=1, pfa=1)
        saved = SavedSafeState(console)
        for module, reason in [(1, 'input_safe'), (9, 'input_safe'), (0x0b, 'input_safe'),
                               (3, 'buss_safe'), (4, 'eq_safe'), (7, 'eq_safe'),
                               (5, 'dynamics_safe'), (8, 'inserts_safe'), (0x0d, 'pan_safe'),
                               (0x0e, 'output_safe'), (0x13, 'external_safe')]:
            with self.subTest(module=module):
                self.assertEqual(saved.evaluate(0x100, 1, module, 0), {'safe': True, 'reasons': [reason]})
        self.assertFalse(saved.evaluate(0x100, 0x21, 0x0c, 0)['safe'])
        self.assertFalse(saved.evaluate(0x100, 1, 0x16, 0)['safe'])
        self.assertIsNone(saved.evaluate(0x100, 1, 6, 0)['safe'])
        self.assertIsNone(saved.evaluate(0x101, 0x21, 0x0c, 0)['safe'])
        self.assertIsNone(saved.evaluate(0x1234, 0x21, 0x0c, 0)['safe'])
        _flags(console, 0x100, pf1=0)
        self.assertIsNone(SavedSafeState(console).evaluate(0x100, 1, 1, 0)['safe'])
        _flags(console, 0x100, pf0=1)
        self.assertTrue(SavedSafeState(console).evaluate(0x100, 0x21, 0x0c, 0)['safe'])

    def test_aux_node_destination_and_klang_override(self):
        console = _fixture()[0].console
        source = console._channel(0x100)
        source.modules.append(_module(2, [(0xf2, 0, 0), (0xf2, 1, _bits(1))]))
        _flags(console, 0x100, pf0=0, pf2=0)
        _flags(console, 0x500, pf0=0, pf2=1)
        _flags(console, 0x501, pf0=1, pf2=0)
        saved = SavedSafeState(console)
        self.assertEqual(saved.evaluate(0x100, 1, 2, 0), {'safe': True, 'reasons': ['destination_aux_safe']})
        self.assertEqual(saved.evaluate(0x100, 1, 2, 1), {'safe': True, 'reasons': ['aux_node_safe']})

        # A destination's Full Safe is not the native test for an Aux node.
        _flags(console, 0x500, pf0=1, pf2=0)
        snapshots = SnapshotSection.empty(2242)
        snapshots.options.patch(0xeb, b'\1', 237)
        before = console.to_bytes(), snapshots.to_bytes()
        saved = SavedSafeState(console, snapshots)
        self.assertFalse(saved.evaluate(0x100, 1, 2, 0)['safe'])
        for parameter in (0x0b, 0x0e, 0x0f, 0x10):
            self.assertEqual(saved.evaluate(0x100, parameter, 2, 0),
                             {'safe': True, 'reasons': ['klang_positions_safe']})
        self.assertFalse(saved.evaluate(0x100, 0x0c, 2, 0)['safe'])
        self.assertIsNone(SavedSafeState(console).evaluate(0x100, 0x10, 2, 0)['safe'])
        self.assertEqual((console.to_bytes(), snapshots.to_bytes()), before)

    def test_main_alt_setting_and_mustard_module_take_own_path(self):
        console = _fixture()[0].console
        _flags(console, 0x100, pf0=0, pf1=0)
        saved = SavedSafeState(console)
        self.assertFalse(saved.evaluate(0x100, 0, 1, 0)['safe'])
        self.assertEqual(saved.evaluate(0x100, 0x0e, 1, 0),
                         {'safe': None, 'reasons': ['unresolved:dont_mirror_alt_input']})

        _flags(console, 0x100, pf0=1, pf4=1, pf5=0)
        source = console._channel(0x100)
        source.modules += [_module(0x1c, [(0xf4, 0, 0)]), _module(0x1d, [(0xf5, 0, _bits(1))])]
        saved = SavedSafeState(console)
        self.assertFalse(saved.evaluate(0x100, 2, 0x1b, 0)['safe'])
        self.assertFalse(saved.evaluate(0x100, 2, 0x1c, 0)['safe'])
        self.assertTrue(saved.evaluate(0x100, 2, 0x1d, 0)['safe'])
        self.assertTrue(saved.evaluate(0x100, 2, 0x1e, 0)['safe'])
        self.assertIsNone(saved.evaluate(0x100, 2, 0x1a, 0)['safe'])

    def test_full_safe_route_exception_follows_source(self):
        console = _fixture()[0].console
        _flags(console, 0x100, pf0=1)
        _flags(console, 0x101, pf0=0, pf9=1)
        route = _module(0x0f, [(0, 0, 0x01010000), (0, 1, 0)])
        console._channel(0x100).modules.append(route)
        saved = SavedSafeState(console)
        self.assertEqual(saved.evaluate(0x100, 0, 0x0f, 0),
                         {'safe': True, 'reasons': ['linked_source:output_safe']})
        self.assertEqual(saved.evaluate(0x100, 0, 0x0f, 1),
                         {'safe': False, 'reasons': ['linked_route_disconnected']})
        self.assertTrue(saved.evaluate(0x100, 1, 0x0f, 0)['safe'])
        self.assertIsNone(saved.evaluate(0x100, 0, 0x15, 0)['safe'])
        _flags(console, 0x101, pf0=0, pf9=0)
        self.assertFalse(SavedSafeState(console).evaluate(0x100, 0, 0x0f, 0)['safe'])

    def test_shared_current_records_and_ambiguous_values(self):
        console = _fixture()[0].console
        source = console._channel(0x100)
        source.own_modules.data = b'\0'
        source.modules = [0]
        snapshots = SnapshotSection.empty(2242)
        snapshots.modules = [_module(0x0c, [(0xf0, 0, 0), (0xf6, 0, _bits(1))])]
        self.assertIsNone(SavedSafeState(console).evaluate(0x100, 0x21, 0x0c, 0)['safe'])
        saved = SavedSafeState(console, snapshots)
        self.assertTrue(saved.evaluate(0x100, 0x21, 0x0c, 0)['safe'])
        snapshots.modules[0] = _module(0x0c, [(0xf0, 0, 0), (0xf6, 0, 0), (0xf6, 0, _bits(1))])
        self.assertIsNone(SavedSafeState(console, snapshots).evaluate(0x100, 0x21, 0x0c, 0)['safe'])
        with self.assertRaisesRegex(SnapshotFormatError, 'uint16'):
            saved.evaluate(0x100, 0x21, 0x0c, -1)

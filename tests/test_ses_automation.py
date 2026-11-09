from pathlib import Path
import struct
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'digico-native-session' / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from ses_automation import inspect_automation
from ses_routing import C9Channel, C9Console, C9Group, SocketGroups
from ses_snapshot import SnapshotFormatError


def _topology(version=2242, duplicate_input=False):
    groups = []
    source = [(0x0120, 9, b'Track'), (0x0121, 10, b'Spare')]
    if duplicate_input:
        source.append((0x0122, 10, b'Conflict'))
    for group_id, channels in ((1, source), (5, [(0x0504, 2, b'Phones')])):
        metadata = bytes((group_id,)) + bytes(107)
        records = []
        for channel_id, logical, name in channels:
            channel = bytearray(88 if version == 2025 else 121)
            struct.pack_into('<HH', channel, 0, channel_id, logical)
            channel[4:5 + len(name)] = bytes((len(name),)) + name
            records.append(C9Channel(bytes(channel)))
        groups.append(C9Group(0xca, metadata, records))
    return C9Console(version, SocketGroups(b'', ()), SocketGroups(b'', ()), groups)


def _command(parameter, module, band, first, last, value):
    return struct.pack('<HHBBBBBiBfH', 20, 1, parameter, module, band, first, 1, last, 1, value, 0)


def _macro(commands=(), triggers=(), *, trigger_type=3, slot=11, name=b'Test'):
    header = bytearray(40)
    struct.pack_into('<HB', header, 0, 1, len(name))
    header[3:3 + len(name)] = name
    header[34:36] = bytes((trigger_type, slot))
    return (bytes(header) + struct.pack('<i', len(commands)) + b''.join(commands)
            + struct.pack('<i', len(triggers)) + b''.join(triggers))


def _macros(*records):
    return struct.pack('<Hi', 0xf8, len(records)) + b''.join(records) + struct.pack('<HH', 2, 0)


def _audio_sync(version, primary=0, secondary=0, use_secondary=0):
    width = {2025: 15, 2242: 17}[version]
    return struct.pack('<HHHB', width, primary, secondary, use_secondary) + bytes(width - 5)


_STRUCTURE = {'timecode_source_raw': 1, 'video_frame_rate': 25.0, 'sample_frequency': 48000.0}


class SesAutomationTests(unittest.TestCase):
    def test_macro_effects_use_logical_ranges_and_aux_id_bands(self):
        records = [_command(14, 1, 0, 9, 9, 1), _command(2, 8, 1, 9, 10, 0),
                   _command(2, 2, 4, 9, 10, 1)]
        data = _macros(_macro(records, name='測試'.encode('cp950')))
        report = inspect_automation({0xf8: data}, _topology(), _STRUCTURE)['macros']
        self.assertEqual(report['count'], 1)
        row = report['records'][0]
        self.assertEqual(row['name'], '測試')
        self.assertEqual(row['trigger'], {'type_raw': 3, 'slot_raw': 11, 'kind': 'smart_key',
                                          'action': 'on', 'bank': 2, 'key': 2})
        effects = [command['effect'] for command in row['commands']]
        self.assertEqual(effects, [
            {'operation': 'input_select', 'source': 'alt', 'input_logical_range': {'first': 9, 'last': 9}},
            {'operation': 'insert_in', 'insert': 'B', 'enabled': False,
             'input_logical_range': {'first': 9, 'last': 10}},
            {'operation': 'aux_send_on', 'aux_band': 4, 'enabled': True,
             'input_logical_range': {'first': 9, 'last': 10}},
        ])
        self.assertEqual(report['input_channels'][9]['channel_id'], 0x0120)
        self.assertEqual(report['aux_bands'][4], {'channel_id': 0x0504, 'name': 'Phones'})
        self.assertEqual(row['trigger_records'], [])

    def test_unknown_or_ambiguous_command_and_trigger_items_remain_raw(self):
        opaque = b'\x04\0\x01\x02'
        ambiguous = _command(14, 1, 0, 9, 10, 1)
        unknown_value = _command(2, 2, 4, 9, 9, 3)
        trigger = struct.pack('<H', 14) + bytes(range(12))
        data = _macros(_macro((opaque, ambiguous, unknown_value), (trigger,), trigger_type=0x51))
        report = inspect_automation({0xf8: data}, _topology(duplicate_input=True), _STRUCTURE)['macros']
        row = report['records'][0]
        self.assertNotIn(10, report['input_channels'])
        self.assertEqual(row['trigger']['kind'], 'snapshot')
        self.assertEqual(row['trigger_records'], [trigger.hex()])
        self.assertEqual(row['commands'], [
            {'raw_hex': entry.hex(), 'effect': None} for entry in (opaque, ambiguous, unknown_value)])

    def test_clocks_and_absent_macros(self):
        for version in (2025, 2242):
            with self.subTest(version=version):
                report = inspect_automation({0xe3: _audio_sync(version, 10, 14, 1)},
                                            _topology(version), _STRUCTURE)
                self.assertEqual(report['version'], version)
                self.assertIsNone(report['macros'])
                self.assertEqual(report['clock']['timecode'], {
                    'source': 'master', 'source_raw': 1, 'frames_per_second': 25.0})
                self.assertEqual(report['clock']['audio_sync'], {
                    'source': 'optocore', 'source_raw': 14, 'primary_source_raw': 10,
                    'secondary_source_raw': 14, 'select_secondary_raw': 1})
                self.assertEqual(report['clock']['sample_frequency'], 48000.0)

    def test_malformed_sections_fail_closed(self):
        for raw in (_macros(_macro())[:-1], _macros(_macro()) + b'\0',
                    _macros(_macro((b'\x01\0',))), _macros(_macro(name=b'A' * 32)),
                    struct.pack('<Hi', 0xf8, 0x7fffffff)):
            with self.subTest(raw=raw.hex()), self.assertRaises(SnapshotFormatError):
                inspect_automation({0xf8: raw}, _topology(), _STRUCTURE)
        with self.assertRaises(SnapshotFormatError):
            inspect_automation({0xe3: _audio_sync(2025)}, _topology(), _STRUCTURE)
        with self.assertRaises(SnapshotFormatError):
            inspect_automation({}, _topology(2126), _STRUCTURE)


if __name__ == '__main__':
    unittest.main()

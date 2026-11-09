from copy import deepcopy
import json
import struct
import unittest
from unittest.mock import patch

from test_ses_routing import _fixture, _module

from ses_signal import inspect_signal
from ses_snapshot import SnapshotFormatError, SnapshotSection


def _float_bits(value):
    return struct.unpack('<I', struct.pack('<f', value))[0]


def _fixture_with_signals():
    routing, _ = _fixture()
    routing.topology[0x100].set_name(b'Test Lead')
    routing.topology[0x501].set_name(b'Test Stereo Aux')
    raw = bytearray(routing.topology[0x501].metadata)
    raw[0x25] = 2
    routing.topology[0x501].metadata = bytes(raw)
    routing.console._channel(0x501).metadata.patch(38, b'\x02', 39)
    routing.topology[0x1c00].set_name(b'Mic 1')
    routing.topology[0x1c01].set_name(b'Mic 2')
    routing.topology[0x4e00].set_name(b'Line 1')
    routing.topology[0x600].set_name(b'Test Group')

    source = routing.console._channel(0x100)
    source.modules = [
        _module(1, [(0, 0, 0x1c000000), (0, 1, 0x1c010000), (0x0e, 0, 0)]),
        _module(2, [(1, 1, _float_bits(-5.5)), (2, 1, _float_bits(1)), (4, 1, _float_bits(1))]),
        _module(3, [(9, 0, _float_bits(1))]),
        _module(8, [(2, 0, _float_bits(1)), (0xe1, 0, _float_bits(4)),
                    (0xfe, 0, 0x4e000000), (0, 0, 0x1c000000), (2, 1, 0)])]
    snapshots = SnapshotSection.empty(2242)
    snapshots.create_from_current_console(routing.console.to_bytes())
    snapshots.rename(0, b'Opening')
    snapshots.set_number(0, 1)

    # Use real shared-module references in the second cue. Leave one record
    # absent so the comparison must distinguish no saved value from OFF.
    next_state = deepcopy(routing.console.snapshot)
    next_source = next(ch for ch in next_state.channels if ch.channel_id == 0x100)
    next_source.modules = [
        _module(1, [(0, 0, 0x1c000000), (0x0e, 0, _float_bits(1))]),
        _module(2, [(1, 1, _float_bits(-150)), (2, 1, 0), (4, 1, 0)]),
        _module(3, [(9, 0, _float_bits(1))]),
        _module(8, [(2, 0, 0), (0xe1, 0, _float_bits(4)), (0xfe, 0, 0), (0, 0, 0), (2, 1, 0)])]
    saved = next_source.modules[0]
    saved.controls = saved.controls[:11] + b'\x04' + saved.controls[12:]
    snapshots.modules.extend(next_source.modules)
    next_source.own_modules.data = b'\0'
    next_source.modules = list(range(len(snapshots.modules)))
    next_state.rename(b'Speech')
    snapshots.snapshots.append(next_state)
    snapshots.set_number(1, 2)
    snapshots.options.patch(0, struct.pack('<i', 0), 237)
    snapshots = SnapshotSection.from_bytes(snapshots.to_bytes())
    return snapshots, routing.console, routing.c9


class SesSignalTests(unittest.TestCase):
    def test_current_inserts_taps_and_stereo_aux(self):
        snapshots, console, topology = _fixture_with_signals()
        before = snapshots.to_bytes(), console.to_bytes(), topology.to_bytes()
        report = inspect_signal(snapshots, console, topology, channel_id=0x100)
        row, = report['current_channels']
        self.assertEqual(row['name'], 'Test Lead')
        self.assertEqual(row['main_alt']['selection']['value'], 'main')
        self.assertEqual(row['main_alt']['alt_route']['value'],
                         {'raw_uint32': 0x1c010000, 'channel_id': 0x1c01, 'name': 'Mic 2'})
        a, b = row['inserts']
        self.assertEqual(a['slot'], 'A')
        self.assertTrue(a['enabled']['value'])
        self.assertEqual(a['tap']['value'], 'pre_eq')
        self.assertEqual(a['send']['value']['name'], 'Line 1')
        self.assertEqual(a['return']['value']['name'], 'Mic 1')
        self.assertFalse(b['enabled']['value'])
        aux, = row['aux_sends']
        self.assertEqual(aux['destination_stereo_mode'], 2)
        self.assertEqual(aux['destination_name'], 'Test Stereo Aux')
        self.assertEqual(aux['level_db']['value'], -5.5)
        self.assertEqual(aux['tap']['value'], 'pre_fader')
        group, = row['group_sends']
        self.assertEqual(group['destination_channel_id'], 0x600)
        self.assertFalse(report['scope_inheritance_applied'])
        self.assertFalse(report['effective_recall_verified'])
        self.assertEqual((snapshots.to_bytes(), console.to_bytes(), topology.to_bytes()), before)
        json.dumps(report, allow_nan=False)

    def test_shared_cue_deltas_preserve_provenance_and_ignore_missing_records(self):
        snapshots, console, topology = _fixture_with_signals()
        report = inspect_signal(snapshots, console, topology, channel_id=0x100)
        cue, = report['cue_deltas']
        self.assertEqual(cue['cue'], {'index': 1, 'number': 2.0, 'name': 'Speech'})
        self.assertEqual(cue['before_only_field_count'], 1)
        self.assertEqual(cue['after_only_field_count'], 0)
        self.assertEqual(cue['change_count_by_section'], {'main_alt': 1, 'aux_send': 3, 'insert': 3})
        change = next(row for row in cue['changes'] if row['field'] == 'selection')
        self.assertEqual(change['after']['value'], 'alt')
        self.assertEqual(change['after']['flags'], 4)
        self.assertFalse(any(row['field'] == 'alt_route' for row in cue['changes']))
        self.assertEqual(report['current_vs_selected_cue']['change_count_by_section'], {})

    def test_detail_cap_preserves_complete_change_counts(self):
        snapshots, console, topology = _fixture_with_signals()
        with patch('ses_signal._MAX_TOTAL_CUE_DETAILS', 2):
            report = inspect_signal(snapshots, console, topology, channel_id=0x100)
        cue, = report['cue_deltas']
        self.assertEqual(len(cue['changes']), 2)
        self.assertTrue(all(row['section'] == 'insert' for row in cue['changes']))
        self.assertEqual(cue['omitted_change_details'], 5)
        self.assertEqual(sum(cue['change_count_by_section'].values()), 7)

    def test_group_to_group_uses_native_band_and_destination(self):
        snapshots, console, topology = _fixture_with_signals()
        groups = next(group for group in topology.groups if group.group_id == 6)
        destination = deepcopy(groups.channels[0])
        raw = bytearray(destination.metadata)
        struct.pack_into('<HH', raw, 0, 0x601, 2)
        raw[0x25] = 1
        struct.pack_into('<H', raw, 0x32, 0)
        destination.metadata = bytes(raw)
        destination.set_name(b'Submix')
        groups.channels.append(destination)
        source = console._channel(0x600)
        source.modules.append(_module(3, [(9, 1, _float_bits(1))]))
        row, = inspect_signal(snapshots, console, topology, channel_id=0x600)['current_channels']
        send, = row['group_sends']
        self.assertEqual((row['channel_type'], send['destination_channel_id'], send['destination_name']),
                         ('group', 0x601, 'Submix'))

    def test_bounds_and_invalid_inputs_fail_closed(self):
        snapshots, console, topology = _fixture_with_signals()
        with patch('ses_signal._MAX_RECORDS_SCANNED', 1):
            with self.assertRaisesRegex(SnapshotFormatError, 'record budget'):
                inspect_signal(snapshots, console, topology)
        with self.assertRaisesRegex(SnapshotFormatError, 'channel_id'):
            inspect_signal(snapshots, console, topology, channel_id=True)
        with self.assertRaisesRegex(SnapshotFormatError, 'matching'):
            inspect_signal(SnapshotSection.empty(2126), console, topology)

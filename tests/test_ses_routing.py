from pathlib import Path
import struct
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'digico-native-session' / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from ses_console import CurrentConsole
from ses_routing import (C9Channel, C9Console, C9Group, SesRouting, SesRoutingError,
                         SocketGroups, build_local_ports, read_io_devices, read_ports)
from ses_snapshot import ChannelSnapshot, ConsoleSnapshot, ModuleSnapshot, SnapshotFormatError, _Block


def _module(kind, records):
    header = bytearray(58)
    struct.pack_into('<i', header, 0, kind)
    struct.pack_into('<i', header, 44, len(records))
    header[53] = 1
    controls = b''.join(bytes((parameter, kind, band, 0)) + struct.pack('<I', raw)
                        for parameter, band, raw in records)
    return ModuleSnapshot(_Block(bytes(header)), controls, -1, -1, 0)


def _fixture(version=2242, devices=None):
    groups = {}
    mix = []

    def add(channel_id, stereo_mode, buss_id, modules):
        group_id = channel_id >> 8
        if group_id not in groups:
            group_metadata = bytearray(108)
            group_metadata[:6] = bytes((group_id, 4)) + b'Test'
            groups[group_id] = C9Group(0xca, bytes(group_metadata), [])
        metadata = bytearray(120 if version == 2126 else 121)
        struct.pack_into('<HH', metadata, 0, channel_id, (channel_id & 255) + 1)
        metadata[4:10] = b'\x05Track'
        metadata[0x25] = stereo_mode
        struct.pack_into('<iH', metadata, 0x26, -1, 0)
        struct.pack_into('<H', metadata, 0x32, buss_id)
        groups[group_id].channels.append(C9Channel(bytes(metadata)))
        snapshot_metadata = bytearray(39)
        struct.pack_into('<H', snapshot_metadata, 0, channel_id)
        snapshot_metadata[2:8] = b'\x05Track'
        struct.pack_into('<iB', snapshot_metadata, 34, -1, stereo_mode)
        mix.append(ChannelSnapshot(_Block(b'\1'), modules, _Block(bytes(snapshot_metadata))))

    for channel_id, mode in ((0x100, 1), (0x101, 1), (0x102, 2)):
        add(channel_id, mode, 0, [
            _module(1, [(0, 0, 0), (0, 1, 0), (1, 0, 0x3f000000)]),
            _module(2, [(1, 0, 0xc3160000), (2, 0, 0), (1, 1, 0xc3160000), (2, 1, 0)]),
            _module(3, [(9, 0, 0)]), _module(5, [(0x16, 0, 0)]),
            _module(8, [(0, 0, 0), (0, 1, 0), (0xfe, 0, 0), (0xfe, 1, 0)]),
            _module(0x0e, [(0xfe, 0, 0)])])
    for channel_id in (0x500, 0x501):
        add(channel_id, 1, 0, [_module(0x0c, [(0x21, 0, 0)])])
    for channel_id, mode, pair in ((0x600, 2, 0x700), (0x700, 1, 0x600)):
        add(channel_id, mode, pair, [_module(0x0c, [(0x21, 0, 0)]),
                                    _module(0x0e, [(0xfe, 0, 0)])])
    add(0x0f00, 1, 0, [_module(1, [(0, 0, 0), (0, 1, 0)]),
                        _module(6, [(0x24, 0, 0xc3160000), (0x25, 0, 0),
                                    (0x24, 1, 0xc3160000), (0x25, 1, 0)])])
    for channel_id in (0x1000, 0x1001):
        add(channel_id, 1, 0, [_module(0x0c, [(0x21, 0, 0)]),
                               _module(0x0e, [(0xfe, 0, 0)])])
    for channel_id in (0x1c00, 0x1c01):
        add(channel_id, 1, 0, [_module(0x11, [(0xfe, 0, 0)])])
    for channel_id in (0x4e00, 0x4e01, 0x4e02):
        add(channel_id, 1, 0, [_module(0x12, [(0, 0, 0)])])
    topology = C9Console(version, SocketGroups(b'Local In\r\n', (0x1c00,)),
                         SocketGroups(b'Local Out\r\n', (0x4e00,)), list(groups.values()))
    snapshot = ConsoleSnapshot(mix, _Block(b'Test'), bytes(8), _Block(b''), _Block(b''),
                               b'', _Block(b''), _Block(b''), _Block(b''), _Block(b''), _Block(b''))
    profile = 'quantum1-v2126' if version == 2126 else 'quantum5-v2242'
    ports = build_local_ports(profile)
    routing = SesRouting(topology, CurrentConsole(snapshot, version), ports, devices)
    return routing, ports


def _io_devices(input_types, output_types):
    metadata = struct.pack('<iiBBBB', len(input_types), len(output_types), 0x0f, 0, 0, 1)
    return (struct.pack('<HiHH', 0xfc, 1, 0xfb, len(metadata))
            + metadata + input_types + output_types)


class SesRoutingTests(unittest.TestCase):
    def test_topology_roundtrip_and_preserving_metadata_edits(self):
        for version in (2126, 2242):
            with self.subTest(version=version):
                routing, _ = _fixture(version)
                raw = routing.c9.to_bytes()
                topology = C9Console.from_bytes(raw, version)
                self.assertEqual(topology.to_bytes(), raw)
                channel = topology.groups[0].channels[0]
                before = channel.metadata
                channel.set_name(b'New').set_preset_index(2)
                self.assertEqual(channel.name_bytes, b'New')
                self.assertEqual(channel.preset_index, 2)
                self.assertEqual(channel.metadata[8:0x26], before[8:0x26])
                self.assertEqual(channel.metadata[0x2a:], before[0x2a:])
                with self.assertRaises(SnapshotFormatError):
                    C9Console.from_bytes(raw[:-1], version)

    def test_input_selectors_and_group_assignment_preserve_other_fields(self):
        routing, ports = _fixture()
        topology = routing.c9.to_bytes()
        physical = {'port_type': 1, 'port_number': 0, 'socket_index': 1}
        routing.set_input_route(0x100, physical)
        routing.set_input_route(0x100, 0x1c00, 'alt')
        routing.set_input_route(0x100, 0x1c01, 'insert_return', 1)
        routing.set_group_assignment(0x100, 0x600, True)
        reparsed = SesRouting(routing.c9, CurrentConsole.from_bytes(routing.console.to_bytes()), ports)
        view = reparsed.inspect(channel_id=0x100)['channels'][0]
        self.assertEqual(view['input_routes']['main'], 0x1c010000)
        self.assertEqual(view['input_routes']['alt'], 0x1c000000)
        self.assertEqual(view['input_routes']['insert_return_1'], 0x1c010000)
        self.assertEqual(view['group_assignments'], [0x600])
        self.assertEqual(reparsed.c9.to_bytes(), topology)
        reparsed.set_input_route(0x100, None)
        self.assertEqual(reparsed.inspect(channel_id=0x100)['channels'][0]['input_routes']['main'], 0)
        self.assertEqual(reparsed._control(0x100, (1, 1, 0)).raw, struct.pack('<f', .5))

    def test_c9_group_buss_matrix_extension_is_preserved(self):
        routing, _ = _fixture()
        matrix = (b'\xe7\0' + struct.pack('<i', 1) + b'\xe6\0'
                  + bytes(7) + struct.pack('<H', 96) + bytes(96)
                  + b'\x01' + struct.pack('<H', 4) + b'Test' + b'\x00')
        routing.topology[0x600].buss_matrix = matrix
        raw = routing.c9.to_bytes()
        restored = C9Console.from_bytes(raw)
        self.assertEqual(restored.to_bytes(), raw)
        group = next(group for group in restored.groups if group.group_id == 6)
        group.channels[0].set_preset_index(0)
        self.assertEqual(group.channels[0].buss_matrix, matrix)

    def test_stereo_input_main_and_alt_store_only_the_base_socket(self):
        for version in (2126, 2242):
            with self.subTest(version=version):
                routing, ports = _fixture(version)
                routing.set_input_route(0x102, 0x1c00)
                selector = {'port_type': 1, 'port_number': 0, 'socket_index': 0}
                routing.set_input_route(0x102, selector, 'alt')
                reparsed = SesRouting(routing.c9, CurrentConsole.from_bytes(routing.console.to_bytes(), version), ports)
                before = reparsed.console.to_bytes()
                for source, kind, message in ((0x1c01, 'main', 'consecutive'),
                                              (0x1c00, 'key', 'main and alt')):
                    with self.assertRaisesRegex(SesRoutingError, message):
                        reparsed.set_input_route(0x102, source, kind)
                    self.assertEqual(reparsed.console.to_bytes(), before)
                reparsed.set_input_route(0x102, None, 'alt')
                routes = reparsed.inspect(channel_id=0x102)['channels'][0]['input_routes']
                self.assertEqual(routes['main'], 0x1c000000)
                self.assertEqual(routes['alt'], 0)

    def test_stereo_input_refuses_mismatched_fitted_socket_types(self):
        routing, _ = _fixture(devices=_io_devices(b'\x33\x04', b''))
        before = routing.console.to_bytes()
        with self.assertRaisesRegex(SesRoutingError, 'different configured types'):
            routing.set_input_route(0x102, 0x1c00)
        self.assertEqual(routing.console.to_bytes(), before)

    def test_configured_devices_bound_fitted_endpoints(self):
        devices = _io_devices(b'\x33\x1b', b'\x33\x00')
        routing, _ = _fixture(devices=devices)
        self.assertEqual(read_io_devices(devices)[(1, 0)].input_types, b'\x33\x1b')
        before = routing.console.to_bytes()
        for action in (lambda: routing.set_input_route(0x100, 0x1c01),
                       lambda: routing.set_output_route(0x100, 0x4e01, True)):
            with self.assertRaisesRegex(SesRoutingError, 'unfitted'):
                action()
            self.assertEqual(routing.console.to_bytes(), before)

    def test_aux_send_sets_level_and_on_only_for_the_requested_band(self):
        routing, ports = _fixture()
        routing.set_aux_send(0x100, 0x501, -7.5, True)
        reparsed = SesRouting(routing.c9, CurrentConsole.from_bytes(routing.console.to_bytes()), ports)
        sends = reparsed.inspect(channel_id=0x100)['channels'][0]['aux_sends']
        self.assertEqual(sends, [{'aux_channel_id': 0x500, 'level': -150.0, 'enabled': False},
                                 {'aux_channel_id': 0x501, 'level': -7.5, 'enabled': True}])
        before = reparsed.console.to_bytes()
        for args in ((0x102, 0x500, -2.5, True), (0x100, 0x500, float('nan'), True),
                     (0x100, 0x500, 2.5, 1), (0x100, 0x600, 0, False)):
            with self.assertRaises(SesRoutingError):
                reparsed.set_aux_send(*args)
            self.assertEqual(reparsed.console.to_bytes(), before)
        reparsed._control(0x100, (2, 2, 1)).replace(struct.pack('<f', 2))
        before = reparsed.console.to_bytes()
        with self.assertRaisesRegex(SesRoutingError, 'Boolean encoding'):
            reparsed.set_aux_send(0x100, 0x501, -1, False)
        self.assertEqual(reparsed.console.to_bytes(), before)

    def test_output_ownership_and_disconnect_leave_other_owner_intact(self):
        routing, _ = _fixture()
        routing.set_output_route(0x100, 0x4e00, True)
        before = routing.console.to_bytes()
        with self.assertRaisesRegex(SesRoutingError, 'occupied'):
            routing.set_output_route(0x101, 0x4e00, True)
        routing.set_output_route(0x101, 0x4e00, False)
        self.assertEqual(routing.console.to_bytes(), before)
        routing.set_output_route(0x100, 0x4e00, False)
        self.assertEqual(routing.inspect(channel_id=0x100)['channels'][0]['physical_outputs'], [])

    def test_matrix_input_crosspoint_and_output_roundtrip(self):
        routing, ports = _fixture()
        routing.set_input_route(0x0f00, 0x1c01)
        routing.set_input_route(0x0f00, 0x1c00, 'alt')
        routing.set_matrix_send(0x0f00, 0x1001, -3.5, True)
        routing.set_output_route(0x1001, 0x4e02, True)
        restored = SesRouting(routing.c9, CurrentConsole.from_bytes(routing.console.to_bytes()), ports)
        inputs = restored.inspect(channel_id=0x0f00)['channels'][0]
        self.assertEqual(inputs['input_routes'], {'main': 0x1c010000, 'alt': 0x1c000000})
        self.assertEqual(inputs['matrix_sends'], [
            {'matrix_channel_id': 0x1000, 'level': -150.0, 'enabled': False},
            {'matrix_channel_id': 0x1001, 'level': -3.5, 'enabled': True},
        ])
        self.assertEqual(restored.inspect(channel_id=0x1001)['channels'][0]['physical_outputs'], [0x4e02])
        before = restored.console.to_bytes()
        with self.assertRaisesRegex(SesRoutingError, 'Matrix Input.*main and alt'):
            restored.set_input_route(0x0f00, 0x1c00, 'key')
        with self.assertRaisesRegex(SesRoutingError, 'occupied'):
            restored.set_output_route(0x1000, 0x4e02, True)
        group = next(group for group in restored.c9.groups if group.group_id == 0x10)
        group.channels.reverse()
        with self.assertRaisesRegex(SesRoutingError, 'band position'):
            restored.set_matrix_send(0x0f00, 0x1001, 0, False)
        self.assertEqual(restored.console.to_bytes(), before)

    def test_stereo_pair_keeps_right_leg_when_left_disconnects(self):
        routing, ports = _fixture()
        routing.set_output_route(0x600, 0x4e00, True, leg='left')
        routing.set_output_route(0x600, 0x4e01, True, leg='right')
        routing._control(0x600, (0xfe, 0x0e, 0)).replace(struct.pack('<I', 0x4e000000))
        routing._control(0x700, (0xfe, 0x0e, 0)).replace(struct.pack('<I', 0x4e010000))
        routing.set_output_route(0x600, 0x4e00, False, leg='left')
        result = SesRouting(routing.c9, CurrentConsole.from_bytes(routing.console.to_bytes()), ports)
        routes = result.inspect(channel_id=0x600)['channels'][0]['output_legs']
        self.assertEqual(routes, {'left': [], 'right': [0x4e01], 'right_channel_id': 0x700})
        self.assertEqual(result._control(0x4e01, (0, 0x12, 0)).route, 0x07000000)
        for owner in (0x600, 0x700):
            self.assertEqual(result._control(owner, (0xfe, 0x0e, 0)).route, 0)

    def test_stereo_and_route_binding_guards_are_atomic(self):
        routing, _ = _fixture()
        before = routing.console.to_bytes()
        with self.assertRaisesRegex(SesRoutingError, 'requires a left or right'):
            routing.set_output_route(0x600, 0x4e00, True)
        with self.assertRaisesRegex(SesRoutingError, 'applies only'):
            routing.set_output_route(0x100, 0x4e00, True, leg='right')
        routing.topology[0x700].metadata = (routing.topology[0x700].metadata[:0x32]
                                           + b'\0\0' + routing.topology[0x700].metadata[0x34:])
        with self.assertRaisesRegex(SesRoutingError, 'reciprocal'):
            routing.set_output_route(0x600, 0x4e00, True, leg='right')
        self.assertEqual(routing.console.to_bytes(), before)

    def test_cross_section_identity_and_local_port_metadata(self):
        routing, ports = _fixture()
        parsed = read_ports(ports)
        self.assertEqual(len(parsed), 50)
        self.assertEqual(parsed[0][-2:], b'\x01\0')
        self.assertTrue(all(not any(row) for row in parsed[1:]))
        routing.console.snapshot.channels.pop()
        with self.assertRaisesRegex(SesRoutingError, 'IDs or stereo modes disagree'):
            SesRouting(routing.c9, routing.console, ports)


if __name__ == '__main__':
    unittest.main()

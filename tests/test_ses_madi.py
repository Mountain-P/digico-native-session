from pathlib import Path
import struct
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'digico-native-session' / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from ses_console import CurrentConsole
from ses_madi import add_q5_madi_port1
from ses_routing import C9Console, SesRouting, SesRoutingError, build_local_ports, read_ports
from ses_structure import StructureError, build_structure


class SesMadiTests(unittest.TestCase):
    def test_q5_first_madi_slot_socket_layout_and_patch(self):
        profile = 'quantum5-v2242'
        structure = build_structure(profile, {'inputs': {'mono': 1}})
        local_ports = build_local_ports(profile)
        ports = add_q5_madi_port1(structure, local_ports)
        rows = read_ports(ports)
        self.assertEqual(rows[0], read_ports(local_ports)[0])
        self.assertEqual(rows[1][-2:], b'\x02\0')
        self.assertEqual(rows[1][0x08:0x38:6], bytes([0x16] * 7 + [0]))
        self.assertEqual(rows[1][0x38:0x68:6], bytes([1, 1, 1, 3, 3, 8, 8, 0]))
        self.assertTrue(all(not any(row) for row in rows[2:]))

        topology = C9Console.from_bytes(structure.c9)
        inputs, outputs = topology.groups[4:6]
        self.assertEqual((inputs.group_id, outputs.group_id), (0x1D, 0x4F))
        self.assertEqual((len(inputs.channels), len(outputs.channels)), (56, 56))
        self.assertEqual(inputs.channels[-1].name_bytes, b'1:Mic 56')
        self.assertEqual(outputs.channels[24].name_bytes, b'1:AES out 1')
        self.assertEqual(outputs.channels[-1].name_bytes, b'1:Aviom 16')
        self.assertEqual(topology.input_groups.bank_name(0x1D30), b'Mic 49-56')
        self.assertEqual(topology.output_groups.bank_name(0x4F30), b'Aviom 9-16')
        self.assertIsNone(topology.output_groups.bank_name(0x4F38))

        routing = SesRouting(topology, CurrentConsole.from_bytes(structure.d3), ports,
                             struct.pack('<Hi', 0xFC, 0))
        endpoint = {'port_type': 2, 'port_number': 0, 'socket_index': 55}
        routing.set_input_route(0x0100, endpoint)
        routing.set_output_route(0x0100, endpoint, True)
        result = routing.inspect(channel_id=0x0100)['channels'][0]
        self.assertEqual(result['input_routes']['main'], 0x1D370000)
        self.assertEqual(result['physical_outputs'], [0x4F37])
        with self.assertRaises(SesRoutingError):
            routing.set_input_route(0x0100, {**endpoint, 'socket_index': 56})
        with self.assertRaises(StructureError):
            add_q5_madi_port1(structure, local_ports)


if __name__ == '__main__':
    unittest.main()

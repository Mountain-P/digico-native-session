"""Synthetic routing contracts. No device, VM, target process or network."""
import copy
import importlib.util
from pathlib import Path
import struct
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/digico-native-session/scripts/routing_catalog.py'
spec = importlib.util.spec_from_file_location('routing_catalog', SCRIPT)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def inventory():
    definition = bytearray(58)
    definition[0] = 2
    definition[7:20] = b'\x0csource route'
    port = bytearray(0xd2)
    port[0xd0:0xd2] = bytes((5, 2))
    return {'schema_version': 1, 'executable_sha256': r.TARGET_SHA256,
        'memory_read_only': True, 'structure_verified': True, 'io_inventory_complete': True,
        'channels': [
            {'id': 0x0100, 'pointer': 0x10000000, 'logical_channel': 1, 'stereo_mode': 1, 'input_route': 0},
            {'id': 0x1e00, 'pointer': 0x10001000, 'logical_channel': 1, 'name': 'Synthetic Source'}],
        'modules': [
            {'pointer': 0x20000000, 'parent_pointer': 0x10000000, 'class_name': 'tInputModule',
             'vmt_rva': r.INPUT_VMT_RVA, 'n_signals': 1, 'raw_id_hex': '0001000001'},
            {'pointer': 0x20001000, 'parent_pointer': 0x10001000, 'class_name': 'tInputSocketModule',
             'vmt_rva': r.INPUT_SOCKET_VMT_RVA, 'n_signals': 1, 'raw_id_hex': '002000001e',
             'listen_to_copy_control_pointer': 0x30001000}],
        'controllers': [{'pointer': 0x30000000, 'raw_id_hex': '0001000001', 'data_type': 2,
            'definition_pointer': 0xcd16c0, 'definition_rva': r.SOURCE_DEFINITION_RVA,
            'definition_raw_hex': definition.hex(), 'definition_binding_verified': True,
            'module_vmt_rva': r.INPUT_VMT_RVA, 'process_output_rva': r.INPUT_CALLBACK_RVA,
            'owner_module_pointer': 0x20000000, 'module_id_hex': '0001000001',
            'value_raw_hex': '00000000', 'target_raw_hex': '00000000', 'output_value_raw_hex': '00000000'},
            {'pointer': 0x30001000, 'raw_id_hex': 'eb2000001e', 'owner_module_pointer': 0x20001000,
             'target_raw_hex': '00000000'}],
        'ports': [{'index': 2, 'pointer': 0xd112c0, 'configuration_group_id': 30,
            'port_type': 5, 'port_number': 2, 'raw_hex': port.hex()}],
        'io_devices': [{'role': 'session_io_configuration', 'initialized': True,
            'devices': [{'pointer': 0x40000000, 'port_type': 5, 'port_number': 2,
                'input_count': 1, 'input_socket_types_hex': '33'}]}],
        'socket_groups': [{'role': 'session_socket_groups', 'initialized': True,
            'groups': [{'object_value_raw': 0x1e00, 'name': 'Synthetic Bank'}]}]}


def with_output(inv=None):
    inv = inventory() if inv is None else inv
    inv['channels'][0]['output_sockets'] = {'value': [], 'Count': 0}
    inv['channels'].append({'id': 0x5000, 'pointer': 0x10002000, 'name': 'Synthetic Output'})
    inv['modules'] += [
        {'pointer': 0x20002000, 'parent_pointer': 0x10000000, 'class_name': 'tOutputModule',
         'vmt_rva': 0x81a3cc, 'n_signals': 1, 'raw_id_hex': '000e000001'},
        {'pointer': 0x20003000, 'parent_pointer': 0x10002000, 'class_name': 'tOutputSocketModule',
         'vmt_rva': r.OUTPUT_SOCKET_VMT_RVA, 'n_signals': 1, 'raw_id_hex': '0012000050',
         'route_control_pointer': 0x30003000, 'socket_active': True}]
    raw = bytearray(58);raw[0] = 2;raw[6] = 0xfe
    inv['controllers'] += [
        {'pointer': 0x30002000, 'raw_id_hex': 'fe0e000001', 'data_type': 2,
         'definition_raw_hex': raw.hex(), 'definition_rva': 0x8d738f, 'definition_pointer': 0xcd738f,
         'module_vmt_rva': 0x81a3cc, 'process_output_rva': 0x824f9c,
         'owner_module_pointer': 0x20002000, 'module_id_hex': '000e000001', 'definition_binding_verified': True,
         'value_raw_hex': '00000000', 'target_raw_hex': '00000000', 'output_value_raw_hex': '00000000'},
        {'pointer': 0x30003000, 'raw_id_hex': '0012000050', 'data_type': 2,
         'owner_module_pointer': 0x20003000, 'output_value_raw_hex': '00000000'}]
    inv['ports'][0]['runtime_group_id'] = 80
    device = inv['io_devices'][0]['devices'][0]
    device['output_count'] = 1;device['output_socket_types_hex'] = '33'
    inv['socket_groups'].append({'role': 'runtime_socket_groups', 'initialized': True,
        'groups': [{'object_value_raw': 0x5000, 'name': 'Synthetic Output Bank'}]})
    return inv


def with_profile(kind, insert_index=0):
    inv = with_output() if kind == 'insert_send' else inventory()
    profile = r.SOURCE_PROFILES[kind]
    band = insert_index if kind.startswith('insert_') else profile['band']
    owner_band = insert_index if kind.startswith('insert_') else profile.get('owner_band', 0)
    owner_id = bytes((0, profile['module'], owner_band, 0, 1)).hex()
    inv['modules'][0].update(class_name=profile['class'], raw_id_hex=owner_id, vmt_rva=profile['vmt'])
    raw = bytearray(58);raw[0] = 2;raw[5] = profile.get('definition_band', band);raw[6] = profile['parameter']
    inv['controllers'][0].update(raw_id_hex=bytes((profile['parameter'], profile['module'], band, 0, 1)).hex(),
        module_id_hex=owner_id, definition_raw_hex=raw.hex(), definition_rva=profile['definition'],
        module_vmt_rva=profile['vmt'], process_output_rva=profile['callback'])
    if profile['field']:
        inv['channels'][0][profile['field']] = 0
    return inv


def with_group():
    inv = inventory()
    bits = bytearray(32);bits[0] = 8;bits[31] = 0x80
    inv['channels'][0]['buss_routes_hex'] = bits.hex()
    inv['channels'].append({'id': 0x603, 'pointer': 0x10003000, 'stereo_mode': 2, 'buss_id': 12})
    inv['modules'][0].update(class_name='tGroupSendModule', vmt_rva=0x81d950, raw_id_hex='0003000001')
    inv['modules'].append({'pointer': 0x20004000, 'parent_pointer': 0x10003000, 'class_name': 'tFaderModule',
        'vmt_rva': 0x81e510, 'n_signals': 2, 'raw_id_hex': '000c000306'})
    raw = bytearray(58);raw[5] = 3;raw[6] = 9
    inv['controllers'][0].update(raw_id_hex='0903030001', data_type=0, definition_raw_hex=raw.hex(),
        definition_pointer=0x60000000, definition_rva=None, module_vmt_rva=0x81d950,
        process_output_rva=0x83042c, module_id_hex='0003000001', value_raw_hex='0000803f',
        target_raw_hex='0000803f', output_value_raw_hex='0000803f')
    return inv


class RoutingContracts(unittest.TestCase):
    def test_domain_matches_code_port_device_channel_and_bank(self):
        cat = r.build_routing_catalog(inventory())
        self.assertEqual(len(cat['sources']), 1)
        source = cat['sources'][0]
        self.assertEqual((source['port_type'], source['port_number'], source['socket_index']), (5, 2, 0))
        self.assertEqual(source['route_raw_hex'], '0000001e')
        self.assertEqual(source['slot_in_bank'], 1)
        self.assertFalse(cat['runtime_acceptance_verified'])

    def test_main_route_payload_is_raw_int_bits_not_numeric_float(self):
        plan = r.plan_set_input_route(inventory(), 0x0100, {'port_type': 5, 'port_number': 2, 'socket_index': 0})
        self.assertEqual(plan['payload_hex'], '0000001e')
        self.assertNotEqual(plan['payload_hex'], struct.pack('<f', 0x1e000000).hex())
        self.assertEqual(plan['postcondition']['channel_route_value'], 0x1e000000)
        self.assertEqual(plan['raw_id_hex'], '0001000001')
        self.assertEqual(plan['id'], '0001000001')
        self.assertEqual(plan['control_contract']['raw_type'], 2)
        self.assertEqual(plan['control_contract']['control_pointer'], 0x30000000)
        self.assertEqual(plan['expected_input_route'], 0)
        self.assertFalse(plan['audio_delivery_verified'])

    def test_zero_disconnect_has_specific_semantics(self):
        inv = inventory()
        inv['channels'][0]['input_route'] = 0x1e000000
        inv['controllers'][0]['output_value_raw_hex'] = '0000001e'
        inv['controllers'][0]['value_raw_hex'] = '0000001e'
        inv['controllers'][0]['target_raw_hex'] = '0000001e'
        plan = r.plan_set_input_route(inv, 0x0100, None)
        self.assertEqual(plan['payload_hex'], '00000000')
        self.assertEqual(plan['expected_payload_hex'], '0000001e')

    def test_alt_uses_its_own_band_definition_and_channel_field(self):
        inv = inventory();inv['channels'][0]['alt_input_route'] = 0
        control = inv['controllers'][0];control['raw_id_hex'] = '0001010001'
        raw = bytearray.fromhex(control['definition_raw_hex']);raw[5] = 1
        control['definition_raw_hex'] = raw.hex();control['definition_rva'] = 0x8d1bf0
        plan = r.plan_input_route(inv, 0x0100, None, route_kind='alt')
        self.assertEqual(plan['id'], '0001010001')
        self.assertEqual(plan['postcondition']['channel_route_field'], 'alt_input_route')

    def test_key_and_insert_return_have_separate_code_and_postcondition_bindings(self):
        for kind in ('key', 'mustard_key', 'insert_return'):
            indices = (0, 1) if kind == 'insert_return' else (0,)
            for index in indices:
                inv = with_profile(kind, index)
                plan = r.plan_input_route(inv, 0x0100, None, route_kind=kind, insert_index=index)
                self.assertEqual(plan['route_kind'], kind)
                self.assertEqual(plan['control_contract']['owner_controller_offset'], r.SOURCE_PROFILES[kind]['slot'])
                self.assertEqual(plan['postcondition']['channel_route_field'], 'key_route' if kind == 'key' else None)

    def test_output_positive_add_negative_remove_and_complete_set(self):
        endpoint = {'port_type': 5, 'port_number': 2, 'socket_index': 0}
        inv = with_output()
        plan = r.plan_output_route(inv, 0x0100, endpoint, True)
        self.assertEqual(plan['control_contract']['payload_hex'], '00000050')
        self.assertEqual(plan['route_contract']['expected_output_sockets'], [0x50000000])
        self.assertEqual(plan['route_contract']['target_source_raw_hex'], '00000001')
        inv['channels'][0]['output_sockets'] = [0x50000000]
        inv['controllers'][-1]['output_value_raw_hex'] = '00000001'
        plan = r.plan_output_route(inv, 0x0100, endpoint, False)
        self.assertEqual(plan['control_contract']['payload_hex'], '000000b0')
        self.assertEqual(plan['route_contract']['expected_output_sockets'], [])
        self.assertEqual(plan['route_contract']['target_source_raw_hex'], '00000000')

    def test_output_refuses_steal_and_does_not_clear_someone_else_on_noop_disconnect(self):
        endpoint = {'port_type': 5, 'port_number': 2, 'socket_index': 0}
        inv = with_output();inv['controllers'][-1]['output_value_raw_hex'] = '00000101'
        with self.assertRaisesRegex(r.RoutingError, 'occupied'):
            r.plan_output_route(inv, 0x0100, endpoint, True)
        plan = r.plan_output_route(inv, 0x0100, endpoint, False)
        self.assertTrue(plan['route_contract']['no_change'])
        self.assertEqual(plan['route_contract']['target_source_raw_hex'], '00000101')

    def test_removed_output_command_may_settle_to_zero_only_with_exact_semantic_state(self):
        endpoint = {'port_type': 5, 'port_number': 2, 'socket_index': 0}
        inv = with_output();inv['channels'][0]['output_sockets'] = [0x50000000]
        inv['controllers'][-1]['output_value_raw_hex'] = '00000001'
        plan = r.plan_output_route(inv, 0x0100, endpoint, False)
        current = copy.deepcopy(inv);current['channels'][0]['output_sockets'] = []
        current['controllers'][-1]['output_value_raw_hex'] = '00000000'
        proof = r.verify_output_route_postcondition(plan, current)
        self.assertTrue(proof['verified'])
        self.assertEqual(proof['command_state'], 'cleared_after_remove')
        self.assertFalse(proof['replayed'])
        bad = copy.deepcopy(current);bad['channels'][0]['output_sockets'] = [0x50000000]
        self.assertFalse(r.verify_output_route_postcondition(plan, bad)['verified'])
        bad = copy.deepcopy(current);bad['controllers'][-1]['output_value_raw_hex'] = '00000101'
        self.assertFalse(r.verify_output_route_postcondition(plan, bad)['verified'])
        bad = copy.deepcopy(current);bad['controllers'][-2]['target_raw_hex'] = '0000803f'
        self.assertFalse(r.verify_output_route_postcondition(plan, bad)['verified'])

    def test_positive_output_command_does_not_get_the_remove_zero_exception(self):
        endpoint = {'port_type': 5, 'port_number': 2, 'socket_index': 0}
        inv = with_output();plan = r.plan_output_route(inv, 0x0100, endpoint, True)
        inv['channels'][0]['output_sockets'] = [0x50000000]
        inv['controllers'][-1]['output_value_raw_hex'] = '00000001'
        self.assertFalse(r.verify_output_route_postcondition(plan, inv)['verified'])

    def test_inactive_output_socket_cannot_enter_the_domain(self):
        inv = with_output();inv['modules'][-1]['socket_active'] = False
        self.assertEqual(r.build_routing_catalog(inv, direction='output')['destinations'], [])

    def test_insert_send_uses_module_and_band_suffix_at_output_source(self):
        endpoint = {'port_type': 5, 'port_number': 2, 'socket_index': 0}
        for index, suffix in ((0, '08000001'), (1, '08100001')):
            inv = with_profile('insert_send', index)
            plan = r.plan_input_route(inv, 0x0100, endpoint, route_kind='insert_send', insert_index=index)
            self.assertEqual(plan['control_contract']['payload_hex'], '00000050')
            self.assertEqual(plan['route_contract']['target_source_raw_hex'], suffix)
            for field in ('value_raw_hex', 'target_raw_hex', 'output_value_raw_hex'):
                inv['controllers'][0][field] = '00000050'
            inv['controllers'][-1]['output_value_raw_hex'] = suffix
            clear = r.plan_input_route(inv, 0x0100, None, route_kind='insert_send', insert_index=index)
            self.assertEqual(clear['control_contract']['payload_hex'], '00000000')
            self.assertEqual(clear['route_contract']['previous_endpoint']['source_channel_id'], 0x5000)

    def test_group_assignment_uses_float_and_changes_exactly_one_of_256_bits(self):
        inv = with_group()
        plan = r.plan_group_assignment(inv, 0x100, 0x603, False)
        self.assertEqual(plan['id'], '0903030001')
        self.assertEqual(plan['control_contract']['payload_type'], 'float32_discrete')
        self.assertEqual(plan['control_contract']['payload_hex'], '00000000')
        self.assertIsNone(plan['control_contract']['definition_rva'])
        before = bytes.fromhex(plan['group_contract']['expected_buss_routes_hex'])
        after = bytes.fromhex(plan['group_contract']['post_buss_routes_hex'])
        self.assertEqual([a ^ b for a, b in zip(before, after)], [8] + [0] * 31)
        self.assertEqual(after[31], 0x80)
        self.assertEqual(plan['group_contract']['target_n_signals'], 2)

    def test_group_assignment_requires_real_target_and_consistent_existing_bit(self):
        for field, value in [('id', 0x604), ('stereo_mode', 3)]:
            inv = with_group();inv['channels'][-1][field] = value
            with self.assertRaises(r.RoutingError):
                r.plan_group_assignment(inv, 0x100, 0x603, False)
        inv = with_group();inv['channels'][0]['buss_routes_hex'] = '00' * 32
        with self.assertRaisesRegex(r.RoutingError, 'bit disagree'):
            r.plan_group_assignment(inv, 0x100, 0x603, False)
        with self.assertRaises(r.RoutingError):
            r.plan_group_assignment(with_group(), 0x100, 0x603, 1)

    def test_unfitted_socket_and_missing_native_source_are_excluded(self):
        for bad_type in ('00', '1b'):
            inv = inventory();inv['io_devices'][0]['devices'][0]['input_socket_types_hex'] = bad_type
            self.assertEqual(r.build_routing_catalog(inv)['sources'], [])
        inv = inventory();inv['modules'][1]['vmt_rva'] += 4
        self.assertEqual(r.build_routing_catalog(inv)['sources'], [])

    def test_second_bank_uses_native_socket_index_without_human_one_offset(self):
        inv = inventory()
        inv['channels'][1]['id'] = 0x1e08
        inv['modules'][1]['raw_id_hex'] = '002000081e'
        inv['io_devices'][0]['devices'][0]['input_count'] = 9
        inv['io_devices'][0]['devices'][0]['input_socket_types_hex'] = '00' * 8 + '33'
        inv['socket_groups'][0]['groups'][0] = {'object_value_raw': 0x1e08, 'name': 'Synthetic Bank 2'}
        plan = r.plan_set_input_route(inv, 0x0100, {'port_type': 5, 'port_number': 2, 'socket_index': 8})
        self.assertEqual(plan['payload_hex'], '0000081e')
        self.assertEqual(plan['source']['slot_in_bank'], 1)
        inv = inventory();inv['channels'].pop()
        self.assertEqual(r.build_routing_catalog(inv)['sources'], [])
        inv = inventory();inv['controllers'][1]['target_raw_hex'] = '0000803f'
        self.assertEqual(r.build_routing_catalog(inv)['sources'], [])

    def test_runtime_output_bank_does_not_substitute_for_configuration(self):
        inv = inventory();inv['socket_groups'][0]['groups'][0]['object_value_raw'] = 0x5000
        self.assertEqual(r.build_routing_catalog(inv)['sources'], [])
        with self.assertRaises(r.RoutingError):
            r.plan_set_input_route(inv, 0x0100, {'port_type': 5, 'port_number': 2, 'socket_index': 0})

    def test_wrong_port_copy_and_duplicate_binding_are_rejected(self):
        inv = inventory();inv['ports'][0]['port_number'] = 3
        with self.assertRaisesRegex(r.RoutingError, 'copied native record'):
            r.build_routing_catalog(inv)
        inv = inventory();inv['io_devices'][0]['devices'].append(copy.deepcopy(inv['io_devices'][0]['devices'][0]))
        with self.assertRaisesRegex(r.RoutingError, 'ambiguous'):
            r.build_routing_catalog(inv)

    def test_definition_callback_and_owner_must_all_bind(self):
        for field, value in [('definition_rva', 1), ('process_output_rva', 2),
                ('owner_module_pointer', 3), ('definition_binding_verified', False), ('module_id_hex', '0002000001')]:
            inv = inventory();inv['controllers'][0][field] = value
            with self.assertRaises(r.RoutingError):
                r.plan_set_input_route(inv, 0x0100, None)
        inv = inventory();inv['channels'][0]['input_route'] = 1
        with self.assertRaisesRegex(r.RoutingError, 'previous main route'):
            r.plan_set_input_route(inv, 0x0100, None)

    def test_arbitrary_int_and_stereo_destination_are_rejected(self):
        for source in (12345, {'route': 0x1e000000}, {'port_type': 5, 'port_number': 2, 'socket_index': 1}):
            with self.assertRaises(r.RoutingError):
                r.plan_set_input_route(inventory(), 0x0100, source)
        inv = inventory();inv['channels'][0]['stereo_mode'] = 2
        with self.assertRaisesRegex(r.RoutingError, 'mono'):
            r.plan_set_input_route(inv, 0x0100, None)

    def test_unverified_target_cannot_supply_a_domain(self):
        for key in ('memory_read_only', 'structure_verified', 'io_inventory_complete'):
            inv = inventory();inv[key] = False
            with self.assertRaises(r.RoutingError):
                r.build_routing_catalog(inv)


if __name__ == '__main__':
    unittest.main()

"""Synthetic semantic authoring contracts; no VM, socket, UI, or private show."""
import copy
from pathlib import Path
import struct
import sys
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/digico-native-session/scripts'
sys.path.insert(0, str(SCRIPTS))
import authoring as a
import control_catalog as catalog
import native_session as native
import test_control_catalog as vectors


def with_channels(inventory):
    inventory['channels'] = [{'id': 0x0100, 'channel_type': 1, 'logical_channel': 1,
                              'pointer': 0x900000, 'name': 'Synthetic Vocal',
                              'name_hex': 'Synthetic Vocal'.encode().hex(), 'name_decoded': True}]
    inventory['controllers'][0].setdefault('owner_module_pointer', 0x700000)
    inventory['controllers'][0].setdefault('pointer', 0x800000)
    return inventory


def request(*operations, encoding='ascii'):
    return {'schema_version': 1, 'kind': 'authoring', 'fps': 25,
            'encoding': encoding, 'expected_count': 0, 'operations': list(operations)}


def setting(parameter='fader', value=-10, unit='native', channel=None, **extra):
    return {'op': 'set', 'channel': channel or {'type': 1, 'logical': 1},
            'parameter': parameter, 'value': {'value': value, 'unit': unit}, **extra}


def delay_inventory(parameter=8):
    inventory = with_channels(vectors.inventory(1, parameter, 9))
    inventory['controllers'][0].update(owner_module_pointer=0x700000,
                                      module_vmt_rva=0x81B2F4, process_output_rva=0x828640,
                                      value_raw_hex=struct.pack('<f', 0.).hex())
    seed, points = vectors.seed(((0, 0.), (255, 1.2999999523162842)))
    raw = bytearray.fromhex(seed)
    raw[1:32] = bytes.fromhex(catalog.TIME_UNIT_DESCRIPTOR)
    inventory['definitions'][0].update(response_seed_raw_hex=raw.hex(), response_points=points)
    return inventory


def eq_inventory(shape=1):
    inventory = with_channels(vectors.eq_context_inventory(shape=shape))
    inventory['controllers'][0]['value_raw_hex'] = struct.pack('<f', .5).hex()
    return inventory


def add_name_controller(inventory):
    raw = vectors.definition(4, 255, name='name', units='')
    inventory['controllers'].append({'pointer': 0x800300, 'raw_id_hex': 'ff01000001',
                                     'definition_raw_hex': raw, 'definition_pointer': 0x500300,
                                     'module_vmt_rva': 0x1000, 'process_output_rva': 0x2000,
                                     'owner_module_pointer': 0x700000})
    inventory['definitions'].append({'pointer': 0x500300, 'raw_hex': raw})
    return inventory


class AuthoringTests(unittest.TestCase):
    def assert_code(self, code, request, inventory):
        with self.assertRaises(a.AuthoringError) as caught:
            a.compile_authoring_request(request, inventory)
        self.assertEqual(caught.exception.code, code)
        return caught.exception.as_dict()

    def test_logical_input_one_resolves_native_256_and_strict_raw_request(self):
        inventory = with_channels(vectors.inventory())
        plan = a.compile_authoring_request(request(setting()), inventory)
        raw = plan['stages'][0]['raw_request']
        self.assertEqual(raw['operations'], [{'op': 'set_control', 'id': '210c000001',
                                             'value': -10., 'expected': -20.}])
        compiled = native.compile_request(raw, inventory=inventory)
        self.assertEqual(compiled['steps'][0]['control_contract']['payload_hex'], '000020c1')
        self.assertEqual(compiled['control_inventory_binding']['inventory_sha256'],
                         plan['inventory_binding']['inventory_sha256'])
        self.assertEqual(plan['changes'][0]['channel']['id'], 256)
        self.assertTrue(plan['fully_compiled'])
        self.assertFalse(plan['values_atomic'])
        self.assertFalse(plan['runtime_acceptance_verified'])

    def test_preview_is_pure_and_sequential_expectations_use_encoded_values(self):
        inventory = with_channels(vectors.inventory())
        document = request(setting(value=-10), setting(value=-10), setting(value=-5))
        original_inventory, original_request = copy.deepcopy(inventory), copy.deepcopy(document)
        plan = a.preview_authoring_request(document, inventory)
        self.assertEqual([op['expected'] for op in plan['stages'][0]['raw_request']['operations']], [-20., -10., -10.])
        self.assertEqual([row['changed'] for row in plan['changes']], [True, False, True])
        self.assertEqual(document, original_request)
        self.assertEqual(inventory, original_inventory)

    def test_same_logical_number_in_different_native_types_is_ambiguous(self):
        inventory = with_channels(vectors.inventory())
        inventory['channels'].append({'id': 0x0600, 'channel_type': 6, 'logical_channel': 1, 'name': 'Synthetic Group'})
        diagnostic = self.assert_code('ambiguous_channel', request(setting(channel={'logical': 1})), inventory)
        self.assertEqual([c['id'] for c in diagnostic['details']['candidates']], [256, 1536])
        a.compile_authoring_request(request(setting(channel={'logical': 1, 'type': 1})), inventory)

    def test_exact_name_duplicates_unknown_name_and_undecoded_names(self):
        inventory = with_channels(vectors.inventory())
        inventory['channels'].append({'id': 0x0101, 'channel_type': 1, 'logical_channel': 2, 'name': 'Synthetic Vocal'})
        self.assert_code('ambiguous_channel', request(setting(channel={'name': 'Synthetic Vocal'})), inventory)
        self.assert_code('unknown_channel', request(setting(channel={'name': 'synthetic vocal'})), inventory)
        inventory['channels'][1]['name_decoded'] = False
        result = a.compile_authoring_request(request(setting(channel={'name': 'Synthetic Vocal'})), inventory)
        self.assertEqual(result['changes'][0]['channel']['id'], 256)

    def test_exact_definition_name_and_band_resolve_repeated_eq_controls(self):
        inventory = with_channels(vectors.inventory(1, 5, 4, 0))
        second = copy.deepcopy(inventory['controllers'][0])
        second.update(raw_id_hex='0504010001', definition_pointer=0x500200, pointer=0x800100,
                      definition_raw_hex=vectors.definition(1, 5, 1))
        copied = copy.deepcopy(inventory['definitions'][0])
        copied.update(pointer=0x500200, raw_hex=second['definition_raw_hex'])
        inventory['controllers'].append(second)
        inventory['definitions'].append(copied)
        diagnostic = self.assert_code('ambiguous_parameter', request(setting('eq.frequency', 0)), inventory)
        self.assertEqual([c['band'] for c in diagnostic['details']['candidates']], [0, 1])
        plan = a.compile_authoring_request(request(setting('native:synthetic', 0, band=1)), inventory)
        self.assertEqual(plan['stages'][0]['raw_request']['operations'][0]['id'], '0504010001')

    def test_explicit_milliseconds_convert_only_verified_seconds_and_keep_f32(self):
        inventory = delay_inventory()
        plan = a.compile_authoring_request(request(setting('delay.time', 5, 'ms')), inventory)
        raw = plan['stages'][0]['raw_request']
        compiled = native.compile_request(raw, inventory=inventory)
        self.assertEqual(compiled['steps'][0]['control_contract']['payload_hex'], struct.pack('<f', .005).hex())
        self.assertAlmostEqual(raw['operations'][0]['value'], .005)
        self.assertEqual(plan['changes'][0]['intended'], {'value': 5, 'unit': 'ms'})
        self.assertEqual(plan['changes'][0]['intended_native']['unit'], 'native')

    def test_converted_bounds_apply_to_encoded_native_values(self):
        inventory = delay_inventory()
        a.compile_authoring_request(request(setting('delay.time', 1300, 'ms')), inventory)
        self.assert_code('native_contract', request(setting('delay.time', 1301, 'ms')), inventory)
        self.assert_code('native_contract', request(setting('delay.time', -1, 'ms')), inventory)

    def test_display_labels_do_not_grant_units_and_fine_delay_is_samples(self):
        inventory = with_channels(vectors.inventory())
        self.assert_code('unit_unverified', request(setting(value=-10, unit='dB')), inventory)
        self.assert_code('unit_unverified', request(setting(value=1000, unit='Hz')), inventory)
        # Reused engineering-time descriptor must not override the actual fine
        # delay sample contract, even if the upstream catalog is older.
        fine = delay_inventory(0x17)
        self.assert_code('unit_unverified', request(setting('delay.fine', 1, 'ms')), fine)
        self.assert_code('unit_unverified', request(setting('delay.fine', .001, 's')), fine)
        a.compile_authoring_request(request(setting('delay.fine', 1, 'native')), fine)

    def test_verified_identity_metadata_allows_db_without_display_guessing(self):
        inventory = with_channels(vectors.inventory())
        normalized = catalog.normalize_inventory(inventory)
        normalized['controls'][0]['verified_identity_units'] = {'native_unit': 'dB', 'evidence': ['synthetic trusted native identity contract']}
        with mock.patch.object(a, 'normalize_inventory', return_value=normalized):
            plan = a.compile_authoring_request(request(setting(value=-10, unit='dB')), inventory)
        self.assertEqual(plan['stages'][0]['raw_request']['operations'][0]['value'], -10.)

    def test_eq_q_uses_actual_shape_limits(self):
        bell, shelf = eq_inventory(1), eq_inventory(4)
        a.compile_authoring_request(request(setting('eq.q', 10)), bell)
        self.assert_code('native_contract', request(setting('eq.q', 1)), shelf)
        a.compile_authoring_request(request(setting('eq.q', .85)), shelf)

    def test_mode_changes_require_refresh_before_sibling_is_compiled(self):
        initial = eq_inventory(1)
        plan = a.compile_authoring_request(request(setting('eq.shape', 4), setting('eq.q', 1)), initial)
        self.assertEqual(len(plan['stages']), 2)
        self.assertFalse(plan['fully_compiled'])
        self.assertEqual(plan['stages'][0]['raw_request']['operations'][0]['id'], '0404000001')
        later = plan['stages'][1]
        self.assertIsNone(later['raw_request'])
        self.assertIsNone(later['inventory_sha256'])
        self.assertEqual(later['barrier_before'], 'fresh_inventory_and_recompile')
        self.assertEqual(later['authoring_request']['operations'][0]['channel'], {'id': 256})
        self.assertEqual(later['authoring_request']['operations'][0]['parameter'], 'id:0604000001')
        self.assertEqual(plan['changes'][1]['validation'], 'requires_fresh_inventory')
        self.assert_code('native_contract', later['authoring_request'], eq_inventory(4))

    def test_ordinary_controls_before_mode_are_separate_and_order_is_preserved(self):
        initial = eq_inventory(1)
        plan = a.compile_authoring_request(request(setting('eq.q', 1), setting('eq.shape', 4), setting('eq.q', .8)), initial)
        self.assertEqual([s['operation_indices'] for s in plan['stages']], [[0], [1], [2]])
        self.assertIsNone(plan['stages'][1]['raw_request'])
        self.assertIsNone(plan['stages'][2]['raw_request'])
        fresh = a.compile_authoring_request(plan['stages'][2]['authoring_request'], eq_inventory(4))
        self.assertTrue(fresh['fully_compiled'])
        self.assertAlmostEqual(fresh['changes'][0]['intended_native']['value'], .8)

    def test_rename_strict_encoding_and_preview_raw_bytes(self):
        inventory = add_name_controller(with_channels(vectors.inventory()))
        rename = {'op': 'rename', 'channel': {'id': 256}, 'name': '合成通道'}
        plan = a.compile_authoring_request(request(rename, encoding='cp950'), inventory)
        self.assertEqual(plan['changes'][0]['intended']['name_hex'], '合成通道'.encode('cp950').hex())
        native.compile_request(plan['stages'][0]['raw_request'], inventory=inventory)
        self.assert_code('invalid_name', request(rename, encoding='ascii'), inventory)
        rename['name'] = '合' * 16
        self.assert_code('invalid_name', request(rename, encoding='cp950'), inventory)

    def test_explicit_expected_is_converted_under_same_contract(self):
        plan = a.compile_authoring_request(request(setting('delay', 5, 'ms', expected={'value': 1, 'unit': 'ms'})), delay_inventory())
        self.assertFalse(plan['changes'][0]['expected_is_current'])
        self.assertAlmostEqual(plan['stages'][0]['raw_request']['operations'][0]['expected'], .001)

    def test_strict_unknown_fields_nonfinite_boolean_and_bad_wrappers(self):
        inventory = with_channels(vectors.inventory())
        self.assert_code('invalid_schema', request(setting(typo=True)), inventory)
        self.assert_code('invalid_quantity', request(setting(value=True)), inventory)
        self.assert_code('invalid_quantity', request(setting(value=float('nan'))), inventory)
        self.assert_code('invalid_quantity', request(setting(value=10**1000)), inventory)
        self.assert_code('invalid_selector', request(setting(channel={'id': 256, 'logical': 1})), inventory)
        self.assert_code('unknown_parameter', request(setting('hpf.frequency', 80)), inventory)
        self.assert_code('invalid_inventory', request(setting()), {'status': 'error', 'inventory': inventory})

    def test_reviewed_v2_requires_explicit_fader_alias_and_cannot_admit_other_aliases(self):
        item = catalog.normalize_inventory(vectors.inventory())['controls'][0]
        other_hash = 'a' * 64
        reviewed = {'implementation': 'quantum-layout-v2', 'authoring_aliases': ['fader', 'mute', 'pan']}
        with mock.patch.object(a, 'select_profile', return_value=reviewed) as selector:
            self.assertEqual(a._aliases(item, other_hash), ['fader'])
            selector.assert_called_once_with(executable_sha256=other_hash)
            mute = dict(item, parameter=0x22, module_type=12)
            self.assertEqual(a._aliases(mute, other_hash), [])
        for profile in ({'implementation': 'quantum-layout-v2'},
                        {'implementation': 'quantum-layout-v2', 'authoring_aliases': 'fader'},
                        {'implementation': 'unreviewed-layout', 'authoring_aliases': ['fader']}):
            with mock.patch.object(a, 'select_profile', return_value=profile):
                self.assertEqual(a._aliases(item, other_hash), [])
        with mock.patch.object(a, 'select_profile', side_effect=a.BuildProfileError('unknown exact hash')):
            self.assertEqual(a._aliases(item, other_hash), [])


if __name__ == '__main__':
    unittest.main()

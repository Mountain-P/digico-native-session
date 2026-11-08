"""Profile isolation/admission vectors; no target, VM, executable or network."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/digico-native-session/scripts'))
import build_profiles as builds
import preset_plan
import meter_catalog
import control_catalog


class BuildProfileTests(unittest.TestCase):
    def setUp(self):
        self.original = json.loads((builds.PROFILE_DIRECTORY / 'quantum1-v2126.json').read_text())
        self.digest = self.original['executable']['sha256']

    def test_exact_identity_and_unknown_build_rejection(self):
        self.assertEqual(builds.select_profile(self.digest)['id'], 'quantum1-v2126')
        for digest, identity in [('0'*64, None), (self.digest, 'quantum5-unverified')]:
            with self.assertRaises(builds.BuildProfileError):
                builds.select_profile(digest, identity)

    def test_record_only_candidate_does_not_enter_runnable_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'quantum1-v2126.json').write_text(json.dumps(self.original))
            candidate = copy.deepcopy(self.original)
            candidate.update(id='quantum5-candidate', implementation='unknown-layout')
            candidate['executable']['sha256'] = '1'*64
            candidate['validation'] = {'stage':'static_candidate', 'offline_runtime_verified':False}
            path = root/'candidate.json'; path.write_text(json.dumps(candidate))
            self.assertFalse(builds.load_profile_record(path)['validation']['offline_runtime_verified'])
            with patch.object(builds, 'PROFILE_DIRECTORY', root):
                builds._registry.cache_clear()
                try:
                    self.assertEqual(builds.select_profile(self.digest)['id'], self.original['id'])
                    with self.assertRaises(builds.BuildProfileError):
                        builds.select_profile('1'*64, 'quantum5-candidate')
                finally:
                    builds._registry.cache_clear()

    def test_envelope_and_nested_identities_must_all_agree(self):
        document = {'executable_sha256':self.digest, 'inventory':{
            'build_profile':'quantum5-unverified', 'executable_sha256':self.digest}}
        with self.assertRaises(builds.BuildProfileError):
            builds.profile_for_document(document)
        with self.assertRaises(builds.BuildProfileError):
            builds.profile_for_document({'build_profile':'quantum1-v2126', 'executable_sha256':None})

    def test_contracts_and_steps_carry_exact_build_identity(self):
        plan = preset_plan.plan_lock_preset(preset_index=0, preset_count=1, locked=True)
        for value in (plan, plan['steps'][0]):
            self.assertEqual(value['build_profile'], self.original['id'])
            self.assertEqual(value['executable_sha256'], self.digest)
        document = builds.stamp_profile({'control_contract':{}, 'route_contract':{}, 'group_contract':{},
                                         'processing_checks':[{'control_contract':{}}]})
        for key in ('control_contract', 'route_contract', 'group_contract'):
            self.assertEqual(document[key]['build_profile'], self.original['id'])
        self.assertEqual(document['processing_checks'][0]['control_contract']['executable_sha256'], self.digest)

    def test_selected_profile_drives_payload_and_context_isolation(self):
        # A temporary synthetic reviewed record tests the architecture; it is
        # never shipped as a supported real executable or used with a process.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            other = copy.deepcopy(self.original)
            other['id'] = 'synthetic-build'; other['executable']['sha256'] = '2'*64
            other['presets']['group'] = 0x1234
            other['addresses']['hex_b75f50'] = 0x123456
            other['layouts']['definition']['size'] = 59
            other['layouts']['definition']['data_flags'] = 58
            for value in (self.original, other):
                (root/(value['id']+'.json')).write_text(json.dumps(value))
            with patch.object(builds, 'PROFILE_DIRECTORY', root):
                builds._registry.cache_clear()
                try:
                    plan = preset_plan.plan_lock_preset(preset_index=0,preset_count=1,
                                                        locked=True,build_profile=other['id'])
                    self.assertEqual(plan['steps'][0]['control_id_hex'], 'd200003412')
                    self.assertEqual(plan['steps'][0]['callback_va'], '0x123456')
                    self.assertEqual(plan['executable_sha256'], '2'*64)
                    with builds.use_profile(build_profile=other['id']):
                        raw = bytes(58)+b'\x5a'
                        self.assertEqual(control_catalog.parse_definition(raw.hex())['data_flags'], 0x5a)
                        with self.assertRaises(control_catalog.CatalogError):
                            control_catalog.parse_definition(bytes(58).hex())
                    with self.assertRaises(control_catalog.CatalogError):
                        control_catalog.parse_definition(raw.hex())
                    def read(identity):
                        with builds.use_profile(build_profile=identity):
                            return builds.build_value('presets.group')
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        result = list(pool.map(read, ['synthetic-build', 'quantum1-v2126']))
                    self.assertEqual(result, [0x1234, 0x1b6])
                    self.assertEqual(builds.build_value('id'), 'quantum1-v2126')
                    with self.assertRaises(builds.BuildProfileError):
                        builds.profile_for_document({'executable_sha256':self.digest,
                            'control_contract':{'build_profile':other['id'],'executable_sha256':'2'*64}})
                finally:
                    builds._registry.cache_clear()

    def test_build_feature_gate_precedes_payload_generation(self):
        profile = builds.select_profile()
        readonly = dict(profile); readonly['features'] = {'presets':False}
        with patch.object(builds, 'select_profile', return_value=readonly):
            with self.assertRaises(preset_plan.PresetPlanError):
                preset_plan.plan_lock_preset(preset_index=0,preset_count=1,locked=True)

    def test_base_layout_has_distinct_controller_and_list_parents(self):
        layout = builds.select_profile()['layouts']
        self.assertEqual(layout['controller']['parent'], 0x44)
        self.assertEqual(layout['controller']['audio_id'], 0x48)
        self.assertEqual(layout['controller']['restore'], 0x50)
        self.assertEqual(layout['console_list']['parent'], 0x24)
        for field in ('parent', 'audio_id', 'restore'):
            self.assertLess(layout['controller'][field], layout['controller']['size'])

    def test_incomplete_layout_cannot_be_admitted(self):
        incomplete = copy.deepcopy(self.original)
        incomplete['layouts']['controller']['parent'] = incomplete['layouts']['controller']['size']
        with self.assertRaises(builds.BuildProfileError):
            builds.validate_layouts(incomplete)

    def test_55_byte_definition_has_no_colour_and_shifted_seed_lut(self):
        profile = copy.deepcopy(self.original)
        profile['id'] = 'synthetic-55-byte-record'
        profile['executable']['sha256'] = '3'*64
        profile['layouts']['definition'].update(size=55,colour=None,seed=46,lut=50,data_flags=54)
        builds.validate_layouts(profile)
        raw = bytearray(55);raw[0] = 1
        struct.pack_into('<f', raw, 1, 3.5)
        raw[5:7] = bytes((2, 5));raw[7:11] = b'\x03syn';raw[39:42] = b'\x02Hz'
        struct.pack_into('<II', raw, 46, 0x11223344, 0x55667788);raw[54] = 0xa5
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for value in (self.original, profile):
                (root/(value['id']+'.json')).write_text(json.dumps(value))
            with patch.object(builds, 'PROFILE_DIRECTORY', root):
                builds._registry.cache_clear()
                try:
                    with builds.use_profile(build_profile=profile['id']):
                        parsed = control_catalog.parse_definition(raw.hex())
                        self.assertIsNone(parsed['colour'])
                        self.assertEqual(parsed['response_seed_pointer'], 0x11223344)
                        self.assertEqual(parsed['response_lut_pointer'], 0x55667788)
                        self.assertEqual(parsed['data_flags'], 0xa5)
                        self.assertEqual((parsed['name'], parsed['units'], parsed['band'], parsed['parameter']),
                                         ('syn', 'Hz', 2, 5))
                        self.assertEqual(parsed['raw_hex'], raw.hex())
                finally:
                    builds._registry.cache_clear()
        invalid = copy.deepcopy(profile)
        invalid['layouts']['definition']['colour'] = 54
        with self.assertRaises(builds.BuildProfileError):
            builds.validate_layouts(invalid)

    def test_inventory_only_profile_normalizes_without_granting_writes(self):
        readonly = copy.deepcopy(self.original)
        readonly['id'] = 'synthetic-inventory-only'
        readonly['executable']['sha256'] = '5'*64
        readonly['features']['control_write'] = False
        raw = bytearray(58); raw[6] = 0x22
        source = {'schema_version':1, **builds.profile_identity(readonly), 'controllers':[{
            'raw_id_hex':'220c000001', 'definition_raw_hex':raw.hex(),
            'module_vmt_rva':0x1000, 'process_output_rva':0x2000,
            'value_raw_hex':'00000000'}]}
        registry = ({self.original['id']:self.original, readonly['id']:readonly},
                    {self.digest:self.original, '5'*64:readonly})
        with patch.object(builds, '_registry', return_value=registry):
            catalog = control_catalog.normalize_inventory(source)
            self.assertFalse(catalog['build_write_admitted'])
            self.assertFalse(catalog['controls'][0]['writable_candidate'])
            self.assertIn('not admitted', catalog['controls'][0]['readonly_reason'])
            # A caller cannot turn a read-only profile into a writer by editing
            # normalized candidate metadata in the copied document.
            catalog['controls'][0].update(writable_candidate=True, readonly_reason=None)
            with self.assertRaises(control_catalog.CatalogError):
                control_catalog.plan_control_write(catalog, '220c000001', 1)
            readonly['features'].pop('control_write')
            self.assertFalse(control_catalog.normalize_inventory(source)['build_write_admitted'])

    def test_v2_exact_control_allowlist_and_tamper_rejection(self):
        profile = copy.deepcopy(self.original)
        profile.update(id='synthetic-v2-control',implementation='quantum-layout-v2')
        profile['executable']['sha256']='6'*64
        rule={'id_prefix_hex':'210c00','raw_type':1,'definition_rva':0x10100,
              'module_vmt_rva':0x81e510,'process_output_rva':0x832414}
        profile['control_write_allowlist']=[rule]
        builds.validate_control_write_allowlist(profile)
        raw=bytearray(58);raw[0]=1;raw[6]=0x21
        struct.pack_into('<I',raw,50,0x500000)
        seed=bytearray(38);seed[0]=1
        struct.pack_into('<HI',seed,32,2,0x600000)
        source={'schema_version':1,**builds.profile_identity(profile),'controllers':[{
            'raw_id_hex':'210c000001','definition_raw_hex':raw.hex(),'definition_rva':0x10100,
            'module_vmt_rva':0x81e510,'process_output_rva':0x832414,
            'response_pointer_binding_verified':True,'current_response_seed_pointer':0,'current_response_lut_pointer':0,
            'response_seed_raw_hex':seed.hex(),'response_points':[{'position':0,'value':-150.0},{'position':255,'value':10.0}]}]}
        registry=({self.original['id']:self.original,profile['id']:profile},{self.digest:self.original,'6'*64:profile})
        with patch.object(builds,'_registry',return_value=registry):
            catalog=control_catalog.normalize_inventory(source)
            self.assertTrue(catalog['controls'][0]['writable_candidate'])
            plan=control_catalog.plan_control_write(catalog,'210c000001',-20)
            self.assertEqual(plan['control_write_authorization']['mode'],'exact_profile_allowlist')
            self.assertEqual(plan['response_pointer_binding']['current_response_seed_pointer'],0)
            self.assertEqual(plan['response_pointer_binding']['effective_response_seed_pointer'],0x500000)
            for seed_pointer in (0x500004,None):
                changed=copy.deepcopy(source)
                changed['controllers'][0]['current_response_seed_pointer']=seed_pointer
                blocked=control_catalog.normalize_inventory(changed)
                self.assertFalse(blocked['controls'][0]['writable_candidate'])
                blocked['controls'][0].update(writable_candidate=True,readonly_reason=None)
                with self.assertRaises(control_catalog.CatalogError):
                    control_catalog.plan_control_write(blocked,'210c000001',-20)
            matching=copy.deepcopy(source)
            matching['controllers'][0]['current_response_seed_pointer']=0x500000
            self.assertTrue(control_catalog.normalize_inventory(matching)['controls'][0]['writable_candidate'])
            for field,value in (('raw_type',0),('definition_rva',0x10104),
                                ('module_vmt_rva',0x81e514),('process_output_rva',0x832418)):
                bad=copy.deepcopy(catalog)
                if field in ('raw_type','module_vmt_rva','process_output_rva'):bad['controls'][0][field]=value
                else:bad['controls'][0]['source'][field]=value
                with self.subTest(field=field),self.assertRaises(control_catalog.CatalogError):
                    control_catalog.plan_control_write(bad,'210c000001',-20)
            with builds.use_profile(build_profile=profile['id']):
                for key,kind in (('220c000001',1),('fd0c000001',4)):
                    with self.assertRaises(control_catalog.CatalogError):
                        control_catalog.assert_control_write_allowed(key,raw_type=kind,definition_rva=0x10100,
                            module_vmt_rva=0x81e510,process_output_rva=0x832414)
                for policy in (None,[]):
                    profile['control_write_allowlist']=policy
                    with self.assertRaises(control_catalog.CatalogError):
                        control_catalog.assert_control_write_allowed('210c000001',raw_type=1,definition_rva=0x10100,
                            module_vmt_rva=0x81e510,process_output_rva=0x832414)
        malformed=copy.deepcopy(profile);malformed['control_write_allowlist']=[{**rule,'raw_type':True}]
        with self.assertRaises(builds.BuildProfileError):builds.validate_control_write_allowlist(malformed)

    def test_null_semantic_profiles_keep_known_context_controls_readonly(self):
        profile = copy.deepcopy(self.original)
        profile['contexts'] = {key:None for key in profile['contexts']}
        registry = ({profile['id']:profile}, {self.digest:profile})
        with patch.object(builds, '_registry', return_value=registry):
            for parameter, module, band in ((6,4,0), (0x11,5,0), (0x11,5,3), (8,9,0)):
                with self.subTest(parameter=parameter,module=module,band=band):
                    raw = bytearray(58);raw[0]=1;raw[5]=band;raw[6]=parameter
                    struct.pack_into('<I',raw,50,0x500000)
                    seed = bytearray(38);seed[0]=1
                    struct.pack_into('<HI',seed,32,2,0x600000)
                    identifier=bytes((parameter,module,band,0,1)).hex()
                    source = {'schema_version':1, 'executable_sha256':self.digest,
                              'controllers':[{'raw_id_hex':identifier,'definition_raw_hex':raw.hex(),
                                'module_vmt_rva':0x1000,'process_output_rva':0x2000,
                                'response_seed_raw_hex':seed.hex(),
                                'response_points':[{'position':0,'value':0.0},{'position':255,'value':1.0}]}]}
                    catalog = control_catalog.normalize_inventory(source)
                    self.assertFalse(catalog['controls'][0]['writable_candidate'])
                    self.assertIn('context is not admitted',catalog['controls'][0]['readonly_reason'])
                    with self.assertRaises(control_catalog.CatalogError):
                        control_catalog.plan_control_write(catalog,identifier,0.5)

    def test_v2_boolean_requires_explicit_subset_and_blocks_other_values(self):
        profile=copy.deepcopy(self.original)
        profile.update(id='synthetic-v2-boolean',implementation='quantum-layout-v2')
        profile['executable']['sha256']='7'*64
        profile['control_enums']={}
        profile['control_write_allowlist']=[{'id_prefix_hex':'220c00','raw_type':0,
            'definition_rva':0x10200,'module_vmt_rva':0x81e510,'process_output_rva':0x832414}]
        raw=bytearray(58);raw[6]=0x22
        source={'schema_version':1,**builds.profile_identity(profile),'controllers':[{
            'raw_id_hex':'220c000001','definition_raw_hex':raw.hex(),'definition_rva':0x10200,
            'module_vmt_rva':0x81e510,'process_output_rva':0x832414,
            'response_pointer_binding_verified':True,'current_response_seed_pointer':0,'current_response_lut_pointer':0}]}
        registry=({self.original['id']:self.original,profile['id']:profile},
                  {self.digest:self.original,'7'*64:profile})
        with patch.object(builds,'_registry',return_value=registry):
            denied=control_catalog.normalize_inventory(source)
            self.assertFalse(denied['controls'][0]['writable_candidate'])
            self.assertIn('no explicit v2',denied['controls'][0]['readonly_reason'])
            profile['control_enums'][str(0x0c22)]=[0,1]
            accepted=control_catalog.normalize_inventory(source)
            self.assertEqual(accepted['controls'][0]['value_domain_source'],'profile_restricted_enum')
            self.assertEqual(control_catalog.plan_control_write(accepted,'220c000001',1)['payload_hex'],'0000803f')
            with self.assertRaises(control_catalog.CatalogError):
                control_catalog.plan_control_write(accepted,'220c000001',2)


if __name__ == '__main__':
    unittest.main()

"""Synthetic Preset adapter contract tests; no target, network or Session files."""
from copy import deepcopy
from pathlib import Path
import struct
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]/'skills/digico-native-session/scripts'
sys.path.insert(0,str(SCRIPTS))
import preset_adapter as a


def presets():
    return {'status':'ok','executable_sha256':a.TARGET_SHA256,'presets':{
        'schema_version':1,'subsystem':'native-presets-readonly','preset_count':1,
        'list_pointer':0x2000,'metadata_sha256':'ab'*32,'scope_hex':'00'*32,
        'current_session_path_hex':br'C:\Synthetic\Fixture.ses'.hex(),
        'presets':[{'index':0,'pointer':0x3000,'name_hex':b'Original'.hex(),
            'group_name_hex':b'Synthetic Lab'.hex(),'notes_hex':'',
            'time_stored_hex':struct.pack('<d',1).hex(),'lock_flag':0,
            'fx_resource':0,'channel_count':1,'mix_preset':0,
            'content':{'channel_snapshot_count':1,'channels':[{
                'channel_id':0x101,'modules':[{'type_id_raw':12,'controllers':[{
                    'control_id_hex':'220c000101','value_raw_hex':struct.pack('<f',1).hex()}]}]}]}}]}}


def inventory():
    definition=bytearray(58);definition[6]=0x22
    return {'after':{'current_session_path':r'C:\Synthetic\Fixture.ses'},'inventory':{
        'schema_version':1,'executable_sha256':a.TARGET_SHA256,
        'controllers':[{'pointer':0x4000,'owner_module_pointer':0x5000,
            'module_vmt_rva':0x1000,'process_output_rva':0x2000,
            'raw_id_hex':'220c000001','definition_raw_hex':definition.hex(),
            'value_raw_hex':struct.pack('<f',0).hex()}]}}


def request(op):
    return {'schema_version':1,'fps':25,'encoding':'ascii','expected_count':0,
            'operations':[op]}


class PresetAdapterTests(unittest.TestCase):
    def compile(self,op,**kwargs):
        return a.compile_preset_request(request(op),presets=presets(),**kwargs)

    def test_capture_compiles_to_true_capture_then_optional_rename(self):
        plan=self.compile({'op':'preset_capture','channel_id':0x100,'group_name':'New Synthetic',
                           'name':'Captured','verify_control_ids':['220c000001']},inventory=inventory())
        self.assertEqual(plan['mode'],'preset_execute')
        self.assertEqual([s['op'] for s in plan['steps']],['store_input_preset','rename_preset'])
        self.assertEqual(plan['steps'][0]['processing_checks'][0]['before_raw_hex'],'00000000')
        self.assertEqual(plan['steps'][0]['expected_controller_values'],[{
            'control_id_hex':'220c000001','value_raw_hex':'00000000'}])
        self.assertEqual(plan['final_expected_preset_count'],2)
        self.assertEqual(plan['final_expected_count'],0)
        self.assertFalse(plan['runtime_acceptance_verified'])
        self.assertEqual(len(plan['steps'][0]['frames']),1)

    def test_existing_capture_group_is_protected(self):
        with self.assertRaises(a.PresetAdapterError):
            self.compile({'op':'preset_capture','channel_id':0x100,'group_name':'Synthetic Lab'})

    def test_metadata_operations_bind_exact_native_object_and_group(self):
        for op in ({'op':'preset_rename','name':'Renamed'},
                   {'op':'preset_lock','locked':True},{'op':'preset_delete'}):
            plan=self.compile({'index':0,'group_name':'Synthetic Lab',**op})
            self.assertEqual(plan['preset_inventory_binding']['selected_pointer'],0x3000)
        with self.assertRaises(a.PresetAdapterError):
            self.compile({'op':'preset_delete','index':0,'group_name':'Other'})
        doc=presets();doc['presets']['presets'][0]['lock_flag']=2
        with self.assertRaises(a.PresetAdapterError):
            a.compile_preset_request(request({'op':'preset_rename','index':0,'group_name':'Synthetic Lab','name':'Bad'}),presets=doc)

    def test_update_uses_current_source_raw_value_and_inventory_binding(self):
        plan=self.compile({'op':'preset_update','index':0,'group_name':'Synthetic Lab',
                           'channel_id':0x100,'verify_control_ids':['220c000001']},inventory=inventory())
        step=plan['steps'][0]
        self.assertEqual(step['body_hex'],'00010000')
        self.assertEqual(step['processing_checks'][0]['control_contract']['control_pointer'],0x4000)

    def test_recall_maps_stored_source_to_target_and_restores_raw_scope(self):
        plan=self.compile({'op':'preset_recall','index':0,'group_name':'Synthetic Lab',
            'channel_id':0x100,'scopes':['group'],'verify_control_ids':['220c000001']},inventory=inventory())
        self.assertEqual([s['op'] for s in plan['steps']],
            ['set_preset_recall_scope','recall_input_preset','restore_preset_recall_scope'])
        check=plan['steps'][1]['processing_checks'][0]
        self.assertEqual(check['source_control_id_hex'],'220c000101')
        self.assertEqual(check['expected_raw_hex'],'0000803f')
        self.assertEqual(check['before_raw_hex'],'00000000')
        self.assertEqual(plan['steps'][-1]['expected_scope_hex'],'00'*32)

    def test_recall_rejects_unknown_or_outside_scope_processing(self):
        base={'op':'preset_recall','index':0,'group_name':'Synthetic Lab',
              'channel_id':0x100,'scopes':['group'],'verify_control_ids':['220c000001']}
        for changes in ({'scopes':['eq']},{'verify_control_ids':[]},
                        {'verify_control_ids':['220c000001']*2},
                        {'verify_control_ids':['220c000101']},
                        {'verify_control_ids':['ff01000001']}):
            with self.subTest(changes=changes),self.assertRaises(a.PresetAdapterError):
                self.compile(dict(base,**changes),inventory=inventory())
        doc=presets();doc['presets']['presets'][0]['content']=None
        with self.assertRaises(a.PresetAdapterError):
            a.compile_preset_request(request(base),presets=doc,inventory=inventory())
        with self.assertRaises(a.PresetAdapterError):self.compile(base)

    def test_scope_set_is_a_real_persistent_setter_and_can_restore_raw_bytes(self):
        plan=self.compile({'op':'preset_set_scope','scopes':['eq']})
        self.assertEqual(len(plan['steps']),1)
        self.assertEqual(plan['final_expected_scope_hex'],'90'+'00'*31)
        self.assertEqual(plan['steps'][0]['body_hex'],'2000000090'+'00'*31)
        copied=bytes(range(32)).hex()
        self.assertEqual(self.compile({'op':'preset_set_scope','scope_hex':copied})['final_expected_scope_hex'],copied)
        for op in ({'op':'preset_set_scope'},{'op':'preset_set_scope','scopes':['eq'],'scope_hex':'00'*32}):
            with self.assertRaises(a.PresetAdapterError):self.compile(op)

    def test_strict_envelopes_and_one_operation_boundary(self):
        self.assertTrue(a.is_preset_request(request({'op':'preset_set_scope','scopes':['eq']})))
        self.assertFalse(a.is_preset_request(request({'op':'rename','index':0,'name':'Cue'})))
        doc=presets();doc['executable_sha256']='00'*32
        with self.assertRaises(a.PresetAdapterError):
            a.compile_preset_request(request({'op':'preset_set_scope','scopes':['eq']}),presets=doc)
        multi=request({'op':'preset_set_scope','scopes':['eq']});multi['operations'].append({'op':'save'})
        with self.assertRaises(a.PresetAdapterError):a.compile_preset_request(multi,presets=presets())
        bad=deepcopy(inventory());bad['after']['current_session_path']=r'C:\Synthetic\Other.ses'
        with self.assertRaises(a.PresetAdapterError):self.compile({'op':'preset_capture','channel_id':0x100,
            'group_name':'New Synthetic','verify_control_ids':['220c000001']},inventory=bad)

    def test_after_result_is_valid_presets_input(self):
        doc=presets();state=doc.pop('presets');doc['presets_after']=state
        result=a.normalize_presets(doc)
        self.assertEqual(result['presets'],state['presets'])
        self.assertEqual(result['build_profile'],'quantum1-v2126')
        self.assertNotIn('build_profile',state)

    def test_root_compiler_delegates_with_separate_presets_input(self):
        import native_session
        raw=request({'op':'preset_set_scope','scopes':['eq']})
        plan=native_session.compile_request(raw,presets=presets())
        self.assertEqual(plan['mode'],'preset_execute')
        with self.assertRaises(ValueError):native_session.compile_request(raw)


if __name__=='__main__':unittest.main()

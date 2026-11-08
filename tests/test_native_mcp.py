"""MCP protocol and CLI adapter tests. No VM, network, UI or device required."""
import importlib.util
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'skills/digico-native-session/scripts/mcp_server.py'
sys.path.insert(0,str(SCRIPT.parent))
spec=importlib.util.spec_from_file_location('native_mcp',SCRIPT)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def metadata(version=m.MODERN_VERSION):
    return {'io.modelcontextprotocol/protocolVersion':version,
            'io.modelcontextprotocol/clientCapabilities':{},
            'io.modelcontextprotocol/clientInfo':{'name':'synthetic-test','version':'1'}}


def request(method, params=None, identifier=1):
    return {'jsonrpc':'2.0','id':identifier,'method':method,
            'params':{'_meta':metadata(),**(params or {})}}


def raw_request():
    return {'schema_version':1,'fps':25,'encoding':'ascii','expected_count':0,
            'operations':[{'op':'save_as','path':r'C:\Synthetic\PlanOnly.ses'}]}


def preset_request(operation=None):
    r=raw_request();r['operations']=[operation or {'op':'preset_capture','channel_id':256,
        'group_name':'SyntheticGroup','name':'SyntheticName'}]
    return r


def preset_state(count=1):
    return {'schema_version':1,'subsystem':'native-presets-readonly','memory_read_only':True,
        'executable_sha256':'535097f38bb459e6e299b5da50b62fa811511d92af6d7215a13d703a416f177d',
        'preset_count':count,'list_pointer':0x800000,'scope_hex':'00'*32,'metadata_sha256':'00'*32,
        'current_session_path':r'C:\Synthetic\Presets.ses',
        'current_session_path_hex':r'C:\Synthetic\Presets.ses'.encode('ascii').hex(),
        'library_file_path_established':False,'text_encoding':'ascii',
        'presets':[{'index':i,'pointer':0x810000+i*0x100,'name':'SyntheticPreset',
            'name_hex':b'SyntheticPreset'.hex(),'group_name':'SyntheticGroup',
            'group_name_hex':b'SyntheticGroup'.hex(),'notes_hex':'','time_stored_hex':'00'*8,
            'lock_flag':0,'fx_resource':0,'channel_count':1,'mix_preset':0,
            'content':{'channel_snapshot_count':1,'controller_count':1,
                'controller_content_sha256':'00'*32,
                'channels':[{'private_raw_marker':'DO_NOT_EXPAND_MODEL_RESULT'}]}}
            for i in range(count)]}


def structure_state():
    raw=bytearray(123)
    struct.pack_into('<H',raw,2,72)
    struct.pack_into('<ff',raw,0x53,48000,25)
    return {'schema_version':1,'subsystem':'native-session-structure-readonly',
        'executable_sha256':preset_state(0)['executable_sha256'],
        'executable_hash_verified_by_caller':True,'alias_slot_rva':0x8E9980,
        'record_rva':0x127A17C,'record_bytes':123,'raw_hex':raw.hex(),
        'alias_verified':True,'consistent_double_read':True,
        'memory_read_only':True,'ui_used':False,'network_used':False,
        'headless_structure_setter_established':False}


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.adapter=Mock();self.adapter.call.return_value={'dry_run':True}
        self.server=m.Server(self.adapter)

    def test_modern_discovery_and_list_need_no_prior_handshake(self):
        response=self.server.handle(request('server/discover'))['result']
        self.assertEqual(response['resultType'],'complete')
        self.assertIn(m.MODERN_VERSION,response['supportedVersions'])
        self.assertEqual(response['cacheScope'],'public')
        response=self.server.handle(request('tools/list'))['result']
        self.assertEqual([t['name'] for t in response['tools']],list(m.TOOL_BY_NAME))
        self.assertFalse(self.server.legacy_initialized)

    def test_modern_request_metadata_is_not_reused(self):
        self.server.handle(request('tools/list'))
        missing={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'digico_execute','arguments':{'request':raw_request(),'send':True}}}
        response=self.server.handle(missing)
        self.assertIn('error',response);self.adapter.call.assert_not_called()
        unsupported=request('tools/list');unsupported['params']['_meta']=metadata('1900-01-01')
        self.assertEqual(self.server.handle(unsupported)['error']['code'],-32022)
        bad=request('tools/list');bad['params']['_meta'].pop('io.modelcontextprotocol/clientCapabilities')
        self.assertEqual(self.server.handle(bad)['error']['code'],-32602)

    def test_legacy_initialize_initialized_and_tools(self):
        first={'jsonrpc':'2.0','id':'init','method':'initialize','params':{
            'protocolVersion':m.LEGACY_VERSION,'capabilities':{},'clientInfo':{'name':'legacy','version':'1'}}}
        self.assertEqual(self.server.handle(first)['result']['protocolVersion'],m.LEGACY_VERSION)
        self.assertIsNone(self.server.handle({'jsonrpc':'2.0','method':'notifications/initialized'}))
        result=self.server.handle({'jsonrpc':'2.0','id':2,'method':'tools/list'})['result']
        self.assertNotIn('resultType',result)
        self.assertEqual(len(result['tools']),14)

    def test_legacy_ping_is_allowed_before_initialization(self):
        response=self.server.handle({'jsonrpc':'2.0','id':1,'method':'ping'})
        self.assertEqual(response['result'],{})
        self.assertFalse(self.server.legacy_ready)

    def test_notification_cannot_invoke_a_tool(self):
        message={'jsonrpc':'2.0','method':'tools/call','params':{'name':'digico_execute','arguments':{'request':raw_request(),'send':True}}}
        self.assertIsNone(self.server.handle(message));self.adapter.call.assert_not_called()

    def test_protocol_error_and_tool_failure_are_distinct(self):
        self.adapter.call.side_effect=m.RpcError(-32602,'Unknown tool')
        response=self.server.handle(request('tools/call',{'name':'unknown'}))
        self.assertEqual(response['error']['code'],-32602)
        self.adapter.call.side_effect=m.ToolError('synthetic failed')
        response=self.server.handle(request('tools/call',{'name':'digico_plan'}))
        self.assertTrue(response['result']['isError'])
        self.assertFalse(response['result']['structuredContent']['runtime_acceptance_verified'])

    def test_invalid_ids_batches_and_params_are_rejected(self):
        for bad in (None,[],{'jsonrpc':'2.0','method':'ping','id':None},
                    {'jsonrpc':'2.0','method':'ping','id':True},
                    {'jsonrpc':'2.0','method':'ping','id':1.5}):
            self.assertEqual(self.server.handle(bad)['error']['code'],-32600)
        self.assertEqual(self.server.handle({'jsonrpc':'2.0','method':'ping','id':1,'params':[]})['error']['code'],-32602)

    def test_stdio_parse_error_recovers_and_stdout_is_only_json(self):
        payload=b'{bad}\n'+json.dumps(request('ping')).encode()+b'\n'
        sink=io.BytesIO();m.serve(self.server,io.BytesIO(payload),sink)
        lines=sink.getvalue().decode().splitlines()
        self.assertEqual(len(lines),2)
        self.assertEqual(json.loads(lines[0])['error']['code'],-32700)
        self.assertEqual(json.loads(lines[1])['result']['resultType'],'complete')

    def test_duplicate_json_fields_are_rejected(self):
        with self.assertRaises(ValueError):m.parse_json('{"a":1,"a":2}')
        with self.assertRaises(ValueError):m.parse_json('{"x":NaN}')


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='digico-mcp-')
        self.addCleanup(self.temp.cleanup)
        self.work=Path(self.temp.name)/'private'
        self.commands=[]

    def adapter(self,runner=None):
        return m.Adapter(vm='Synthetic VM',executable=r'C:\Synthetic\Quantum1.exe',
                         work_dir=self.work,run_process=runner or self.runner)

    def runner(self,command,**kwargs):
        self.commands.append((command,kwargs))
        mode=command[2]
        if mode=='catalog':
            result={'total_controls':123,'matching_controls':1,'returned_controls':1,
                    'runtime_acceptance_verified':False,'controls':[{'id':'210c000001','name':'synthetic fader','current_value':0.}]}
            return subprocess.CompletedProcess(command,0,stdout=json.dumps(result),stderr='')
        path=Path(command[command.index('--output' if mode=='plan' else '--result')+1])
        if mode=='plan' or (mode=='execute' and '--send' not in command):
            input_request=json.loads(Path(command[command.index('--request')+1]).read_text())
            result={'mode':'execute','steps':[{'op':'save_as'}], 'expected_count':0,
                    'final_expected_count':0,'runtime_acceptance_verified':False}
            if any(op['op'] in m.PRESET_NAMES for op in input_request['operations']):result['mode']='preset_execute'
            if mode=='execute':result.update(dry_run=True,windows_runner_invoked=False)
        else:
            result={'status':'ok','error':None,'mode':mode,'request_sha256':'a'*64,
                    'memory_read_only':True,'ui_used':False,'verified_steps':[],
                    'after':{'snapshot_count':0,'fps':25,'snapshots':[{'name':'DO_NOT_EXPAND_MODEL_RESULT'}]}}
            if mode=='presets':result['presets']=preset_state()
            if mode=='structure':result['structure']=structure_state()
            if mode=='meters':result['meters']={'memory_read_only':True,'network_used':False,'ui_used':False,
                'physical_audio_verified':False,'values_atomic':False,'freshness':'not_observable',
                'structure_verified':True,'matching_count':1,'returned_count':1,'truncated':False,
                'manager':{'synthetic_cycle_active':False,'registered_count':1,'stream_count':1},
                'meters':[{'raw_id_hex':'2001000001','name':'synthetic meter','channel_id':256,
                    'registry_index':0,'stream_index':0,'unit':'native-meter-code','freshness':'not_observable',
                    'physical_audio_verified':False,'output':{'raw_hex':'00004343','native_code':195,'finite':True},
                    'secondary':{'raw_hex':'00004843','native_code':200,'finite':True},
                    'definition_raw_hex':'DO_NOT_EXPAND_MODEL_RESULT'}]}
            if mode=='inventory':result['inventory']={'controller_count':123,'structure_verified':True,'controllers':[{'definition_raw_hex':'DO_NOT_EXPAND_MODEL_RESULT'}]}
        if mode!='execute' or '--send' in command:
            path.write_text(json.dumps(result),encoding='utf-8')
        return subprocess.CompletedProcess(command,0,stdout=json.dumps(result),stderr='')

    def test_real_cli_plan_and_execute_dryrun_do_not_contact_windows(self):
        adapter=m.Adapter(vm='UNUSED',executable=r'C:\Synthetic\NeverRun.exe',work_dir=self.work)
        for name in ('digico_plan','digico_execute'):
            summary=adapter.call(name,{'request':raw_request()})
            self.assertTrue(summary['dry_run']);self.assertFalse(summary['windows_runner_invoked'])
            result=json.loads(Path(summary['result_path']).read_text())
            self.assertFalse(result['runtime_acceptance_verified'])
            self.assertTrue(Path(summary['request_path']).is_file())
        self.assertFalse(list(self.work.glob('digico-native-runtime-*')))

    def test_send_default_false_and_literal_true_only(self):
        adapter=self.adapter()
        a=adapter.call('digico_execute',{'request':raw_request()})
        self.assertTrue(a['dry_run']);self.assertNotIn('--send',self.commands[-1][0])
        b=adapter.call('digico_execute',{'request':raw_request(),'send':True})
        self.assertFalse(b['dry_run']);self.assertIn('--send',self.commands[-1][0])
        self.assertNotEqual(a['request_path'],b['request_path'])
        for value in (1,'true',None):
            with self.assertRaises(m.ToolError):adapter.call('digico_execute',{'request':raw_request(),'send':value})

    def test_subprocess_has_exact_python_cli_and_no_shell(self):
        self.adapter().call('digico_plan',{'request':raw_request()})
        command,options=self.commands[0]
        self.assertEqual(command[:3],[sys.executable,str(m.CLI_PATH),'plan'])
        self.assertFalse(options['shell']);self.assertEqual(options['encoding'],'utf-8')
        self.assertEqual(options['stdin'],subprocess.DEVNULL)
        self.assertNotIn('--vm',command);self.assertEqual(options['timeout'],180)
        if os.name!='nt':self.assertEqual(self.work.stat().st_mode&0o777,0o700)

    def test_inventory_summary_stays_small_and_preserves_artifact(self):
        summary=self.adapter().call('digico_inventory',{'channel_id':256,'encoding':'cp950','without_response_tables':True})
        self.assertEqual(summary['inventory']['controller_count'],123)
        self.assertNotIn('DO_NOT_EXPAND_MODEL_RESULT',json.dumps(summary))
        self.assertIn('DO_NOT_EXPAND_MODEL_RESULT',Path(summary['result_path']).read_text())
        command=self.commands[0][0]
        self.assertIn('--without-response-tables',command)
        self.assertEqual(command[command.index('--channel-id')+1],'256')
        self.assertEqual(command[command.index('--encoding')+1],'cp950')
        self.assertNotIn('--full-output',command)

    def test_inventory_path_validation_precedes_artifact_creation(self):
        adapter=self.adapter()
        with self.assertRaises(m.ToolError):adapter.call('digico_plan',{'request':raw_request(),'inventory_path':'relative.json'})
        self.assertFalse(self.work.exists())
        source=Path(self.temp.name)/'inventory.json';source.write_text('{}')
        adapter.call('digico_plan',{'request':raw_request(),'inventory_path':str(source)})
        command=self.commands[-1][0]
        self.assertEqual(command[command.index('--inventory')+1],str(source))

    def test_export_and_semantic_input_route_schema(self):
        adapter=self.adapter()
        r=raw_request();r['operations']=[{'op':'export_session','source':r'C:\Synthetic\Source.ses','destination':r'C:\Synthetic\Destination.ses'}]
        self.assertTrue(adapter.call('digico_plan',{'request':r})['dry_run'])
        r['operations'].append({'op':'save'})
        with self.assertRaises(m.ToolError):adapter.call('digico_plan',{'request':r})
        for source in (None,{'port_type':1,'port_number':2,'socket_index':3}):
            r['operations']=[{'op':'set_input_route','channel_id':256,'source':source,'route_kind':'alt'}]
            self.assertTrue(adapter.call('digico_plan',{'request':r})['dry_run'])
        for source in (1,{'port_type':True,'port_number':2,'socket_index':3},
                       {'port_type':1,'port_number':2,'socket_index':256}):
            r['operations']=[{'op':'set_input_route','channel_id':256,'source':source}]
            with self.assertRaises(m.ToolError):adapter.call('digico_plan',{'request':r})
        adapter.call('digico_inventory',{'include_physical_sources':True})
        self.assertIn('--include-physical-sources',self.commands[-1][0])

    def test_all_typed_route_profiles_and_incremental_output_schema(self):
        adapter=self.adapter();r=raw_request()
        for kind in ('main','alt','key','mustard_key','insert_return','insert_send'):
            r['operations']=[{'op':'set_input_route','channel_id':256,'source':None,'route_kind':kind}]
            self.assertTrue(adapter.call('digico_plan',{'request':r})['dry_run'])
        r['operations']=[{'op':'set_output_route','channel_id':256,
            'destination':{'port_type':1,'port_number':1,'socket_index':0},'connected':False}]
        self.assertTrue(adapter.call('digico_plan',{'request':r})['dry_run'])
        r['operations'][0]['connected']=1
        with self.assertRaises(m.ToolError):adapter.call('digico_plan',{'request':r})

    def test_advertised_route_kinds_match_the_current_typed_library(self):
        path=ROOT/'skills/digico-native-session/scripts/routing_catalog.py'
        spec=importlib.util.spec_from_file_location('mcp_route_contract',path)
        routing=importlib.util.module_from_spec(spec);spec.loader.exec_module(routing)
        operation=next(op for op in m.OPERATIONS if op['properties']['op']['const']=='set_input_route')
        self.assertEqual(set(operation['properties']['route_kind']['enum']),set(routing.SOURCE_PROFILES))

    def test_group_assignment_and_group_inventory_flags(self):
        adapter=self.adapter();r=raw_request()
        r['operations']=[{'op':'set_group_assignment','channel_id':256,'group_channel_id':1536,'enabled':True}]
        self.assertTrue(adapter.call('digico_plan',{'request':r})['dry_run'])
        for key,value in (('enabled',1),('channel_id',0),('group_channel_id',256)):
            bad=json.loads(json.dumps(r));bad['operations'][0][key]=value
            with self.assertRaises(m.ToolError):adapter.call('digico_plan',{'request':bad})
        adapter.call('digico_inventory',{'include_group_outputs':True})
        self.assertIn('--include-group-outputs',self.commands[-1][0])
        self.assertNotIn('--include-physical-sources',self.commands[-1][0])

    def test_meters_wraps_readonly_cli_and_keeps_raw_metadata_local(self):
        summary=self.adapter().call('digico_meters',{'channel_id':256,'limit':12,'encoding':'cp950'})
        command=self.commands[-1][0]
        self.assertEqual(command[2],'meters');self.assertNotIn('--send',command)
        self.assertEqual(command[command.index('--limit')+1],'12')
        self.assertEqual(command[command.index('--channel-id')+1],'256')
        self.assertTrue(summary['read_only']);self.assertTrue(summary['windows_runner_invoked'])
        self.assertFalse(summary['meter_snapshot']['physical_audio_verified'])
        self.assertEqual(summary['meter_values'][0]['output']['native_code'],195)
        self.assertNotIn('DO_NOT_EXPAND_MODEL_RESULT',json.dumps(summary))
        self.assertIn('DO_NOT_EXPAND_MODEL_RESULT',Path(summary['result_path']).read_text())
        for bad in (0,1001,True):
            with self.assertRaises(m.ToolError):self.adapter().call('digico_meters',{'limit':bad})
        with self.assertRaises(m.ToolError):self.adapter().call('digico_meters',{'send':True})

    def test_preset_query_and_execute_use_typed_cli_and_local_artifacts(self):
        adapter=self.adapter()
        summary=adapter.call('digico_presets',{'encoding':'cp950','content_indices':[0,2]})
        command=self.commands[-1][0]
        self.assertEqual(command[2],'presets')
        self.assertEqual([command[i+1] for i,x in enumerate(command) if x=='--content-index'],['0','2'])
        self.assertEqual(summary['presets_snapshot']['preset_count'],1)
        self.assertNotIn('DO_NOT_EXPAND_MODEL_RESULT',json.dumps(summary))
        self.assertIn('DO_NOT_EXPAND_MODEL_RESULT',Path(summary['result_path']).read_text())
        for indices in ([True],[0,0],[10000]):
            with self.assertRaises(m.ToolError):adapter.call('digico_presets',{'content_indices':indices})
        source=Path(self.temp.name)/'presets.json';source.write_text('{}')
        args={'request':preset_request(),'presets_path':str(source)}
        plan=adapter.call('digico_preset_execute',args)
        command=self.commands[-1][0]
        self.assertEqual(command[2],'execute');self.assertNotIn('--send',command)
        self.assertEqual(command[command.index('--presets')+1],str(source))
        self.assertTrue(plan['dry_run']);self.assertFalse(plan['windows_runner_invoked'])
        self.assertEqual(plan['compiled_mode'],'preset_execute')
        adapter.call('digico_plan',args)
        self.assertEqual(self.commands[-1][0][2],'plan')

        inventory=Path(self.temp.name)/'inventory.json';inventory.write_text('{}')
        args.update(send=True,inventory_path=str(inventory))
        sent=adapter.call('digico_preset_execute',args)
        command=self.commands[-1][0]
        self.assertIn('--send',command)
        self.assertEqual(command[command.index('--inventory')+1],str(inventory))
        self.assertFalse(sent['dry_run']);self.assertTrue(sent['windows_runner_invoked'])

    def test_structure_is_readonly_and_decodes_the_fresh_raw_record(self):
        summary=self.adapter().call('digico_structure',{'encoding':'cp950'})
        command=self.commands[-1][0]
        self.assertEqual(command[2],'structure');self.assertNotIn('--send',command)
        self.assertEqual(command[command.index('--encoding')+1],'cp950')
        self.assertTrue(summary['read_only'])
        self.assertEqual(summary['structure']['input_count_slots'][0],72)
        self.assertEqual(summary['structure']['sample_frequency'],48000)
        self.assertFalse(summary['structure']['headless_structure_setter_established'])
        self.assertNotIn('raw_hex',summary['structure'])
        for arguments in ({'send':True},{'address':123},{'encoding':'utf-8'}):
            with self.assertRaises(m.ToolError):self.adapter().call('digico_structure',arguments)
        for key,value in (('alias_verified',False),('consistent_double_read',False),
                          ('memory_read_only',False),('network_used',True),('raw_hex','00'),
                          ('executable_sha256','0'*64),('executable_hash_verified_by_caller',False),
                          ('alias_slot_rva',0),('headless_structure_setter_established',True)):
            state=structure_state();state[key]=value
            with self.assertRaises(m.ToolError):
                m.Adapter._summary('structure',{'structure':state},Path('missing'),Path('result'),False)

    def test_preset_verification_preserves_processing_and_audio_limits(self):
        result={'mode':'preset_execute','status':'ok','presets_after':preset_state(),
            'original_preset_contents_verified':True,'original_scope_restored':True,
            'verified_steps':[{'op':'preset_recall','verified':True,'preset_count':1,
                'metadata_verified':True,'stored_controller_subset_verified':False,
                'processing_verified':True,'audio_delivery_verified':False,
                'private_raw_marker':'DO_NOT_EXPAND_MODEL_RESULT'}]}
        out=m.Adapter._summary('execute',result,Path('missing'),Path('result'),True)
        self.assertTrue(out['preset_verification']['steps'][0]['processing_verified'])
        self.assertFalse(out['preset_verification']['steps'][0]['audio_delivery_verified'])
        self.assertTrue(out['preset_verification']['original_scope_restored'])
        self.assertNotIn('DO_NOT_EXPAND_MODEL_RESULT',json.dumps(out))

    def test_preset_schema_requires_scoped_recall_and_standalone_typed_ops(self):
        adapter=self.adapter();source=Path(self.temp.name)/'presets.json';source.write_text('{}')
        base={'op':'preset_recall','index':0,'group_name':'SyntheticGroup','channel_id':256,
            'scopes':['eq'],'verify_control_ids':['0704000001']}
        for operation in (base,{'op':'preset_set_scope','scopes':['eq']},
            {'op':'preset_set_scope','scope_hex':'00'*32},
            {'op':'preset_lock','index':0,'group_name':'SyntheticGroup','locked':True},
            {'op':'preset_delete','index':0,'group_name':'SyntheticGroup'},
            {'op':'preset_update','index':0,'group_name':'SyntheticGroup','channel_id':256},
            {'op':'preset_rename','index':0,'group_name':'SyntheticGroup','name':'SyntheticRenamed'}):
            args={'request':preset_request(operation),'presets_path':str(source)}
            adapter.call('digico_preset_execute',args)
        bad=dict(base);bad.pop('verify_control_ids')
        with self.assertRaises(m.ToolError):adapter.call('digico_preset_execute',{'request':preset_request(bad),'presets_path':str(source)})
        for operation in ({'op':'preset_set_scope','scopes':['eq'],'scope_hex':'00'*32},
                          {'op':'preset_set_scope','scopes':['unknown']},
                          {'op':'preset_set_scope','scopes':['eq','eq']}):
            with self.assertRaises(m.ToolError):adapter.call('digico_preset_execute',{'request':preset_request(operation),'presets_path':str(source)})
        with self.assertRaises(m.ToolError):adapter.call('digico_execute',{'request':preset_request()})
        mixed=preset_request();mixed['operations'].append({'op':'save'})
        with self.assertRaises(m.ToolError):adapter.call('digico_execute',{'request':mixed,'presets_path':str(source)})
        utf8=preset_request();utf8['encoding']='utf-8'
        with self.assertRaises(m.ToolError):adapter.call('digico_execute',{'request':utf8,'presets_path':str(source)})

    def test_real_cli_preset_plan_and_default_execute_are_offline(self):
        source=Path(self.temp.name)/'synthetic-presets.json'
        source.write_text(json.dumps({'status':'ok','executable_sha256':preset_state(0)['executable_sha256'],
            'presets':preset_state(0)}),encoding='utf-8')
        adapter=m.Adapter(vm='UNUSED',executable=r'C:\Synthetic\NeverRun.exe',work_dir=self.work)
        for name in ('digico_plan','digico_preset_execute'):
            summary=adapter.call(name,{'request':preset_request(),'presets_path':str(source)})
            self.assertTrue(summary['dry_run']);self.assertFalse(summary['windows_runner_invoked'])
            self.assertEqual(summary['compiled_mode'],'preset_execute')
            self.assertTrue(Path(summary['result_path']).is_file())
        self.assertFalse(list(self.work.glob('digico-native-runtime-*')))

    def test_catalog_is_local_filtered_and_bounded(self):
        source=Path(self.temp.name)/'inventory.json';source.write_text('{}')
        summary=self.adapter().call('digico_catalog',{'inventory_path':str(source),
            'channel_id':256,'module':12,'query':'fader','writable_only':True,'limit':7})
        command=self.commands[-1][0]
        self.assertEqual(command[2],'catalog');self.assertNotIn('--vm',command)
        self.assertEqual(command[command.index('--module')+1],'12')
        self.assertEqual(command[command.index('--limit')+1],'7')
        self.assertIn('--writable-only',command)
        self.assertFalse(summary['windows_runner_invoked'])
        self.assertEqual(summary['controls'][0]['id'],'210c000001')
        self.assertTrue(Path(summary['result_path']).is_file())
        for limit in (0,1001,True):
            with self.assertRaises(m.ToolError):self.adapter().call('digico_catalog',{'inventory_path':str(source),'limit':limit})
        large={'controls':[{'id':'210c000001','name':'x'*50000}]*10}
        bounded=m.Adapter._summary('catalog',large,Path('missing'),Path('result'),False)
        self.assertTrue(bounded['response_truncated'])
        self.assertLess(len(json.dumps(bounded)),64000)

    def test_typed_schema_forbids_raw_network_gui_and_unknown_ops(self):
        adapter=self.adapter()
        for arguments in ({'host':'192.0.2.1'},{'encoding':'made-up'}):
            with self.assertRaises(m.ToolError):adapter.call('digico_status',arguments)
        for operation in ({'op':'raw_packet','hex':'00'},{'op':'set_control','id':'00','value':1},
                          {'op':'set_channel_name','channel_id':True,'name':'x'}):
            r=raw_request();r['operations']=[operation]
            with self.assertRaises(m.ToolError):adapter.call('digico_plan',{'request':r})
        self.assertFalse(self.work.exists())

    def test_real_shaped_success_error_null_is_not_a_tool_error(self):
        summary=self.adapter().call('digico_status',{})
        self.assertEqual(summary['status'],'ok')
        raw=json.loads(Path(summary['result_path']).read_text())
        self.assertIn('error',raw);self.assertIsNone(raw['error'])
        server=m.Server(self.adapter())
        reply=server.handle(request('tools/call',{'name':'digico_status','arguments':{}}))
        self.assertFalse(reply['result']['isError'])
        self.assertEqual(reply['result']['structuredContent']['status'],'ok')
        def actual_error(command,**kwargs):
            return subprocess.CompletedProcess(command,0,stdout='{"status":"ok","error":"real failure"}',stderr='')
        with self.assertRaises(m.ToolError):self.adapter(actual_error).call('digico_status',{})

    def test_failed_cli_cannot_become_success_even_with_status_ok_file(self):
        def failed(command,**kwargs):
            self.runner(command,**kwargs)
            return subprocess.CompletedProcess(command,2,stdout='{"status":"ok"}',stderr='synthetic failure')
        with self.assertRaisesRegex(m.ToolError,'CLI failed'):
            self.adapter(failed).call('digico_inspect',{})
        self.assertTrue(list(self.work.glob('*.result.json')))

    def test_missing_result_or_malformed_stdout_never_confirms_success(self):
        def absent(command,**kwargs):return subprocess.CompletedProcess(command,0,stdout='{"status":"ok"}',stderr='')
        with self.assertRaisesRegex(m.ToolError,'absent'):
            self.adapter(absent).call('digico_inspect',{})
        def corrupt(command,**kwargs):return subprocess.CompletedProcess(command,0,stdout='diagnostic, not JSON',stderr='')
        with self.assertRaisesRegex(m.ToolError,'stdout'):
            self.adapter(corrupt).call('digico_plan',{'request':raw_request()})

    def test_timeout_keeps_request_and_reports_unknown_acceptance(self):
        def timeout(command,**kwargs):raise subprocess.TimeoutExpired(command,180)
        with self.assertRaisesRegex(m.ToolError,'acceptance is unknown'):
            self.adapter(timeout).call('digico_execute',{'request':raw_request(),'send':True})
        self.assertEqual(len(list(self.work.glob('*.request.json'))),1)

    def test_hidden_or_nonprivate_work_directory_is_rejected(self):
        if os.name!='nt':
            with self.assertRaises(m.ToolError):m.Adapter(vm='x',executable='x',work_dir=Path(self.temp.name)/'.hidden')
            self.work.mkdir(mode=0o755)
            with self.assertRaises(m.ToolError):self.adapter().call('digico_status',{})


class StdioProcessTests(unittest.TestCase):
    def test_real_stdio_discovery_plan_and_clean_eof(self):
        with tempfile.TemporaryDirectory(prefix='digico-mcp-stdio-') as directory:
            work=Path(directory)/'private'
            process=subprocess.Popen([sys.executable,str(SCRIPT),'--vm','UNUSED',
                '--exe',r'C:\Synthetic\NeverRun.exe','--work-dir',str(work)],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            messages=queue.Queue()
            def reader():
                for line in process.stdout:
                    try:messages.put(json.loads(line.decode('utf-8')))
                    except Exception as error:messages.put(error)
            thread=threading.Thread(target=reader);thread.start()
            def rpc(message):
                process.stdin.write((json.dumps(message)+'\n').encode('utf-8'));process.stdin.flush()
                reply=messages.get(timeout=5)
                if isinstance(reply,Exception):raise reply
                return reply
            try:
                self.assertEqual(rpc(request('server/discover'))['result']['resultType'],'complete')
                reply=rpc(request('tools/call',{'name':'digico_plan','arguments':{'request':raw_request()}},2))
                self.assertFalse(reply['result']['isError'])
                summary=reply['result']['structuredContent']
                self.assertTrue(summary['dry_run']);self.assertFalse(summary['windows_runner_invoked'])
                self.assertTrue(Path(summary['result_path']).is_file())
                self.assertFalse(list(work.glob('digico-native-runtime-*')))
                process.stdin.close();process.wait(timeout=5);thread.join(2)
                self.assertEqual(process.returncode,0)
                self.assertEqual(process.stderr.read(),b'')
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=5)
                thread.join(2)
                for stream in (process.stdin,process.stdout,process.stderr):
                    if stream and not stream.closed:stream.close()


class SequentialIsolationTests(unittest.TestCase):
    def test_two_slow_sequential_calls_do_not_lose_the_second(self):
        import time
        messages=queue.Queue();calls=[]
        class SlowAdapter:
            def call(self,name,args):
                time.sleep(0.03);calls.append(name);return {'call_number':len(calls)}
        class Sink:
            def write(self,data):messages.put(json.loads(data.decode()))
            def flush(self):pass
        readfd,writefd=os.pipe();source=os.fdopen(readfd,'rb',buffering=0);client=os.fdopen(writefd,'wb',buffering=0)
        thread=threading.Thread(target=m.serve,args=(m.Server(SlowAdapter()),source,Sink()));thread.start()
        try:
            for index,name in enumerate(('digico_status','digico_meters'),1):
                client.write((json.dumps(request('tools/call',{'name':name,'arguments':{}},index))+'\n').encode())
                response=messages.get(timeout=2)
                self.assertEqual(response['id'],index);self.assertFalse(response['result']['isError'])
                self.assertEqual(response['result']['structuredContent']['call_number'],index)
            self.assertEqual(calls,['digico_status','digico_meters'])
        finally:
            client.close();thread.join(3);source.close()
        self.assertFalse(thread.is_alive())

    def test_real_stdio_children_cannot_consume_protocol_stdin(self):
        import shutil
        with tempfile.TemporaryDirectory(prefix='digico-mcp-isolated-') as directory:
            folder=Path(directory);server_path=folder/'mcp_server.py'
            shutil.copyfile(SCRIPT,server_path)
            # This hermetic child intentionally reads stdin before answering.
            # It must see EOF, not the MCP parent's request pipe.
            (folder/'native_session.py').write_text("""import sys,json,pathlib
assert sys.stdin.read() == ''
mode=sys.argv[1];target=pathlib.Path(sys.argv[sys.argv.index('--result')+1])
r={'status':'ok','error':None,'mode':mode,'verified_steps':[]}
if mode=='meters':r['meters']={'memory_read_only':True,'network_used':False,'ui_used':False,'physical_audio_verified':False,'structure_verified':True,'values_atomic':False,'freshness':'not_observable','matching_count':0,'returned_count':0,'truncated':False,'manager':{},'meters':[]}
target.write_text(json.dumps(r),encoding='utf-8');print(json.dumps(r),flush=True)
""",encoding='utf-8')
            process=subprocess.Popen([sys.executable,str(server_path),'--vm','UNUSED','--exe','UNUSED',
                '--work-dir',str(folder/'private'),'--timeout-seconds','1'],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            messages=queue.Queue()
            def reader():
                for line in process.stdout:messages.put(json.loads(line.decode()))
            thread=threading.Thread(target=reader);thread.start()
            try:
                for index,name in enumerate(('digico_status','digico_meters','digico_status'),1):
                    message=request('tools/call',{'name':name,'arguments':{}},index)
                    process.stdin.write((json.dumps(message)+'\n').encode());process.stdin.flush()
                    response=messages.get(timeout=3)
                    self.assertEqual(response['id'],index);self.assertFalse(response['result']['isError'])
                process.stdin.close();process.wait(timeout=3);thread.join(2)
                self.assertEqual(process.returncode,0);self.assertEqual(process.stderr.read(),b'')
                self.assertEqual(len(list((folder/'private').glob('*.result.json'))),3)
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=3)
                thread.join(2)
                for stream in (process.stdin,process.stdout,process.stderr):
                    if stream and not stream.closed:stream.close()


class CancellationTests(unittest.TestCase):
    def test_cancelled_call_gets_no_reply_but_ping_still_works(self):
        started=threading.Event();release=threading.Event();messages=queue.Queue()
        class SlowAdapter:
            def call(self,name,args):
                started.set();release.wait(3);return {'synthetic':'completed'}
        class Sink:
            def write(self,data):messages.put(json.loads(data.decode()))
            def flush(self):pass
        readfd,writefd=os.pipe()
        source=os.fdopen(readfd,'rb',buffering=0);client=os.fdopen(writefd,'wb',buffering=0)
        thread=threading.Thread(target=m.serve,args=(m.Server(SlowAdapter()),source,Sink()))
        thread.start()
        try:
            client.write((json.dumps(request('tools/call',{'name':'digico_plan','arguments':{}},10))+'\n').encode())
            self.assertTrue(started.wait(2))
            client.write(b'{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":10}}\n')
            client.write((json.dumps(request('ping',identifier=11))+'\n').encode())
            self.assertEqual(messages.get(timeout=2)['id'],11)
            release.set();client.close();thread.join(3)
            self.assertFalse(thread.is_alive());self.assertTrue(messages.empty())
        finally:
            release.set()
            if not client.closed:client.close()
            thread.join(3);source.close()


if __name__=='__main__':unittest.main()

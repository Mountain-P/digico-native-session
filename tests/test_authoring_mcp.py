"""Safe MCP profile/authoring/recovery facade; real pure CLI or mocked target."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_native_mcp as base
import test_authoring as author
import test_authoring_workflow as workflow
from test_operation_journal import journal_bytes
from authoring import compile_authoring_request
from authoring_workflow import run_authoring
from operation_journal import read_journal, recovery_report

m = base.m
PRIVATE = 'PRIVATE_NATIVE_CONTENT_MUST_STAY_IN_ARTIFACT'
PROFILE = 'quantum1-v2126'


def author_request():
    return author.request(author.setting())


class SafeFacadeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='digico-mcp-facade-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'private'
        self.commands = []
        self.initial = workflow.wrapper()
        self.inventory = self.root / 'inventory.json'
        self.inventory.write_text(json.dumps(self.initial))
        self.compiled = self.root / 'compiled.json'
        self.compiled.write_text('{"synthetic_native_request":true}')
        digest = hashlib.sha256(self.compiled.read_bytes()).hexdigest()
        self.journal = self.root / 'journal.jsonl'
        self.journal.write_bytes(journal_bytes([
            ('step_started', {'operation_index': 0, 'private_payload': PRIVATE}),
            ('step_verified', {'operation_index': 0, 'receipt': {'verified': True, 'private_payload': PRIVATE}}),
            ('step_started', {'operation_index': 1})], request_hash=digest))

    def adapter(self, runner=None):
        return m.Adapter(vm='Synthetic VM', executable=r'C:\Synthetic\Quantum1.exe',
                         process_id=42, work_dir=self.work, run_process=runner or self.runner)

    def arguments(self):
        return {'request': author_request(), 'inventory_path': str(self.inventory)}

    def runner(self, command, **kwargs):
        mode = command[2]
        if mode not in ('profile', 'author', 'author-execute', 'recover'):
            return base.AdapterTests.runner(self, command, **kwargs)
        self.commands.append((command, kwargs))
        if mode == 'profile':
            result = {'build_profile': PROFILE, 'executable_sha256': self.initial['executable_sha256'],
                      'model': 'Quantum1', 'release': 'V2126',
                      'features': {'inventory': True, 'control_write': True},
                      'validation': {'offline_runtime_verified': True},
                      'network_used': False, 'target_called': False}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(result), stderr='')
        output_flag = '--output' if mode in ('author', 'recover') else '--result'
        result_path = Path(command[command.index(output_flag) + 1])
        if mode == 'recover':
            result = recovery_report(read_journal(command[command.index('--journal') + 1]),
                                     compiled_request_path=command[command.index('--compiled-request') + 1] if '--compiled-request' in command else None)
        else:
            request = json.loads(Path(command[command.index('--request') + 1]).read_text())
            inventory = json.loads(Path(command[command.index('--inventory') + 1]).read_text())
            result = compile_authoring_request(request, inventory)
            if mode == 'author-execute':
                if '--send' in command:
                    backend = workflow.Backend(inventory)
                    result = run_authoring(request, inventory, backend.execute, backend.refresh)
                    result.update(atomic=False, dry_run=False, artifacts=[],
                                  work_dir=str(result_path.parent / 'synthetic-author-work'),
                                  intent_path=str(result_path) + '.request.json')
                    result['stages'][0]['execution_result']['private_payload'] = PRIVATE
                else:
                    result.update(dry_run=True, windows_runner_invoked=False, network_used=False)
            result['private_native_record'] = PRIVATE
        if mode != 'author-execute' or '--send' in command:
            result_path.write_text(json.dumps(result), encoding='utf-8')
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(result), stderr='')

    def test_four_tools_advertise_typed_readonly_and_mutating_boundaries(self):
        self.assertEqual(m.SERVER_INFO['version'], '0.3.0')
        for name in ('digico_profile', 'digico_author', 'digico_recover'):
            self.assertTrue(m.TOOL_BY_NAME[name]['annotations']['readOnlyHint'])
            self.assertFalse(m.TOOL_BY_NAME[name]['annotations']['openWorldHint'])
        self.assertFalse(m.TOOL_BY_NAME['digico_author_execute']['annotations']['readOnlyHint'])
        self.assertFalse(m.TOOL_BY_NAME['digico_author_execute']['inputSchema']['properties']['send']['default'])

    def test_profile_is_local_bundled_metadata_without_target_arguments(self):
        summary = self.adapter().call('digico_profile', {'build_profile': PROFILE})
        command = self.commands[-1][0]
        self.assertEqual(command[2], 'profile')
        self.assertEqual(command[command.index('--build-profile') + 1], PROFILE)
        for option in ('--vm', '--exe', '--process-id', '--send', '--result'):
            self.assertNotIn(option, command)
        self.assertEqual(summary['build_profile'], PROFILE)
        self.assertFalse(summary['target_called'])
        self.assertFalse(summary['windows_runner_invoked'])
        self.assertTrue(Path(summary['result_path']).is_file())

    def test_author_preview_is_pure_and_full_plan_stays_in_the_private_artifact(self):
        summary = self.adapter().call('digico_author', self.arguments())
        command = self.commands[-1][0]
        self.assertEqual(command[2], 'author')
        self.assertIn('--output', command)
        self.assertNotIn('--vm', command)
        self.assertNotIn('--send', command)
        self.assertTrue(summary['dry_run'])
        self.assertTrue(summary['read_only'])
        self.assertFalse(summary['windows_runner_invoked'])
        self.assertEqual(summary['authoring']['operation_count'], 1)
        self.assertEqual(summary['authoring']['changes'][0]['intended'], {'value': -10, 'unit': 'native'})
        self.assertNotIn(PRIVATE, json.dumps(summary))
        self.assertIn(PRIVATE, Path(summary['result_path']).read_text())

    def test_author_execute_dryrun_send_and_fixed_target_use_distinct_artifacts(self):
        adapter = self.adapter()
        dryrun = adapter.call('digico_author_execute', self.arguments())
        command, options = self.commands[-1]
        self.assertEqual(command[2], 'author-execute')
        self.assertNotIn('--send', command)
        self.assertTrue(dryrun['dry_run'])
        self.assertFalse(dryrun['windows_runner_invoked'])
        sent = adapter.call('digico_author_execute', {**self.arguments(), 'send': True, 'build_profile': PROFILE})
        command = self.commands[-1][0]
        self.assertIn('--send', command)
        self.assertEqual(command[command.index('--vm') + 1], 'Synthetic VM')
        self.assertEqual(command[command.index('--exe') + 1], r'C:\Synthetic\Quantum1.exe')
        self.assertEqual(command[command.index('--process-id') + 1], '42')
        self.assertEqual(command[command.index('--build-profile') + 1], PROFILE)
        self.assertFalse(sent['dry_run'])
        self.assertTrue(sent['windows_runner_invoked'])
        self.assertEqual(sent['verified_operation_count'], 1)
        self.assertFalse(sent['auto_replay'])
        self.assertNotEqual(sent['result_path'], dryrun['result_path'])
        self.assertNotIn(PRIVATE, json.dumps(sent))
        self.assertIn(PRIVATE, Path(sent['result_path']).read_text())
        self.assertFalse(options['shell'])
        self.assertEqual(options['stdin'], subprocess.DEVNULL)

    def test_only_literal_boolean_send_can_execute_authoring(self):
        adapter = self.adapter()
        for bad in (1, 'true', None, [], {}):
            with self.subTest(send=bad), self.assertRaises(m.ToolError):
                adapter.call('digico_author_execute', {**self.arguments(), 'send': bad})
        self.assertEqual(self.commands, [])
        self.assertFalse(self.work.exists())

    def test_author_schema_rejects_ambiguous_selector_shapes_raw_writes_and_unknown_units(self):
        adapter = self.adapter()
        operations = [
            {'op': 'set', 'channel': {'id': 256, 'name': 'Synthetic'}, 'parameter': 'fader', 'value': {'value': -10, 'unit': 'native'}},
            {'op': 'set', 'channel': {'logical': 1, 'name': 'Synthetic'}, 'parameter': 'fader', 'value': {'value': -10, 'unit': 'native'}},
            {'op': 'set', 'channel': {'logical': True}, 'parameter': 'fader', 'value': {'value': -10, 'unit': 'native'}},
            {'op': 'set', 'channel': {'id': 256}, 'parameter': 'mute', 'value': {'value': True, 'unit': 'native'}},
            {'op': 'set', 'channel': {'id': 256}, 'parameter': 'pan', 'value': {'value': 50, 'unit': '%'}},
            {'op': 'set_control', 'id': '210c000001', 'value': -10},
            {'op': 'raw_packet', 'hex': '00'}]
        for operation in operations:
            request = author_request(); request['operations'] = [operation]
            with self.assertRaises(m.ToolError):
                adapter.call('digico_author', {'request': request, 'inventory_path': str(self.inventory)})
        self.assertEqual(self.commands, [])
        self.assertFalse(self.work.exists())

    def test_all_new_tools_forbid_per_call_host_process_gui_and_send_overrides(self):
        adapter = self.adapter()
        valid = {'digico_profile': {}, 'digico_author': self.arguments(),
                 'digico_author_execute': self.arguments(), 'digico_recover': {'journal_path': str(self.journal)}}
        for name, arguments in valid.items():
            for key, value in (('host', '192.0.2.1'), ('vm', 'Other VM'),
                               ('exe', r'C:\Other.exe'), ('process_id', 99), ('address', 123), ('osc', True)):
                with self.subTest(tool=name, field=key), self.assertRaises(m.ToolError):
                    adapter.call(name, {**arguments, key: value})
        for name in ('digico_profile', 'digico_author', 'digico_recover'):
            with self.assertRaises(m.ToolError):
                adapter.call(name, {**valid[name], 'send': True})
        self.assertEqual(self.commands, [])

    def test_recover_uses_existing_absolute_paths_and_never_targets_or_replays(self):
        before = (self.journal.read_bytes(), self.compiled.read_bytes())
        summary = self.adapter().call('digico_recover', {'journal_path': str(self.journal),
                                                        'compiled_request_path': str(self.compiled)})
        command = self.commands[-1][0]
        self.assertEqual(command[2], 'recover')
        self.assertEqual(command[command.index('--journal') + 1], str(self.journal))
        self.assertEqual(command[command.index('--compiled-request') + 1], str(self.compiled))
        self.assertIn('--output', command)
        for option in ('--vm', '--exe', '--send', '--result'):
            self.assertNotIn(option, command)
        self.assertEqual(summary['verified_operation_indices'], [0])
        self.assertEqual(summary['indeterminate_operation_indices'], [1])
        self.assertFalse(summary['target_called'])
        self.assertFalse(summary['automatic_replay_allowed'])
        self.assertNotIn(PRIVATE, json.dumps(summary))
        self.assertIn(PRIVATE, Path(summary['result_path']).read_text())
        self.assertEqual((self.journal.read_bytes(), self.compiled.read_bytes()), before)

    def test_local_input_validation_happens_before_artifact_creation(self):
        adapter = self.adapter()
        for tool, arguments in (
            ('digico_author', {'request': author_request(), 'inventory_path': 'relative.json'}),
            ('digico_recover', {'journal_path': 'relative.jsonl'}),
            ('digico_recover', {'journal_path': str(self.journal), 'compiled_request_path': 'relative.json'})):
            with self.assertRaises(m.ToolError):
                adapter.call(tool, arguments)
        self.assertFalse(self.work.exists())
        self.assertEqual(self.commands, [])

    def test_workflow_failure_reports_private_artifact_without_echoing_full_stdout(self):
        def failed(command, **kwargs):
            path = Path(command[command.index('--result') + 1])
            result = {'status': 'indeterminate', 'private_plan': PRIVATE, 'auto_replay': False}
            path.write_text(json.dumps(result))
            return subprocess.CompletedProcess(command, 2, stdout=json.dumps(result), stderr='')
        server = m.Server(self.adapter(failed))
        response = server.handle(base.request('tools/call', {'name': 'digico_author_execute',
            'arguments': {**self.arguments(), 'send': True}}))['result']
        self.assertTrue(response['isError'])
        summary = response['structuredContent']
        self.assertNotIn(PRIVATE, json.dumps(summary))
        self.assertIn('result_path', summary)
        self.assertIn(PRIVATE, Path(summary['result_path']).read_text())

    def test_build_profile_is_supported_only_where_the_cli_accepts_it(self):
        adapter = self.adapter()
        for name in ('digico_status', 'digico_inspect', 'digico_inventory', 'digico_meters', 'digico_structure', 'digico_presets'):
            summary = adapter.call(name, {'build_profile': PROFILE})
            command = self.commands[-1][0]
            self.assertEqual(command[command.index('--build-profile') + 1], PROFILE)
            self.assertNotIn('--send', command)
        request = base.raw_request(); request['build_profile'] = PROFILE
        adapter.call('digico_plan', {'request': request})
        command = self.commands[-1][0]
        self.assertNotIn('--build-profile', command)
        self.assertEqual(json.loads(Path(command[command.index('--request') + 1]).read_text())['build_profile'], PROFILE)
        adapter.call('digico_execute', {'request': request, 'build_profile': PROFILE})
        self.assertIn('--build-profile', self.commands[-1][0])
        with self.assertRaises(m.ToolError):
            adapter.call('digico_author', {**self.arguments(), 'build_profile': PROFILE})

    def test_real_profile_author_dryrun_and_recovery_commands_are_local_only(self):
        adapter = self.adapter(subprocess.run)
        for name, arguments in (
            ('digico_profile', {'build_profile': PROFILE}),
            ('digico_author', self.arguments()),
            ('digico_author_execute', self.arguments()),
            ('digico_recover', {'journal_path': str(self.journal), 'compiled_request_path': str(self.compiled)})):
            summary = adapter.call(name, arguments)
            self.assertFalse(summary['windows_runner_invoked'])
            self.assertTrue(Path(summary['result_path']).is_file())
        self.assertFalse(list(self.work.glob('digico-native-runtime-*')))
        self.assertFalse(list(self.work.glob('author-*')))
        with self.assertRaises(m.ToolError):
            adapter.call('digico_profile', {'build_profile': 'quantum-unsupported-build'})

    def test_authoring_summary_bounds_changes_and_does_not_expand_raw_stage_requests(self):
        request = author_request(); request['operations'] *= 40
        summary = self.adapter().call('digico_author', {'request': request, 'inventory_path': str(self.inventory)})
        self.assertEqual(summary['authoring']['operation_count'], 40)
        self.assertEqual(len(summary['authoring']['changes']), 25)
        self.assertTrue(summary['authoring']['response_truncated'])
        self.assertNotIn('raw_request', json.dumps(summary))
        self.assertNotIn(PRIVATE, json.dumps(summary))


if __name__ == '__main__':
    unittest.main()

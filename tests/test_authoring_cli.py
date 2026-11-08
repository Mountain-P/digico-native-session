"""CLI publication/lifecycle tests with a synthetic backend; no VM or network."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/digico-native-session/scripts'))
import native_session as native
import test_authoring as author
import test_authoring_workflow as workflow
import test_control_catalog as vectors
from target_lock import TargetBusyError


class LeaseProbe:
    def __init__(self):
        self.active = False
        self.entries = 0
        self.exits = 0
        self.targets = []

    @contextlib.contextmanager
    def __call__(self, *, vm, executable):
        if self.active:
            raise AssertionError('nested host lease acquisition')
        self.targets.append((vm, executable))
        self.entries += 1
        self.active = True
        try:
            yield
        finally:
            self.active = False
            self.exits += 1


class NativeBackend:
    def __init__(self, initial, lease, failure=None, failure_call=0):
        self.backend = workflow.Backend(initial)
        self.lease = lease
        self.failure = failure
        self.failure_call = failure_call
        self.calls = []

    def __call__(self, plan, *, vm, executable, result_path, process_id=None):
        if not self.lease.active:
            raise AssertionError('native backend invoked outside the whole-workflow host lease')
        path = Path(result_path)
        index = len(self.calls)
        self.calls.append({'plan': copy.deepcopy(plan), 'path': path, 'vm': vm,
                           'executable': executable, 'process_id': process_id})
        # Simulate exactly the artifacts that can survive a backend timeout.
        native.atomic_json(Path(str(path) + '.request.json'), plan, exclusive=True)
        Path(str(path) + '.journal.jsonl').write_text('{"synthetic":"attempt_started"}\n', encoding='utf-8')
        if self.failure == 'raise_after_artifact' and index == self.failure_call:
            raise native.ContractError('synthetic transport timeout; acceptance is unknown')
        if plan['mode'] == 'inventory':
            result = copy.deepcopy(self.backend.state)
            selected = plan['channel_id']
            source = result['inventory']
            source['channels'] = [row for row in source['channels'] if row['id'] == selected]
            source['controllers'] = [row for row in source['controllers']
                                     if int.from_bytes(bytes.fromhex(row['raw_id_hex'])[3:5], 'little') == selected]
            used = {row['definition_pointer'] for row in source['controllers']}
            source['definitions'] = [row for row in source['definitions'] if row['pointer'] in used]
            source['selection'] = {'full_tree': False, 'channel_id': selected}
            result['sample_id'] = 'synthetic-refresh-%d' % index
        else:
            raw = {key: plan[key] for key in ('schema_version', 'fps', 'encoding', 'expected_count')}
            operations = []
            for step in plan['steps']:
                if step['op'] == 'set_control':
                    operations.append({'op': 'set_control', 'id': step['id'],
                                       'value': step['control_contract']['value'],
                                       'expected': step['control_contract']['expected']})
                else:
                    operations.append({'op': 'set_channel_name', 'channel_id': step['channel_id'], 'name': step['name']})
            raw['operations'] = operations
            result = self.backend.execute(raw, copy.deepcopy(self.backend.state), index)
        if index == self.failure_call:
            if self.failure == 'error':
                result.update(status='error', error='synthetic native rejection', verified_steps=[])
            elif self.failure == 'incomplete_success':
                result['verified_steps'] = []
        result['request_sha256'] = hashlib.sha256(Path(str(path) + '.request.json').read_bytes()).hexdigest()
        native.atomic_json(path, result, exclusive=True)
        return result


def two_channel_inventory():
    result = workflow.wrapper(author.eq_inventory(1))
    source = result['inventory']
    other = copy.deepcopy(source['channels'][0])
    other.update(id=257, pointer=0x900100, logical_channel=2, name='Synthetic Other', name_hex=b'Synthetic Other'.hex())
    source['channels'].append(other)
    fader = vectors.inventory()
    row = copy.deepcopy(fader['controllers'][0])
    row.update(raw_id_hex='210c000101', module_id_hex='000c000101', definition_pointer=0x500300,
               pointer=0x800300, owner_module_pointer=0x700300)
    definition = copy.deepcopy(fader['definitions'][0]); definition['pointer'] = 0x500300
    source['controllers'].append(row); source['definitions'].append(definition)
    return result


class AuthoringCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='synthetic-author-cli-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'authoring.json'
        self.inventory = self.root / 'inventory.json'
        self.result = self.root / 'workflow.json'
        self.document = author.request(author.setting())
        self.initial = workflow.wrapper()
        self.write_inputs()

    def write_inputs(self):
        self.source.write_text(json.dumps(self.document, ensure_ascii=False), encoding='utf-8')
        self.inventory.write_text(json.dumps(self.initial, ensure_ascii=False), encoding='utf-8')

    def args(self, command='author-execute', *, send=False):
        args = [command, '--request', str(self.source), '--inventory', str(self.inventory)]
        if command == 'author-execute':
            args += ['--vm', 'Synthetic VM', '--exe', r'C:\Synthetic\Quantum1.exe',
                     '--process-id', '42', '--result', str(self.result)]
        if send:
            args.append('--send')
        return args

    def invoke(self, args, backend=None, lease=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        actor = SimpleNamespace(__wrapped__=backend if backend is not None else mock.Mock(side_effect=AssertionError('must not invoke a backend')))
        lease = lease if lease is not None else mock.Mock(side_effect=AssertionError('must not acquire a target lease'))
        with mock.patch.object(native, 'invoke_windows', actor), mock.patch.object(native, 'target_lock', lease), \
             mock.patch.object(native.subprocess, 'run', side_effect=AssertionError('no VM or subprocess is authorized by these tests')), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = native.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_author_preview_writes_only_requested_plan_and_calls_no_backend(self):
        output = self.root / 'preview.json'
        code, stdout, stderr = self.invoke(self.args('author') + ['--output', str(output)])
        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result['kind'], 'authoring_plan')
        self.assertEqual(json.loads(output.read_text()), result)
        self.assertEqual(result['stages'][0]['raw_request']['operations'][0]['id'], '210c000001')
        self.assertFalse(self.result.exists())
        self.assertEqual(list(self.root.glob('author-*')), [])

    def test_default_execute_is_preview_and_does_not_touch_an_existing_result(self):
        self.result.write_text('existing synthetic evidence', encoding='utf-8')
        code, stdout, stderr = self.invoke(self.args())
        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        self.assertTrue(result['dry_run'])
        self.assertFalse(result['network_used'])
        self.assertFalse(result['windows_runner_invoked'])
        self.assertEqual(self.result.read_text(), 'existing synthetic evidence')
        self.assertFalse(Path(str(self.result) + '.request.json').exists())
        self.assertEqual(list(self.root.glob('author-*')), [])

    def test_explicit_profile_mismatch_is_rejected_in_dry_run_and_send(self):
        for send in (False, True):
            code, stdout, stderr = self.invoke(self.args(send=send) + ['--build-profile', 'quantum-other'])
            self.assertEqual(code, 2)
            self.assertEqual(stdout, '')
            self.assertIn('--build-profile differs', json.loads(stderr)['error'])
            self.assertFalse(self.result.exists())
            self.assertEqual(list(self.root.glob('author-*')), [])

    def test_send_publishes_fresh_intent_private_workdir_and_exact_native_artifacts(self):
        lease = LeaseProbe(); backend = NativeBackend(self.initial, lease)
        code, stdout, stderr = self.invoke(self.args(send=True), backend, lease)
        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(json.loads(self.result.read_text()), result)
        self.assertEqual(result['status'], 'ok')
        self.assertFalse(result['dry_run'])
        self.assertFalse(result['atomic'])
        work_dir = Path(result['work_dir'])
        intent_path = Path(str(self.result) + '.request.json')
        intent = json.loads(intent_path.read_text())
        self.assertEqual(intent['kind'], 'authoring_workflow_intent')
        self.assertEqual(intent['request'], self.document)
        self.assertEqual(intent['work_dir'], str(work_dir))
        self.assertEqual(result['intent_path'], str(intent_path))
        self.assertEqual(work_dir.parent, self.result.parent)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(work_dir.stat().st_mode), 0o700)
        self.assertEqual((lease.entries, lease.exits, lease.active), (1, 1, False))
        self.assertEqual(lease.targets, [('Synthetic VM', r'C:\Synthetic\Quantum1.exe')])
        self.assertEqual(len(backend.calls), 1)
        call = backend.calls[0]
        self.assertEqual(call['process_id'], 42)
        self.assertEqual(call['plan']['mode'], 'execute')
        self.assertNotIn('kind', call['plan'])
        self.assertEqual(call['path'], work_dir / 'stage-000.json')
        self.assertEqual(result['artifacts'][0]['path'], str(call['path']))
        self.assertTrue(Path(str(call['path']) + '.request.json').is_file())
        self.assertTrue(Path(str(call['path']) + '.journal.jsonl').is_file())

    def test_mode_barrier_uses_bound_inventory_plan_and_one_lease_for_all_callbacks(self):
        self.initial = workflow.wrapper(author.eq_inventory(1))
        self.document = author.request(author.setting('eq.shape', 4), author.setting('eq.q', .8))
        self.write_inputs()
        lease = LeaseProbe(); backend = NativeBackend(self.initial, lease)
        code, stdout, stderr = self.invoke(self.args(send=True), backend, lease)
        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual([call['plan']['mode'] for call in backend.calls], ['execute', 'inventory', 'execute'])
        refresh = backend.calls[1]['plan']
        self.assertEqual(refresh['channel_id'], 256)
        self.assertTrue(refresh['include_response_tables'])
        self.assertFalse(refresh['include_physical_sources'])
        self.assertFalse(refresh['include_group_outputs'])
        self.assertEqual(refresh['build_profile'], 'quantum1-v2126')
        self.assertEqual(refresh['executable_sha256'], self.initial['executable_sha256'])
        self.assertAlmostEqual(backend.calls[2]['plan']['steps'][0]['control_contract']['expected'], .6)
        self.assertEqual((lease.entries, lease.exits), (1, 1))
        self.assertEqual([artifact['kind'] for artifact in result['artifacts']], ['execute', 'inventory', 'execute'])
        self.assertEqual(result['completed_stages'], 2)

    def test_multi_channel_barrier_refreshes_each_raw_channel_then_merges(self):
        self.initial = two_channel_inventory()
        self.document = author.request(author.setting('eq.shape', 4), author.setting('eq.q', .8),
                                       author.setting('fader', -10, channel={'id': 257}))
        self.write_inputs()
        lease = LeaseProbe(); backend = NativeBackend(self.initial, lease)
        code, stdout, stderr = self.invoke(self.args(send=True), backend, lease)
        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        refreshes = [call for call in backend.calls if call['plan']['mode'] == 'inventory']
        self.assertEqual([call['plan']['channel_id'] for call in refreshes], [256, 257])
        self.assertEqual([call['path'].name for call in refreshes], ['refresh-001-0100.json', 'refresh-001-0101.json'])
        self.assertEqual(result['refreshes'][0]['merge_provenance']['source_count'], 2)
        final = backend.calls[-1]['plan']['steps']
        self.assertEqual([step['id'] for step in final], ['0604000001', '210c000101'])

    def test_final_mode_refresh_is_an_artifact_without_replaying_the_mode(self):
        self.initial = workflow.wrapper(author.eq_inventory(1))
        self.document = author.request(author.setting('eq.shape', 4)); self.write_inputs()
        lease = LeaseProbe(); backend = NativeBackend(self.initial, lease)
        code, stdout, stderr = self.invoke(self.args(send=True), backend, lease)
        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual([call['plan']['mode'] for call in backend.calls], ['execute', 'inventory'])
        self.assertIn('final_inventory_binding', result)
        self.assertEqual(result['artifacts'][-1]['stage'], 1)

    def test_existing_result_or_intent_is_never_overwritten_and_no_work_is_started(self):
        for suffix in ('', '.request.json'):
            occupied = Path(str(self.result) + suffix)
            occupied.write_text('synthetic sentinel', encoding='utf-8')
            code, stdout, stderr = self.invoke(self.args(send=True))
            self.assertEqual(code, 2)
            self.assertEqual(stdout, '')
            self.assertIn('fresh absolute', json.loads(stderr)['error'])
            self.assertEqual(occupied.read_text(), 'synthetic sentinel')
            self.assertEqual(list(self.root.glob('author-*')), [])
            occupied.unlink()

    def test_relative_send_result_rejects_before_creating_intent_or_workdir(self):
        args = self.args(send=True)
        args[args.index('--result') + 1] = 'relative-synthetic-workflow.json'
        code, stdout, stderr = self.invoke(args)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, '')
        self.assertIn('fresh absolute', json.loads(stderr)['error'])
        self.assertEqual(list(self.root.glob('author-*')), [])

    def test_workdir_uuid_collision_does_not_reuse_existing_artifacts(self):
        token = 'a' * 32
        collision = self.root / ('author-' + token[:12]); collision.mkdir()
        sentinel = collision / 'stage-000.json'; sentinel.write_text('original evidence')
        with mock.patch.object(native.uuid, 'uuid4', return_value=SimpleNamespace(hex=token)):
            code, stdout, stderr = self.invoke(self.args(send=True))
        self.assertEqual(code, 2)
        self.assertEqual(stdout, '')
        self.assertIn('File exists', json.loads(stderr)['error'])
        self.assertEqual(sentinel.read_text(), 'original evidence')
        self.assertFalse(Path(str(self.result) + '.request.json').exists())

    def test_intent_claim_race_preserves_other_writer_and_never_invokes_backend(self):
        original_atomic = native.atomic_json
        intent_path = Path(str(self.result) + '.request.json')
        def race(path, document, **kwargs):
            if Path(path) == intent_path:
                intent_path.write_text('synthetic concurrent intent owner', encoding='utf-8')
            return original_atomic(path, document, **kwargs)
        with mock.patch.object(native, 'atomic_json', side_effect=race):
            code, stdout, stderr = self.invoke(self.args(send=True))
        self.assertEqual(code, 2)
        self.assertEqual(stdout, '')
        self.assertIn(str(intent_path), json.loads(stderr)['error'])
        self.assertEqual(intent_path.read_text(), 'synthetic concurrent intent owner')
        self.assertFalse(self.result.exists())

    def test_final_publication_race_preserves_native_receipts_and_does_not_replay(self):
        original_atomic = native.atomic_json
        lease = LeaseProbe(); backend = NativeBackend(self.initial, lease)
        def race(path, document, **kwargs):
            if Path(path) == self.result:
                self.result.write_text('synthetic concurrent result owner', encoding='utf-8')
            return original_atomic(path, document, **kwargs)
        with mock.patch.object(native, 'atomic_json', side_effect=race):
            code, stdout, stderr = self.invoke(self.args(send=True), backend, lease)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, '')
        self.assertIn(str(self.result), json.loads(stderr)['error'])
        self.assertEqual(self.result.read_text(), 'synthetic concurrent result owner')
        self.assertEqual(len(backend.calls), 1)
        intent = json.loads(Path(str(self.result) + '.request.json').read_text())
        self.assertTrue(Path(intent['work_dir']).is_dir())
        self.assertEqual(json.loads(backend.calls[0]['path'].read_text())['status'], 'ok')
        self.assertTrue(Path(str(backend.calls[0]['path']) + '.journal.jsonl').is_file())
        self.assertEqual((lease.entries, lease.exits, lease.active), (1, 1, False))

    def test_backend_error_publishes_reviewable_failure_and_retains_artifacts(self):
        lease = LeaseProbe(); backend = NativeBackend(self.initial, lease, failure='error')
        code, stdout, stderr = self.invoke(self.args(send=True), backend, lease)
        self.assertEqual(code, 2, stderr)
        result = json.loads(stdout)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['completed_stages'], 0)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(result['stages'][0]['execution_result']['error'], 'synthetic native rejection')
        self.assertEqual(json.loads(self.result.read_text()), result)
        self.assertTrue(Path(result['intent_path']).is_file())
        self.assertTrue(Path(result['artifacts'][0]['path']).is_file())
        self.assertFalse(result['auto_replay'])

    def test_timeout_after_backend_writes_request_keeps_attempted_artifact_in_manifest(self):
        lease = LeaseProbe(); backend = NativeBackend(self.initial, lease, failure='raise_after_artifact')
        code, stdout, stderr = self.invoke(self.args(send=True), backend, lease)
        self.assertEqual(code, 2, stderr)
        result = json.loads(stdout)
        self.assertEqual(result['status'], 'indeterminate')
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(len(result['artifacts']), 1)
        attempted = Path(result['artifacts'][0]['path'])
        self.assertEqual(attempted, backend.calls[0]['path'])
        self.assertFalse(attempted.exists())
        self.assertTrue(Path(str(attempted) + '.request.json').exists())
        self.assertTrue(Path(str(attempted) + '.journal.jsonl').exists())
        self.assertTrue(self.result.is_file())
        self.assertFalse(result['auto_replay'])

    def test_refresh_transport_failure_stops_after_verified_mode_and_retains_attempt(self):
        self.initial = workflow.wrapper(author.eq_inventory(1))
        self.document = author.request(author.setting('eq.shape', 4), author.setting('eq.q', .8))
        self.write_inputs()
        lease = LeaseProbe(); backend = NativeBackend(self.initial, lease, failure='raise_after_artifact', failure_call=1)
        code, stdout, stderr = self.invoke(self.args(send=True), backend, lease)
        self.assertEqual(code, 2, stderr)
        result = json.loads(stdout)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['failure_phase'], 'refresh')
        self.assertEqual(result['completed_stages'], 1)
        self.assertEqual([call['plan']['mode'] for call in backend.calls], ['execute', 'inventory'])
        self.assertEqual([item['kind'] for item in result['artifacts']], ['execute', 'inventory'])
        self.assertTrue(Path(str(backend.calls[1]['path']) + '.request.json').exists())
        self.assertEqual(result['remaining_authoring_requests'][0]['operations'][0]['parameter'], 'id:0604000001')
        self.assertEqual((lease.entries, lease.exits), (1, 1))

    def test_success_without_complete_verification_is_indeterminate_and_never_retried(self):
        lease = LeaseProbe(); backend = NativeBackend(self.initial, lease, failure='incomplete_success')
        code, stdout, stderr = self.invoke(self.args(send=True), backend, lease)
        self.assertEqual(code, 2, stderr)
        self.assertEqual(json.loads(stdout)['status'], 'indeterminate')
        self.assertEqual(len(backend.calls), 1)

    def test_busy_target_retains_intent_and_never_calls_backend(self):
        lock = mock.Mock(side_effect=TargetBusyError('synthetic host target already busy'))
        code, stdout, stderr = self.invoke(self.args(send=True), lease=lock)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, '')
        self.assertIn('already busy', json.loads(stderr)['error'])
        self.assertTrue(Path(str(self.result) + '.request.json').is_file())
        self.assertFalse(self.result.exists())

    def test_invalid_request_does_not_publish_an_execution_intent(self):
        self.document['operations'][0]['parameter'] = 'unverified semantic parameter'
        self.write_inputs()
        code, stdout, stderr = self.invoke(self.args(send=True))
        self.assertEqual(code, 2)
        self.assertEqual(stdout, '')
        self.assertIn('unknown_parameter', json.loads(stderr)['error'])
        self.assertFalse(Path(str(self.result) + '.request.json').exists())
        self.assertEqual(list(self.root.glob('author-*')), [])


if __name__ == '__main__':
    unittest.main()

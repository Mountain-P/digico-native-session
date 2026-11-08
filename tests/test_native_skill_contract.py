"""Contract and independent wire-vector checks; no VM, socket or UI operations."""
import base64
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills' / 'digico-native-session' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import native_session as native


def request(*operations, count=0, encoding='ascii'):
    return {'schema_version': 1, 'fps': 25, 'encoding': encoding,
            'expected_count': count, 'operations': list(operations)}


def wire(hex_string):
    """Independent minimal checker, deliberately not the bundled decoder."""
    data = bytes.fromhex(hex_string)
    size, connection, category = struct.unpack_from('<IHB', data)
    if size != len(data) or size < 20:
        raise AssertionError('bad frame size')
    if (sum(data[:-2]) % 65536) != int.from_bytes(data[-2:], 'little'):
        raise AssertionError('bad frame checksum')
    return SimpleNamespace(raw=data, category=category, connection=connection,
                           control=data[7:12], index=int.from_bytes(data[12:16], 'little', signed=True),
                           body=data[16:-2])


def fake_bundle(root):
    scripts = root / 'scripts'; scripts.mkdir(parents=True)
    script = scripts / 'windows_native.ps1'; script.write_text('# Never executed\n')
    assets = root / 'assets'; assets.mkdir()
    (assets / 'empty-template.json').write_text('{"filename":"empty.ses"}\n')
    (assets / 'empty.ses').write_bytes(b'Synthetic resource, not a native session')
    return script


class NativeOperationContractTests(unittest.TestCase):
    def test_create_name_and_quarterframe_vectors(self):
        plan = native.compile_request(request({'op': 'create', 'index': 'append', 'name': 'ASCIItest1',
                                              'timecode': '00:01:23:12'}))
        step = plan['steps'][0]
        self.assertEqual(step['index'], 0)
        self.assertEqual(step['expected_count_after'], 1)
        self.assertEqual(step['time_quarterframes'], 8348)
        # Literal vectors independently checked against the task's successful native trace.
        self.assertEqual(step['frames'], [
            '16000000ff0001c90000080700000000ffffffffea05',
            '20000000ff0003cd00000807000000000a000000415343494974657374316205',
            '16000000ff0001b500001507000000009c200000a302',
        ])
        self.assertFalse(plan['runtime_acceptance_verified'])

    def test_delete_rename_save_and_file_wire_vectors(self):
        cases = [
            ({'op': 'delete', 'index': 0}, 1, '16000000ff0001ce000008070000000000000000f301'),
            ({'op': 'rename', 'index': 0, 'name': 'A'}, 1, '17000000ff0003cd000008070000000001000000413702'),
            ({'op': 'save'}, 1, '16000000ff0001000000a6010000000000000000bd01'),
            ({'op': 'save_as', 'path': 'C:\\T.ses'}, 1, '1e000000ff0003000000a5010000000008000000433a5c542e7365737404'),
            ({'op': 'load', 'path': 'C:\\T.ses'}, 1, '1e000000ff00030000009b010000000008000000433a5c542e7365736a04'),
        ]
        for operation, count, expected in cases:
            with self.subTest(op=operation['op']):
                step = native.compile_request(request(operation, count=count))['steps'][0]
                self.assertEqual(step['frames'], [expected])
                wire(step['frames'][0])

    def test_count_simulation_tracks_insert_and_delete(self):
        plan = native.compile_request(request(
            {'op': 'create', 'index': 0, 'name': 'Inserted'},
            {'op': 'delete', 'index': 1},
            {'op': 'rename', 'index': 1, 'name': 'Shifted'},
            {'op': 'create', 'index': 'append', 'name': 'Tail'},
            {'op': 'timecode', 'index': 2, 'timecode': '00:00:00:01'},
            {'op': 'save_as', 'path': 'C:/Projects/Test.ses'}, count=2))
        self.assertEqual([step['expected_count_after'] for step in plan['steps']], [3, 2, 2, 3, 3, 3])
        self.assertEqual([step['index'] for step in plan['steps'][:5]], [0, 1, 1, 2, 2])
        self.assertEqual(plan['steps'][-1]['path'], 'C:\\Projects\\Test.ses')
        self.assertEqual(plan['final_expected_count'], 3)
        for step in plan['steps']:
            for raw in step['frames']:
                self.assertEqual(wire(raw).connection, 255)
        timing = wire(plan['steps'][4]['frames'][0])
        self.assertEqual((timing.control.hex(), timing.index, struct.unpack('<i', timing.body)[0]),
                         ('b500001507', 2, 4))

    def test_load_and_new_session_do_not_guess_loaded_count(self):
        for op in ('load', 'new_session'):
            with self.subTest(op=op):
                plan = native.compile_request(request({'op': op, 'path': 'C:\\New.ses'}, count=9))
                self.assertEqual(plan['expected_count'], 9)
                self.assertIsNone(plan['final_expected_count'])
                self.assertIsNone(plan['steps'][0]['expected_count_after'])
        new = native.compile_request(request({'op': 'new_session', 'path': 'C:\\New.ses'}, count=9))
        self.assertEqual(new['steps'][0]['frames'], [])
        self.assertFalse(new['steps'][0]['overwrite'])

    def test_index_constraints_after_prior_operations(self):
        invalid = [request({'op': 'create', 'index': 1, 'name': 'Gap'}),
                   request({'op': 'rename', 'index': 0, 'name': 'Missing'}),
                   request({'op': 'delete', 'index': 0}),
                   request({'op': 'timecode', 'index': 0, 'timecode': '00:00:00:00'}),
                   request({'op': 'delete', 'index': 0}, {'op': 'rename', 'index': 0, 'name': 'Gone'}, count=1)]
        for value in (True, False, -1, 0.0, '0', None):
            invalid.append(request({'op': 'create', 'index': value, 'name': 'Bad index'}))
        for document in invalid:
            with self.subTest(document=document), self.assertRaises(native.ContractError):
                native.compile_request(document)

    def test_strict_schema_and_final_file_operation(self):
        invalid = [
            request({'op': 'create', 'index': 0, 'name': 'A', 'frames': ['injection']}),
            request({'op': 'save', 'path': 'C:\\ignored.ses'}),
            request({'op': 'rename', 'index': 0, 'name': 'A', 'encoding': 'cp950'}, count=1),
            request({'op': 'not_an_opcode'}), request(),
            request({'op': 'load', 'path': 'C:\\A.ses'}, {'op': 'save'}),
            request({'op': 'new_session', 'path': 'C:\\A.ses'}, {'op': 'save'}),
            request({'op': 'save'}, {'op': 'create', 'index': 0, 'name': 'A'}),
            request({'op': 'save_as', 'path': 'C:\\A.ses', 'overwrite': 1}),
        ]
        extra = request({'op': 'save'}); extra['host'] = '192.0.2.1'; invalid.append(extra)
        missing = request({'op': 'save'}); del missing['expected_count']; invalid.append(missing)
        boolean = request({'op': 'save'}); boolean['schema_version'] = True; invalid.append(boolean)
        boolean = request({'op': 'save'}); boolean['expected_count'] = False; invalid.append(boolean)
        for document in invalid:
            with self.subTest(document=document), self.assertRaises(native.ContractError):
                native.compile_request(document)

    def test_strict_byte_names_no_replacement_or_truncation(self):
        for encoding, valid, invalid in [('ascii', 'A' * 31, 'A' * 32),
                                         ('cp950', '中' * 15 + 'A', '中' * 16),
                                         ('utf-8', '中' * 10 + 'A', '中' * 11)]:
            with self.subTest(encoding=encoding):
                step = native.compile_request(request({'op': 'create', 'index': 0, 'name': valid}, encoding=encoding))['steps'][0]
                raw = wire(step['frames'][1])
                self.assertEqual(int.from_bytes(raw.body[:4], 'little'), 31)
                self.assertEqual(raw.body[4:], valid.encode(encoding))
                self.assertEqual(step['name'], valid)
                with self.assertRaises(native.ContractError):
                    native.compile_request(request({'op': 'create', 'index': 0, 'name': invalid}, encoding=encoding))
        for name in ('', 'A\0B', '😀'):
            with self.subTest(name=name), self.assertRaises(native.ContractError):
                native.compile_request(request({'op': 'create', 'index': 0, 'name': name}, encoding='cp950'))

    def test_timecode_units_and_boundaries(self):
        for text, expected in [('00:00:00:00', 0), ('00:00:00:24', 96), ('00:00:01:00', 100),
                               ('00:01:23:12', 8348), ('23:59:59:24', 8639996)]:
            with self.subTest(text=text):
                self.assertEqual(native.timecode_quarterframes(text), expected)
        for text in ('00:00:00:25', '24:00:00:00', '00:60:00:00', '00:00:60:00',
                     '00:00:00.12', '0:00:00:00', '００:00:00:00', 1):
            with self.subTest(text=text), self.assertRaises(native.ContractError):
                native.timecode_quarterframes(text)
        for fps in (24, 30, 29.97, True, '25'):
            with self.subTest(fps=fps), self.assertRaises(native.ContractError):
                native.timecode_quarterframes('00:00:00:00', fps)

    def test_windows_path_constraints_and_shortstring_bound(self):
        accepted = 'C:\\' + 'A' * 248 + '.ses'
        self.assertEqual(len(accepted), 255)
        self.assertEqual(native.session_path(accepted), accepted)
        bad = ['relative.ses', 'C:relative.ses', '\\\\server\\share\\file.ses',
               'C:\\Projects\\..\\file.ses', 'C:\\A\0.ses', 'C:\\Chinese中文.ses',
               'C:\\A?.ses', 'C:\\A|B.ses', 'C:\\A:stream.ses', 'C:\\A.txt',
               'C:\\CON.ses', 'C:\\bad \\file.ses', accepted + 'x', 'C:\\' + 'A' * 249 + '.ses']
        for path in bad:
            with self.subTest(path=path), self.assertRaises(native.ContractError):
                native.session_path(path)

    def test_default_overwrite_is_false_and_explicit_boolean_is_retained(self):
        for overwrite in (False, True):
            step = native.compile_request(request({'op': 'save_as', 'path': 'C:\\A.ses', 'overwrite': overwrite}))['steps'][0]
            self.assertIs(step['overwrite'], overwrite)
        step = native.compile_request(request({'op': 'save_as', 'path': 'C:\\A.ses'}))['steps'][0]
        self.assertIs(step['overwrite'], False)

    def test_local_resource_bounds_are_not_console_capacity_claims(self):
        plan = native.compile_request(request({'op': 'save'}, count=2000))
        self.assertEqual(plan['final_expected_count'], 2000)
        plan = native.compile_request(request({'op': 'create', 'index': 'append', 'name': 'Last'}, count=1999))
        self.assertEqual(plan['final_expected_count'], 2000)
        for document in (request({'op': 'save'}, count=2001),
                         request({'op': 'create', 'index': 'append', 'name': 'Beyond bound'}, count=2000)):
            with self.subTest(document=document), self.assertRaises(native.ContractError):
                native.compile_request(document)
        operations = [{'op': 'rename', 'index': 0, 'name': 'A'} for _ in range(1000)]
        self.assertEqual(len(native.compile_request(request(*operations, count=1))['steps']), 1000)
        with mock.patch.object(native, '_frame', side_effect=AssertionError('resource bound must be checked first')):
            with self.assertRaisesRegex(native.ContractError, 'resource bound'):
                native.compile_request(request(*(operations + operations[:1]), count=1))


class NativeCliAndPublicationTests(unittest.TestCase):
    def authenticated_mac_launcher(self, command, stage):
        bootstrap = base64.b64decode(command[-1]).decode('utf-16le')
        launcher = Path(stage) / 'invoke.ps1'
        self.assertTrue(launcher.is_file())
        content = launcher.read_text(encoding='utf-8')
        self.assertIn(hashlib.sha256(launcher.read_bytes()).hexdigest(), bootstrap)
        self.assertIn(native.mac_guest_path(launcher).replace("'", "''"), bootstrap)
        self.assertIn('Runtime launcher hash mismatch', bootstrap)
        self.assertLess(bootstrap.index('Get-FileHash'), bootstrap.index('ReadAllText'))
        self.assertIn('[scriptblock]::Create([IO.File]::ReadAllText($p))', bootstrap)
        self.assertNotIn('foreach($resource', bootstrap)
        self.assertNotIn('-SkillRoot', bootstrap)
        self.assertLess(len(command[-1]), 2000)
        return bootstrap, content

    def invoke_main(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = native.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_plan_and_default_execute_are_offline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'request.json'; output = root / 'plan.json'
            source.write_text(json.dumps(request({'op': 'create', 'index': 'append', 'name': 'Intro'})))
            with mock.patch.object(native, 'invoke_windows', side_effect=AssertionError('must not invoke Windows')) as actor:
                code, stdout, stderr = self.invoke_main(['plan', '--request', str(source), '--output', str(output)])
                self.assertEqual(code, 0, stderr)
                self.assertEqual(json.loads(stdout), json.loads(output.read_text()))
                code, stdout, stderr = self.invoke_main(['execute', '--request', str(source), '--vm', 'unused',
                                                        '--exe', 'C:\\Unused.exe', '--result', str(root / 'unused-result.json')])
                self.assertEqual(code, 0, stderr)
                self.assertTrue(json.loads(stdout)['dry_run'])
                self.assertFalse(json.loads(stdout)['network_used'])
                self.assertFalse((root / 'unused-result.json').exists())
                actor.assert_not_called()

    def test_execute_profile_flag_selects_quantum5_for_an_unqualified_dry_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'synthetic-request.json'
            result_path = root / 'unused-result.json'
            document = request({'op': 'create', 'index': 'append', 'name': 'Synthetic A'})
            source.write_text(json.dumps(document), encoding='utf-8')
            original = source.read_bytes()
            with mock.patch.object(native, 'invoke_windows', side_effect=AssertionError('dry-run must not invoke Windows')) as actor, \
                 mock.patch.object(native.subprocess, 'run', side_effect=AssertionError('dry-run must not invoke a VM')):
                code, stdout, stderr = self.invoke_main([
                    'execute', '--request', str(source), '--build-profile', 'quantum5-v2242',
                    '--vm', 'Synthetic VM', '--exe', r'C:\Synthetic\Quantum5.exe',
                    '--result', str(result_path)])
            self.assertEqual(code, 0, stderr)
            plan = json.loads(stdout)
            expected = native.select_profile(build_profile='quantum5-v2242')
            self.assertEqual(plan['build_profile'], expected['id'])
            self.assertEqual(plan['executable_sha256'], expected['executable']['sha256'])
            self.assertEqual(plan['steps'][0]['build_profile'], expected['id'])
            self.assertTrue(plan['dry_run'])
            self.assertFalse(plan['network_used'])
            self.assertFalse(plan['windows_runner_invoked'])
            self.assertFalse(plan['runtime_acceptance_verified'])
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(sorted(path.name for path in root.iterdir()), [source.name])
            actor.assert_not_called()

    def test_execute_profile_flag_rejects_a_different_explicit_request_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'synthetic-request.json'
            document = request({'op': 'create', 'index': 'append', 'name': 'Synthetic A'})
            document['build_profile'] = 'quantum1-v2126'
            source.write_text(json.dumps(document), encoding='utf-8')
            with mock.patch.object(native, 'invoke_windows', side_effect=AssertionError('profile mismatch must not invoke Windows')) as actor, \
                 mock.patch.object(native.subprocess, 'run', side_effect=AssertionError('profile mismatch must not invoke a VM')):
                for send in (False, True):
                    with self.subTest(send=send):
                        args = ['execute', '--request', str(source), '--build-profile', 'quantum5-v2242',
                                '--vm', 'Synthetic VM', '--exe', r'C:\Synthetic\Quantum5.exe',
                                '--result', str(root / 'unused-result.json')]
                        code, stdout, stderr = self.invoke_main(args + (['--send'] if send else []))
                        self.assertEqual(code, 2)
                        self.assertEqual(stdout, '')
                        self.assertIn('--build-profile differs from the request', json.loads(stderr)['error'])
            self.assertEqual(sorted(path.name for path in root.iterdir()), [source.name])
            actor.assert_not_called()

    def test_execute_profile_flag_cannot_override_a_different_inventory_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'synthetic-request.json'
            inventory = root / 'synthetic-inventory.json'
            source.write_text(json.dumps(request({'op': 'create', 'index': 'append', 'name': 'Synthetic A'})), encoding='utf-8')
            profile = native.select_profile(build_profile='quantum1-v2126')
            inventory.write_text(json.dumps({'schema_version': 1, 'build_profile': profile['id'],
                                             'executable_sha256': profile['executable']['sha256']}), encoding='utf-8')
            with mock.patch.object(native, 'invoke_windows', side_effect=AssertionError('inventory mismatch must not invoke Windows')) as actor, \
                 mock.patch.object(native.subprocess, 'run', side_effect=AssertionError('inventory mismatch must not invoke a VM')):
                for send in (False, True):
                    with self.subTest(send=send):
                        args = ['execute', '--request', str(source), '--inventory', str(inventory),
                                '--build-profile', 'quantum5-v2242', '--vm', 'Synthetic VM',
                                '--exe', r'C:\Synthetic\Quantum5.exe', '--result', str(root / 'unused-result.json')]
                        code, stdout, stderr = self.invoke_main(args + (['--send'] if send else []))
                        self.assertEqual(code, 2)
                        self.assertEqual(stdout, '')
                        self.assertIn('Mixed build identities', json.loads(stderr)['error'])
            self.assertEqual(sorted(path.name for path in root.iterdir()), sorted([source.name, inventory.name]))
            actor.assert_not_called()

    def test_invalid_final_step_does_not_publish_partial_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'bad.json'; output = Path(temporary) / 'plan.json'
            source.write_text(json.dumps(request({'op': 'create', 'index': 0, 'name': 'Valid'},
                                                 {'op': 'rename', 'index': 99, 'name': 'Invalid'})))
            output.write_text('original output')
            code, _, _ = self.invoke_main(['plan', '--request', str(source), '--output', str(output)])
            self.assertEqual(code, 2)
            self.assertEqual(output.read_text(), 'original output')

    def test_duplicate_json_keys_are_not_silently_overridden(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'request.json'
            source.write_text('{"schema_version":1,"fps":25,"encoding":"ascii",'
                              '"expected_count":0,"expected_count":1,"operations":[{"op":"save"}]}')
            code, _, stderr = self.invoke_main(['plan', '--request', str(source)])
            self.assertEqual(code, 2)
            self.assertIn('duplicate JSON field', stderr)

    def test_atomic_publication_failure_and_exclusive_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'output.json'; output.write_text('original')
            with self.assertRaises(ValueError):
                native.atomic_json(output, {'value': float('nan')})
            self.assertEqual(output.read_text(), 'original')
            with self.assertRaises(FileExistsError):
                native.atomic_json(output, {'new': 1}, exclusive=True)
            self.assertEqual(output.read_text(), 'original')
            fresh = Path(temporary) / 'fresh.json'
            native.atomic_json(fresh, {'中文': '保留'}, exclusive=True)
            self.assertEqual(json.loads(fresh.read_text(encoding='utf-8')), {'中文': '保留'})
            self.assertEqual(sorted(p.name for p in Path(temporary).iterdir()), ['fresh.json', 'output.json'])

    def test_mapping_rejects_external_and_relative_host_paths(self):
        with self.assertRaises(native.ContractError):
            native.mac_guest_path(Path('/tmp/native-result.json'))
        with self.assertRaises(native.ContractError):
            native.mac_guest_path(Path('relative.json'))
        expected = '\\\\Mac\\Home\\native-result.json'
        self.assertEqual(native.mac_guest_path(Path.home() / 'native-result.json'), expected)

    def test_symlink_does_not_escape_home_mapping(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home()), prefix='.native-contract-') as inside, tempfile.TemporaryDirectory() as outside:
            link = Path(inside) / 'escape'; link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(native.ContractError):
                native.mac_guest_path(link / 'result.json')

    def test_runtime_wrapper_hashes_mapping_and_quotes_parameters(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home()), prefix='native-contract-') as temporary:
            root = Path(temporary); script = fake_bundle(root); scripts = script.parent
            result = root / "operator's result.json"
            plan = native.compile_request(request({'op': 'save'}))

            def fake_run(command, **kwargs):
                self.assertEqual(command[:5], ['prlctl', 'exec', 'Test VM', '--current-user', 'powershell.exe'])
                stage = next(root.glob('digico-native-runtime-*'))
                _, wrapper = self.authenticated_mac_launcher(command, stage)
                self.assertIn('-SkillRoot', wrapper)
                self.assertIn("operator''s result.json", wrapper)
                receipt = stage / 'runtime-resources.json'
                self.assertIn(hashlib.sha256(receipt.read_bytes()).hexdigest(), wrapper)
                self.assertLess(len(command[-1]), 5000)
                self.assertIn('-ProcessId 1234', wrapper)
                compiled = result.with_name(result.name + '.request.json')
                digest = hashlib.sha256(compiled.read_bytes()).hexdigest()
                self.assertIn(digest, wrapper)
                result.write_text(json.dumps({'status': 'ok', 'request_sha256': digest}))
                return SimpleNamespace(returncode=0, stderr='', stdout='This is not acceptance evidence')

            with mock.patch.object(native, '__file__', str(scripts / 'native_session.py')), mock.patch.object(native.subprocess, 'run', side_effect=fake_run) as actor:
                actual = native.invoke_windows(plan, vm='Test VM', executable="C:\\User's Editor\\Quantum1.exe", result_path=result, process_id=1234)
                self.assertEqual(actual['status'], 'ok')
                actor.assert_called_once()
                with self.assertRaises(native.ContractError):
                    native.invoke_windows(plan, vm='Test VM', executable='C:\\Unused.exe', result_path=result)
                self.assertEqual(actor.call_count, 1)

    def test_stdout_and_stale_result_are_not_accepted(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home()), prefix='native-contract-') as temporary:
            root = Path(temporary); fake_bundle(root)
            result = root / 'result.json'

            def fake_run(*args, **kwargs):
                result.write_text(json.dumps({'status': 'ok', 'request_sha256': 'wrong'}))
                return SimpleNamespace(returncode=0, stdout='success', stderr='')

            with mock.patch.object(native, '__file__', str(root / 'scripts' / 'native_session.py')), mock.patch.object(native.subprocess, 'run', side_effect=fake_run):
                with self.assertRaisesRegex(native.ContractError, 'does not match'):
                    native.invoke_windows(native.compile_request(request({'op': 'save'})), vm='unused', executable='C:\\Unused.exe', result_path=result)

    def test_hidden_installed_bundle_is_staged_as_complete_visible_resources(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home()), prefix='native-contract-') as temporary:
            root = Path(temporary); installed = root / '.codex' / 'skills' / 'example'
            script = fake_bundle(installed)
            nested = installed / 'assets' / 'nested'; nested.mkdir()
            (nested / 'extra.bin').write_bytes(bytes(range(32)))
            expected = {str(p.relative_to(installed)): p.read_bytes()
                        for p in installed.rglob('*') if p.is_file()}
            output = root / 'Documents' / 'reviews' / 'result.json'

            def fake_run(command, **kwargs):
                stages = list(output.parent.glob('digico-native-runtime-*'))
                self.assertEqual(len(stages), 1)
                stage = stages[0]
                bootstrap, wrapper = self.authenticated_mac_launcher(command, stage)
                self.assertFalse(stage.name.startswith('.'))
                self.assertNotIn('\\.codex\\', wrapper)
                self.assertNotIn('\\.codex\\', bootstrap)
                self.assertIn(native.mac_guest_path(stage), wrapper)
                receipt_path = stage / 'runtime-resources.json'
                receipt = json.loads(receipt_path.read_text())
                self.assertEqual({item['path'] for item in receipt['resources']}, set(expected))
                self.assertNotIn('invoke.ps1', {item['path'] for item in receipt['resources']})
                for relative, original in expected.items():
                    self.assertEqual((stage / relative).read_bytes(), original)
                    resource = next(item for item in receipt['resources'] if item['path'] == relative)
                    self.assertEqual(resource['sha256'], hashlib.sha256(original).hexdigest())
                self.assertIn(hashlib.sha256(receipt_path.read_bytes()).hexdigest(), wrapper)
                self.assertIn('foreach($resource in @($receipt.resources))', wrapper)
                self.assertIn('Assert-Hash (Join-Path $root $resource.path) $resource.sha256', wrapper)
                self.assertLess(len(command[-1]), 5000)
                # The prepared stage remains self-contained even without its
                # synthetic hidden source installation. No real skill is removed.
                shutil.rmtree(installed)
                compiled = output.with_name(output.name + '.request.json')
                digest = hashlib.sha256(compiled.read_bytes()).hexdigest()
                output.write_text(json.dumps({'status': 'ok', 'request_sha256': digest}))
                return SimpleNamespace(returncode=0, stderr='', stdout='')

            with mock.patch.object(native, '__file__', str(script.with_name('native_session.py'))), mock.patch.object(native.subprocess, 'run', side_effect=fake_run):
                actual = native.invoke_windows(native.compile_request(request({'op': 'save'})), vm='unused', executable='C:\\Unused.exe', result_path=output)
            retained = Path(actual['runtime_stage_path'])
            self.assertTrue(retained.is_dir())
            self.assertEqual(retained.parent, output.parent)
            self.assertNotIn('runtime_stage_path', json.loads(output.read_text()))

    def test_mac_hidden_result_is_rejected_before_staging_or_vm(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home()), prefix='native-contract-') as temporary:
            root = Path(temporary); script = fake_bundle(root / 'bundle')
            with mock.patch.object(native, '__file__', str(script.with_name('native_session.py'))), mock.patch.object(native, '_stage_native_resources', side_effect=AssertionError('must not stage')), mock.patch.object(native.subprocess, 'run', side_effect=AssertionError('must not invoke VM')):
                for result in (root / '.hidden' / 'result.json', root / '.result.json'):
                    with self.subTest(result=result), self.assertRaisesRegex(native.ContractError, '~/Documents'):
                        native.invoke_windows(native.compile_request(request({'op': 'save'})), vm='unused', executable='C:\\Unused.exe', result_path=result)
            self.assertFalse((root / '.hidden').exists())

    def test_existing_request_is_rejected_before_stage_creation(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home()), prefix='native-contract-') as temporary:
            root = Path(temporary); script = fake_bundle(root / 'bundle')
            result = root / 'result.json'
            compiled = result.with_name(result.name + '.request.json'); compiled.write_text('keep existing request')
            with mock.patch.object(native, '__file__', str(script.with_name('native_session.py'))), mock.patch.object(native, '_stage_native_resources', side_effect=AssertionError('must not stage')), mock.patch.object(native.subprocess, 'run', side_effect=AssertionError('must not invoke VM')):
                with self.assertRaisesRegex(native.ContractError, 'new paths'):
                    native.invoke_windows(native.compile_request(request({'op': 'save'})), vm='unused', executable='C:\\Unused.exe', result_path=result)
            self.assertEqual(compiled.read_text(), 'keep existing request')
            self.assertEqual(list(root.glob('digico-native-runtime-*')), [])

    def test_timeout_keeps_stage_and_reports_its_path(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home()), prefix='native-contract-') as temporary:
            root = Path(temporary); script = fake_bundle(root / 'bundle'); result = root / 'result.json'
            timeout = native.subprocess.TimeoutExpired('synthetic command', 600)
            with mock.patch.object(native, '__file__', str(script.with_name('native_session.py'))), mock.patch.object(native.subprocess, 'run', side_effect=timeout):
                with self.assertRaises(native.ContractError) as caught:
                    native.invoke_windows(native.compile_request(request({'op': 'save'})), vm='unused', executable='C:\\Unused.exe', result_path=result)
            stages = list(root.glob('digico-native-runtime-*'))
            self.assertEqual(len(stages), 1)
            self.assertIn(str(stages[0]), str(caught.exception))
            self.assertTrue((stages[0] / 'runtime-resources.json').is_file())
            self.assertTrue((stages[0] / 'invoke.ps1').is_file())
            self.assertTrue(result.with_name(result.name + '.request.json').is_file())
            self.assertFalse(result.exists())

    def test_corrupted_resource_copy_fails_closed_and_retains_stage(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home()), prefix='native-contract-') as temporary:
            root = Path(temporary); bundle = root / 'bundle'; fake_bundle(bundle)
            copyfile = shutil.copyfile

            def corrupt_copy(source, destination):
                copyfile(source, destination)
                Path(destination).write_bytes(b'corrupted copy')

            with mock.patch.object(native.shutil, 'copyfile', side_effect=corrupt_copy):
                with self.assertRaisesRegex(native.ContractError, 'changed while staging') as caught:
                    native._stage_native_resources(bundle, root)
            stages = list(root.glob('digico-native-runtime-*'))
            self.assertEqual(len(stages), 1)
            self.assertIn(str(stages[0]), str(caught.exception))

    def test_native_windows_branch_does_not_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); script = fake_bundle(root / 'bundle'); output = root / 'result.json'

            def fake_run(command, **kwargs):
                self.assertEqual(command[:3], ['powershell.exe', '-NoProfile', '-EncodedCommand'])
                wrapper = base64.b64decode(command[-1]).decode('utf-16le')
                self.assertIn(str(script), wrapper)
                compiled = output.with_name(output.name + '.request.json')
                digest = hashlib.sha256(compiled.read_bytes()).hexdigest()
                output.write_text(json.dumps({'status': 'ok', 'request_sha256': digest}))
                return SimpleNamespace(returncode=0, stderr='', stdout='')

            with mock.patch.object(native, '__file__', str(script.with_name('native_session.py'))), mock.patch.object(native, '_native_windows', return_value=True), mock.patch.object(native, '_stage_native_resources', side_effect=AssertionError('must not stage native Windows')), mock.patch.object(native.subprocess, 'run', side_effect=fake_run):
                actual = native.invoke_windows(native.compile_request(request({'op': 'save'})), vm='local', executable='C:\\Unused.exe', result_path=output)
            self.assertNotIn('runtime_stage_path', actual)

    def test_encoded_wrapper_length_does_not_grow_with_asset_count(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home()), prefix='native-contract-') as temporary:
            root = Path(temporary); installed = root / '.codex' / 'skills' / 'example'
            script = fake_bundle(installed)
            sizes = []
            expected_counts = []

            def fake_run(command, **kwargs):
                encoded = command[-1]
                sizes.append(len(encoded))
                self.assertLess(len(encoded), 5000)
                output = root / ('result%d.json' % len(sizes))
                newest = max(output.parent.glob('digico-native-runtime-*'), key=lambda p: p.stat().st_mtime_ns)
                bootstrap, wrapper = self.authenticated_mac_launcher(command, newest)
                receipt_path = newest / 'runtime-resources.json'
                receipt = json.loads(receipt_path.read_text())
                self.assertEqual(len(receipt['resources']), expected_counts[-1])
                for item in receipt['resources']:
                    data = (newest / item['path']).read_bytes()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), item['sha256'])
                self.assertIn(hashlib.sha256(receipt_path.read_bytes()).hexdigest(), wrapper)
                self.assertEqual(wrapper.count(native.mac_guest_path(newest)), 1)
                self.assertEqual(bootstrap.count(native.mac_guest_path(newest)), 1)
                compiled = output.with_name(output.name + '.request.json')
                digest = hashlib.sha256(compiled.read_bytes()).hexdigest()
                output.write_text(json.dumps({'status': 'ok', 'request_sha256': digest}))
                return SimpleNamespace(returncode=0, stderr='', stdout='')

            with mock.patch.object(native, '__file__', str(script.with_name('native_session.py'))), mock.patch.object(native.subprocess, 'run', side_effect=fake_run):
                expected_counts.append(3)
                native.invoke_windows(native.compile_request(request({'op': 'save'})), vm='unused', executable='C:\\Unused.exe', result_path=root / 'result1.json')
                for index in range(200):
                    (installed / 'assets' / ('resource-%03d.bin' % index)).write_bytes(bytes([index % 256]))
                expected_counts.append(203)
                native.invoke_windows(native.compile_request(request({'op': 'save'})), vm='unused', executable='C:\\Unused.exe', result_path=root / 'result2.json')
            self.assertEqual(sizes[0], sizes[1])

    def test_no_arbitrary_host_flag_is_exposed(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            native.main(['inspect', '--vm', 'unused', '--exe', 'C:\\unused.exe', '--result', '/tmp/unused', '--host', '192.0.2.1'])
        self.assertEqual(caught.exception.code, 2)

    def test_inspection_uses_only_the_explicit_name_encoding(self):
        for selected in (None, 'ascii', 'cp950', 'utf-8'):
            with self.subTest(encoding=selected):
                args = ['inspect', '--vm', 'unused', '--exe', 'C:\\unused.exe', '--result', '/tmp/not-written.json']
                if selected is not None:
                    args.extend(['--encoding', selected])
                with mock.patch.object(native, 'invoke_windows', return_value={'status': 'ok'}) as actor:
                    code, _, stderr = self.invoke_main(args)
                self.assertEqual(code, 0, stderr)
                plan = actor.call_args.args[0]
                self.assertEqual(plan['mode'], 'inspect')
                self.assertEqual(plan['encoding'], selected or 'ascii')
                self.assertNotIn('expected_count', plan)


if __name__ == '__main__':
    unittest.main()

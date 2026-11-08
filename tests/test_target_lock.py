"""Local OS locking tests; no VM, network, UI or DiGiCo target calls."""
import importlib.util
import json
import os
from pathlib import Path
import queue
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/digico-native-session/scripts'
spec = importlib.util.spec_from_file_location('digico_target_lock', SCRIPTS / 'target_lock.py')
locks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(locks)
TARGET = {'vm': 'Synthetic Windows VM', 'executable': r'C:\Synthetic\Quantum1.exe'}

CHILD = r'''
import json,sys
sys.path.insert(0,sys.argv[1])
from target_lock import target_lock,TargetBusyError
try:
    with target_lock(lock_dir=sys.argv[2],vm=sys.argv[3],executable=sys.argv[4]) as lease:
        print(json.dumps({'acquired':True,'key':lease.key}),flush=True)
        if len(sys.argv)>5:sys.stdin.readline()
except TargetBusyError:
    print(json.dumps({'busy':True,'target_calls':0}),flush=True)
    sys.exit(23)
'''


class TargetLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='digico-lock-test-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'private-locks'

    def child_command(self, target=None, hold=False):
        target = target or TARGET
        return [sys.executable, '-u', '-c', CHILD, str(SCRIPTS), str(self.directory),
                target['vm'], target['executable']] + (['hold'] if hold else [])

    def start_holder(self):
        process = subprocess.Popen(self.child_command(hold=True), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def stop():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        self.addCleanup(stop)
        ready = queue.Queue()
        thread = threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True)
        thread.start()
        line = ready.get(timeout=5)
        self.assertTrue(line, process.stderr.read() if process.poll() is not None else 'holder did not start')
        self.assertTrue(json.loads(line)['acquired'])
        return process

    def probe(self, target=None):
        return subprocess.run(self.child_command(target), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=5)

    def test_normalized_target_hash_has_no_target_names(self):
        same = {'vm': '  SYNTHETIC WINDOWS VM  ', 'executable': 'c:/Synthetic/Folder/../QUANTUM1.EXE'}
        key = locks.target_key(**TARGET)
        self.assertEqual(key, locks.target_key(**same))
        self.assertEqual(len(key), 64)
        self.assertNotIn('Synthetic', key)
        if os.name != 'nt':
            self.assertNotEqual(key, locks.target_key(vm='Different VM', executable=TARGET['executable']))
        self.assertNotEqual(key, locks.target_key(vm=TARGET['vm'], executable=r'C:\Other\Quantum1.exe'))
        for bad in ('', '  ', None, True, 'bad\0value'):
            with self.assertRaises(locks.TargetLockError):
                locks.target_key(vm=bad, executable=TARGET['executable'])

    def test_native_windows_ignores_dummy_vm_labels_in_lock_identity(self):
        # Patch only this pure platform predicate; changing os.name on POSIX
        # could make pathlib try to instantiate unsupported WindowsPath objects.
        with patch.object(locks, '_native_windows', return_value=True):
            first = locks.target_key(vm='local', executable=TARGET['executable'])
            self.assertEqual(first, locks.target_key(vm='Unused Other Label',
                executable='c:/Synthetic/QUANTUM1.EXE'))
            self.assertNotEqual(first, locks.target_key(vm='local',
                executable=r'C:\Other\Quantum1.exe'))
        with patch.object(locks, '_native_windows', return_value=False):
            self.assertNotEqual(locks.target_key(**TARGET), locks.target_key(
                vm='Different VM', executable=TARGET['executable']))

    def test_second_process_fails_fast_without_entering_target_body_then_releases(self):
        holder = self.start_holder()
        blocked = self.probe()
        self.assertEqual(blocked.returncode, 23, blocked.stderr)
        self.assertEqual(json.loads(blocked.stdout), {'busy': True, 'target_calls': 0})
        self.assertIsNone(holder.poll())
        holder.stdin.write('\n');holder.stdin.flush()
        holder.communicate(timeout=5)
        self.assertEqual(holder.returncode, 0)
        after = self.probe()
        self.assertEqual(after.returncode, 0, after.stderr)
        self.assertTrue(json.loads(after.stdout)['acquired'])

    def test_os_releases_after_abrupt_holder_exit(self):
        holder = self.start_holder()
        holder.kill();holder.communicate(timeout=5)
        after = self.probe()
        self.assertEqual(after.returncode, 0, after.stderr)

    def test_distinct_target_process_can_enter_while_first_is_held(self):
        self.start_holder()
        target = {**TARGET, 'executable': r'C:\Other\Quantum1.exe'}
        result = self.probe(target)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exception_releases_lock_without_unlinking_stable_file(self):
        with self.assertRaisesRegex(RuntimeError, 'synthetic exception'):
            with locks.target_lock(**TARGET, lock_dir=self.directory) as lease:
                path = lease.path
                raise RuntimeError('synthetic exception')
        self.assertTrue(path.is_file())
        # Windows byte-range locks also deny reads of the locked marker.
        # Inspect its retained content only after exception cleanup releases it.
        self.assertEqual(path.read_bytes(), b'\0')
        with locks.target_lock(**TARGET, lock_dir=self.directory) as second:
            self.assertEqual(second.path, path)
        self.assertEqual(list(self.directory.iterdir()), [path])
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_same_process_and_thread_cannot_enter_held_target(self):
        errors = []
        with locks.target_lock(**TARGET, lock_dir=self.directory):
            with self.assertRaises(locks.TargetBusyError):
                with locks.target_lock(**TARGET, lock_dir=self.directory):
                    self.fail('reentrant target body entered')
            def compete():
                try:
                    with locks.target_lock(**TARGET, lock_dir=self.directory):
                        errors.append('unexpected acquisition')
                except locks.TargetBusyError:
                    errors.append('busy')
            thread = threading.Thread(target=compete);thread.start();thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ['busy'])

    def test_decorator_holds_before_body_and_releases_on_exception(self):
        calls = []
        @locks.locked_target
        def operation(*, vm, executable):
            calls.append('target body')
            raise RuntimeError('target exception')
        with patch.object(locks, 'default_lock_dir', return_value=self.directory):
            with locks.target_lock(**TARGET):
                with self.assertRaises(locks.TargetBusyError):operation(**TARGET)
                self.assertEqual(calls, [])
            with self.assertRaisesRegex(RuntimeError, 'target exception'):operation(**TARGET)
            with locks.target_lock(**TARGET):
                pass
        self.assertEqual(calls, ['target body'])

    @unittest.skipIf(os.name == 'nt', 'POSIX owner/mode and symlink checks')
    def test_nonprivate_directory_and_symlinks_are_rejected(self):
        self.directory.mkdir(mode=0o755)
        self.directory.chmod(0o755)
        with self.assertRaises(locks.TargetLockError):
            with locks.target_lock(**TARGET, lock_dir=self.directory):pass
        self.directory.chmod(0o700)
        link = Path(self.temp.name) / 'linked-directory'
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(locks.TargetLockError):
            with locks.target_lock(**TARGET, lock_dir=link):pass
        other = Path(self.temp.name) / 'other-file';other.write_bytes(b'private')
        filename = self.directory / (locks.target_key(**TARGET) + '.lock')
        filename.symlink_to(other)
        with self.assertRaises(locks.TargetLockError):
            with locks.target_lock(**TARGET, lock_dir=self.directory):pass
        self.assertEqual(other.read_bytes(), b'private')


if __name__ == '__main__':
    unittest.main()

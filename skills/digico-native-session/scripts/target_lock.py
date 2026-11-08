"""Fail-fast, same-user host locks for one native CLI target.

Hold ``with target_lock(vm=vm, executable=executable):`` around the entire
Windows invocation, before staging resources or starting a target subprocess.
The lock coordinates local processes using this module/default directory. It
does not coordinate different hosts, OS users or independently chosen lock dirs.
No target calls, retries, credentials, PID files or target names are written.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
from functools import wraps
import hashlib
import json
import ntpath
import os
from pathlib import Path
import stat
import tempfile
import threading
from typing import NamedTuple
import unicodedata


class TargetLockError(ValueError):
    """The local lock cannot be established safely."""


class TargetBusyError(TargetLockError):
    """Another local invocation already holds this target."""


class TargetLease(NamedTuple):
    key: str
    path: Path


_registry_guard = threading.Lock()
_held_paths = set()


def _reset_after_fork():
    global _registry_guard, _held_paths
    _registry_guard = threading.Lock()
    _held_paths = set()


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_reset_after_fork)


def _native_windows():
    return os.name == 'nt'


def target_key(*, vm, executable):
    """Return a private filename key for normalized VM + Windows EXE identity.

    On native Windows the unused VM label is fixed to ``windows-local``. On a
    Mac host Unicode NFC/casefold and outer VM whitespace are normalized. EXE
    slash, case, dot and parent segments always use Windows path rules.
    Process ID is intentionally excluded: callers may select the same editor
    with or without a PID. Path aliases/VM UUID-vs-name aliases are not resolved.
    """
    if (not isinstance(vm, str) or not vm.strip() or '\0' in vm
            or not isinstance(executable, str) or not executable.strip()
            or '\0' in executable):
        raise TargetLockError('lock target requires nonempty VM and executable strings without NUL')
    normalized_vm = ('windows-local' if _native_windows() else
                     unicodedata.normalize('NFC', vm.strip()).casefold())
    normalized_exe = unicodedata.normalize('NFC',
        ntpath.normcase(ntpath.normpath(executable.strip()))).casefold()
    identity = json.dumps([normalized_vm, normalized_exe], ensure_ascii=True,
                          separators=(',', ':')).encode('ascii')
    return hashlib.sha256(identity).hexdigest()


def default_lock_dir():
    """Use the current user's temporary area; only hashes enter filenames."""
    base = Path(tempfile.gettempdir()).resolve()
    if os.name == 'nt':
        # A shared C:\\Temp does not provide a user-private ACL boundary. The
        # ordinary Windows user-temp directory inherits the user profile ACL.
        home = Path.home().resolve()
        try:
            base.relative_to(home)
        except ValueError as error:
            raise TargetLockError('Windows TEMP must be inside the current user profile for private target locks') from error
        user_key = hashlib.sha256(str(home).casefold().encode('utf-8')).hexdigest()[:16]
    else:
        user_key = str(os.getuid())
    return base / ('digico-native-locks-' + user_key)


def _private_directory(path):
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, 'st_file_attributes', 0)
               & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
        raise TargetLockError('target lock directory must be a real directory, not a link')
    if os.name != 'nt' and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise TargetLockError('target lock directory must be owned by this user with mode 0700')


def _open_lock(path):
    try:
        link_info = path.lstat()
    except FileNotFoundError:
        link_info = None
    if link_info is not None and (not stat.S_ISREG(link_info.st_mode)
            or getattr(link_info, 'st_file_attributes', 0)
               & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
        raise TargetLockError('target lock file must be a regular file, not a link')
    flags = (os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
             | getattr(os, 'O_BINARY', 0))
    fd = os.open(str(path), flags, 0o600)
    try:
        os.set_inheritable(fd, False)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise TargetLockError('target lock file must be a regular file')
        if os.name != 'nt' and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise TargetLockError('target lock file must be owned by this user with mode 0600')
        # Windows byte-region locks require a byte. Never truncate or unlink:
        # replacing the inode could allow simultaneous locks on different files.
        if info.st_size == 0:
            os.write(fd, b'\0')
        os.lseek(fd, 0, os.SEEK_SET)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _acquire(fd):
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(fd):
    if os.name == 'nt':
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def target_lock(*, vm, executable, lock_dir=None):
    """Acquire immediately or raise TargetBusyError; release on every exit.

    ``lock_dir`` is a test/embedding override, not a CLI target option. All
    cooperating callers must use the same directory. Keep the persistent one-byte
    marker files; deleting a locked marker defeats file-lock coordination.
    """
    key = target_key(vm=vm, executable=executable)
    directory = Path(lock_dir) if lock_dir is not None else default_lock_dir()
    if not directory.is_absolute():
        raise TargetLockError('target lock directory must be absolute')
    _private_directory(directory)
    path = directory / (key + '.lock')
    local_key = os.path.normcase(str(path.absolute()))
    with _registry_guard:
        if local_key in _held_paths:
            raise TargetBusyError('Another local invocation is using this DiGiCo target; no target call was started. Wait for it to finish and inspect any uncertain result before retrying.')
        _held_paths.add(local_key)
    fd = None
    acquired = False
    try:
        fd = _open_lock(path)
        try:
            _acquire(fd)
            acquired = True
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK,
                               getattr(errno, 'EDEADLK', -1)):
                raise TargetBusyError('Another local invocation is using this DiGiCo target; no target call was started. Wait for it to finish and inspect any uncertain result before retrying.') from None
            raise TargetLockError('Could not acquire the local DiGiCo target lock') from error
        yield TargetLease(key, path)
    finally:
        try:
            if acquired:
                _release(fd)
        finally:
            if fd is not None:
                os.close(fd)
            with _registry_guard:
                _held_paths.discard(local_key)


def locked_target(function):
    """Decorate a function with required keyword-only vm/executable arguments.

    Intended minimal integration: decorate ``invoke_windows``. The lock is held
    before any function-body validation/staging and until return or exception.
    Pure plan/catalog paths that do not invoke Windows need no target lock.
    """
    @wraps(function)
    def wrapped(*args, **kwargs):
        with target_lock(vm=kwargs.get('vm'), executable=kwargs.get('executable')):
            return function(*args, **kwargs)
    return wrapped

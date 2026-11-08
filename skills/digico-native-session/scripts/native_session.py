#!/usr/bin/env python3
"""Plan verified DiGiCo native operations; explicit execution uses the bundled Windows runner.

Planning and execute without --send never contact a VM, editor, or network.
The runner supports the exact Quantum1 V2126 profile; no UI automation, editor
installation, VM boot, firewall changes, or arbitrary remote host is exposed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import sys
import tempfile

from mirror_codec import build_packet

SCHEMA_VERSION = 1
ENCODINGS = ('ascii', 'cp950', 'utf-8')
INT32_MAX = 2 ** 31 - 1
# Local execution-resource bounds shared with the Windows runner, not desk limits.
MAX_SNAPSHOT_COUNT = 2000
MAX_OPERATIONS = 1000
PARALLELS_WRAPPER_LIMIT = 5000  # Local bound for the encoded prlctl argument.
COMMANDS = {
    'create': (1, 0x708, 0xC9), 'delete': (1, 0x708, 0xCE),
    'rename': (3, 0x708, 0xCD), 'timecode': (1, 0x715, 0xB5),
    'save_as': (3, 0x1A5, 0), 'save': (1, 0x1A6, 0),
    'load': (3, 0x19B, 0),
}


class ContractError(ValueError):
    pass


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError('duplicate JSON field: %s' % key)
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=_json_object)


def _fields(obj, required, optional=(), label='request'):
    if not isinstance(obj, dict):
        raise ContractError('%s must be a JSON object' % label)
    missing = set(required) - obj.keys()
    unknown = obj.keys() - set(required) - set(optional)
    if missing:
        raise ContractError('%s is missing fields: %s' % (label, ', '.join(sorted(missing))))
    if unknown:
        raise ContractError('%s has unknown fields: %s' % (label, ', '.join(sorted(unknown))))


def _integer(value, label, low=0, high=INT32_MAX):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ContractError('%s must be an integer in [%d, %d]' % (label, low, high))
    return value


def timecode_quarterframes(text, fps=25):
    if isinstance(fps, bool) or not isinstance(fps, int) or fps != 25:
        raise ContractError('only the runtime-validated 25 fps is supported')
    if not isinstance(text, str) or not re.fullmatch(r'[0-9]{2}:[0-9]{2}:[0-9]{2}:[0-9]{2}', text):
        raise ContractError('timecode must be HH:MM:SS:FF')
    hours, minutes, seconds, frames = map(int, text.split(':'))
    if hours > 23 or minutes > 59 or seconds > 59 or frames >= fps:
        raise ContractError('timecode component is outside the 25-fps range')
    return ((hours * 3600 + minutes * 60 + seconds) * fps + frames) * 4


def encode_name(name, encoding):
    if not isinstance(name, str):
        raise ContractError('name must be a string')
    if '\0' in name:
        raise ContractError('name must not contain NUL')
    try:
        encoded = name.encode(encoding, errors='strict')
    except UnicodeError as error:
        raise ContractError('name is not encodable as %s without replacement' % encoding) from error
    if not 1 <= len(encoded) <= 31:
        raise ContractError('name must contain 1..31 encoded bytes; no truncation is performed')
    return encoded


def session_path(value):
    if not isinstance(value, str) or not value:
        raise ContractError('session path must be a nonempty string')
    try:
        value.encode('ascii', errors='strict')
    except UnicodeError as error:
        raise ContractError('session paths must be ASCII') from error
    if any(ord(char) < 32 for char in value) or any(char in value for char in '<>"|?*'):
        raise ContractError('session path contains an invalid Windows character')
    normalized = value.replace('/', '\\')
    if not re.match(r'^[A-Za-z]:\\', normalized) or ':' in normalized[2:]:
        raise ContractError('session path must be an absolute Windows drive path, not UNC or relative')
    segments = normalized[3:].split('\\')
    if any(not part or part in ('.', '..') or part.endswith((' ', '.')) for part in segments):
        raise ContractError('session path must not contain empty, dot, parent, or trailing-dot/space components')
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {'COM%d' % n for n in range(1, 10)} | {'LPT%d' % n for n in range(1, 10)}
    if any(part.split('.')[0].upper() in reserved for part in segments):
        raise ContractError('session path contains a reserved Windows device name')
    if PureWindowsPath(normalized).suffix.lower() != '.ses':
        raise ContractError('session path must end in .ses')
    if len(normalized.encode('ascii')) > 255:
        raise ContractError('session path exceeds the native 255-byte limit')
    return normalized


def _frame(op, index=0, body=b'\0\0\0\0'):
    import struct
    category, group, control = COMMANDS[op]
    return build_packet(category=category, control_id=struct.pack('<HBH', control, 0, group),
                        list_index=index, payload=body).hex()


def _string_body(raw):
    import struct
    return struct.pack('<I', len(raw)) + raw


def compile_request(request):
    """Validate the entire request and simulate indices before returning any plan."""
    import struct
    _fields(request, ('schema_version', 'fps', 'encoding', 'expected_count', 'operations'))
    if type(request['schema_version']) is not int or request['schema_version'] != SCHEMA_VERSION:
        raise ContractError('schema_version must be integer 1')
    fps = request['fps']
    timecode_quarterframes('00:00:00:00', fps)
    encoding = request['encoding']
    if encoding not in ENCODINGS:
        raise ContractError('encoding must be ascii, cp950, or utf-8')
    count = _integer(request['expected_count'], 'expected_count', high=MAX_SNAPSHOT_COUNT)
    operations = request['operations']
    if not isinstance(operations, list) or not operations:
        raise ContractError('operations must be a nonempty array')
    if len(operations) > MAX_OPERATIONS:
        raise ContractError('operations exceeds the local %d-operation resource bound' % MAX_OPERATIONS)
    steps = []
    for position, operation in enumerate(operations):
        if not isinstance(operation, dict) or not isinstance(operation.get('op'), str):
            raise ContractError('each operation must be an object with a string op')
        op = operation['op']
        if op in ('load', 'new_session') and len(operations) != 1:
            raise ContractError('load/new_session must be the only operation; loaded counts are opaque')
        if op in ('save', 'save_as') and position != len(operations) - 1:
            raise ContractError('save/save_as must be the final operation')
        step = {'op': op}
        if op == 'create':
            _fields(operation, ('op', 'index', 'name'), ('timecode',), 'create')
            if count >= MAX_SNAPSHOT_COUNT:
                raise ContractError('snapshot count would exceed the local %d-snapshot resource bound' % MAX_SNAPSHOT_COUNT)
            index = count if operation['index'] == 'append' else _integer(operation['index'], 'create.index')
            if index > count:
                raise ContractError('create index must be within 0..current_count')
            encoded = encode_name(operation['name'], encoding)
            step.update(index=index, name=operation['name'], name_hex=encoded.hex(), frames=[
                _frame('create', index, struct.pack('<i', -1)),
                _frame('rename', index, _string_body(encoded)),
            ])
            if 'timecode' in operation:
                quarters = timecode_quarterframes(operation['timecode'], fps)
                step.update(timecode=operation['timecode'], time_quarterframes=quarters)
                step['frames'].append(_frame('timecode', index, struct.pack('<i', quarters)))
            count += 1
        elif op in ('delete', 'rename', 'timecode'):
            required = ('op', 'index') + (('name',) if op == 'rename' else ('timecode',) if op == 'timecode' else ())
            _fields(operation, required, label=op)
            index = _integer(operation['index'], op + '.index')
            if index >= count:
                raise ContractError('%s index must refer to a current snapshot' % op)
            step['index'] = index
            if op == 'delete':
                step['frames'] = [_frame(op, index)]
                count -= 1
            elif op == 'rename':
                encoded = encode_name(operation['name'], encoding)
                step.update(name=operation['name'], name_hex=encoded.hex(), frames=[_frame(op, index, _string_body(encoded))])
            else:
                quarters = timecode_quarterframes(operation['timecode'], fps)
                step.update(timecode=operation['timecode'], time_quarterframes=quarters,
                            frames=[_frame(op, index, struct.pack('<i', quarters))])
        elif op in ('save_as', 'load', 'new_session'):
            _fields(operation, ('op', 'path'), ('overwrite',) if op == 'save_as' else (), op)
            path = session_path(operation['path'])
            step['path'] = path
            if op in ('save_as', 'new_session'):
                overwrite = operation.get('overwrite', False)
                if type(overwrite) is not bool:
                    raise ContractError('overwrite must be a boolean')
                step['overwrite'] = overwrite
            step['frames'] = [] if op == 'new_session' else [_frame(op, body=_string_body(path.encode('ascii')))]
            if op in ('load', 'new_session'):
                count = None
        elif op == 'save':
            _fields(operation, ('op',), label='save')
            step['frames'] = [_frame('save')]
        else:
            raise ContractError('unsupported operation: %s' % op)
        step['expected_count_after'] = count
        steps.append(step)
    return {
        'schema_version': SCHEMA_VERSION, 'mode': 'execute', 'fps': fps,
        'encoding': encoding, 'expected_count': request['expected_count'], 'steps': steps,
        'final_expected_count': count, 'runtime_acceptance_verified': False,
    }


def atomic_json(path, document, *, exclusive=False):
    """Publish a complete UTF-8 JSON file in one replace, after full validation."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=str(path.parent), prefix='.' + path.name + '.',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            # Same-directory hard-link publication is atomic and fails if another
            # invocation has already claimed this request filename.
            os.link(str(temporary), str(path))
            temporary.unlink()
        else:
            os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def mac_guest_path(path):
    """Map only a resolved absolute path inside this account's known home share."""
    path = Path(path)
    if not path.is_absolute():
        raise ContractError('runtime paths must be absolute host paths')
    try:
        relative = path.resolve().relative_to(Path.home().resolve())
    except ValueError as error:
        raise ContractError('runtime paths must remain inside Path.home() for the \\Mac\\Home mapping') from error
    return '\\\\Mac\\Home\\' + str(relative).replace('/', '\\')


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _native_windows():
    return os.name == 'nt'


def _stage_native_resources(skill_root, parent):
    """Copy only this bundle's Windows resources into a visible, self-contained root.

    Every copied file is hashed before/after copying and gated again by the guest
    wrapper. Stages are retained, including incomplete stages, for diagnosis.
    """
    skill_root, parent = Path(skill_root), Path(parent)
    assets = skill_root / 'assets'
    script = skill_root / 'scripts' / 'windows_native.ps1'
    if script.is_symlink() or not script.is_file() or assets.is_symlink() or not assets.is_dir():
        raise ContractError('bundled Windows runner/assets are missing or are symlinks')
    sources = [script]
    for source in sorted(assets.rglob('*')):
        if source.is_symlink():
            raise ContractError('bundled assets must be self-contained regular files, not symlinks')
        if source.is_file():
            sources.append(source)
    if len(sources) == 1:
        raise ContractError('bundled assets directory contains no files')
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='digico-native-runtime-', dir=str(parent)))
    resources = []
    try:
        for source in sources:
            relative = source.relative_to(skill_root)
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            shutil.copyfile(str(source), str(destination))
            if hashlib.sha256(destination.read_bytes()).hexdigest() != expected or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise ContractError('bundled runtime resource changed while staging: %s' % relative)
            resources.append({'path': relative.as_posix(), 'sha256': expected,
                              'bytes': destination.stat().st_size})
        receipt = stage / 'runtime-resources.json'
        atomic_json(receipt, {'schema_version': 1, 'resources': resources}, exclusive=True)
        gates = [(stage / item['path'], item['sha256']) for item in resources]
        gates.append((receipt, hashlib.sha256(receipt.read_bytes()).hexdigest()))
        return stage, gates
    except (ContractError, OSError) as error:
        raise ContractError('%s; runtime staging retained at %s' % (error, stage)) from error


def invoke_windows(plan, *, vm, executable, result_path, process_id=None):
    """Execute the bundled runner only after CLI authorization and plan validation."""
    result_path = Path(result_path)
    if not result_path.is_absolute():
        raise ContractError('--result must be an absolute host path')
    if process_id is not None:
        _integer(process_id, 'process_id', 1)
    script_path = Path(__file__).resolve().with_name('windows_native.ps1')
    if not script_path.is_file():
        raise ContractError('bundled windows_native.ps1 is missing')
    request_path = result_path.with_name(result_path.name + '.request.json')
    if result_path.exists() or request_path.exists():
        raise ContractError('--result and its .request.json must both be new paths')
    skill_root = script_path.parent.parent
    stage = None
    try:
        native_windows = _native_windows()
        if native_windows:
            guest_script, guest_request, guest_result = map(str, (script_path, request_path, result_path))
            guest_root = str(skill_root)
            gates = [(guest_script, hashlib.sha256(script_path.read_bytes()).hexdigest())]
            command_prefix = ['powershell.exe', '-NoProfile', '-EncodedCommand']
        else:
            # Installed .codex resources can be invisible to Parallels Home
            # sharing. Only the result location must be directly visible.
            guest_request, guest_result = map(mac_guest_path, (request_path, result_path))
            relative_result = result_path.resolve().relative_to(Path.home().resolve())
            if any(part.startswith('.') for part in relative_result.parts):
                raise ContractError('Mac --result must use visible Home-share components; choose a path under ~/Documents, not a dot-prefixed directory/file')
            stage, host_gates = _stage_native_resources(skill_root, result_path.parent)
            guest_script = mac_guest_path(stage / 'scripts' / 'windows_native.ps1')
            guest_root = mac_guest_path(stage)
            # The receipt is host-generated and includes every staged resource.
            # Gate it once, then verify its resource list inside PowerShell.
            receipt_hash = host_gates[-1][1]
            command_prefix = ['prlctl', 'exec', vm, '--current-user', 'powershell.exe', '-NoProfile', '-EncodedCommand']
        atomic_json(request_path, plan, exclusive=True)
        request_hash = hashlib.sha256(request_path.read_bytes()).hexdigest()
        wrapper = "$ErrorActionPreference='Stop'\n"
        if native_windows:
            gates.append((guest_request, request_hash))
            for path, digest in gates:
                wrapper += "if(-not(Test-Path -LiteralPath %s)){throw 'Required mapped file is missing'}\n" % _ps_quote(path)
                wrapper += "if((Get-FileHash -LiteralPath %s -Algorithm SHA256).Hash.ToLowerInvariant() -ne '%s'){throw 'Mapped file hash mismatch'}\n" % (_ps_quote(path), digest)
            wrapper += '& ([scriptblock]::Create([IO.File]::ReadAllText(%s))) -SkillRoot %s -RequestPath %s -ResultPath %s -ExecutablePath %s' % tuple(map(_ps_quote, (guest_script, guest_root, guest_request, guest_result, executable)))
        else:
            wrapper += '$root=%s\n$requestFile=%s\n$resultFile=%s\n' % tuple(map(_ps_quote, (guest_root, guest_request, guest_result)))
            wrapper += "function Assert-Hash($p,$h){if(-not(Test-Path -LiteralPath $p)-or (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant()-ne $h){throw 'Runtime hash mismatch'}}\n"
            wrapper += "$receiptFile=Join-Path $root 'runtime-resources.json'\n"
            wrapper += "Assert-Hash $receiptFile '%s'\n" % receipt_hash
            wrapper += "$receipt=Get-Content -LiteralPath $receiptFile -Raw -Encoding UTF8|ConvertFrom-Json\n"
            wrapper += 'foreach($resource in @($receipt.resources)){Assert-Hash (Join-Path $root $resource.path) $resource.sha256}\n'
            wrapper += "Assert-Hash $requestFile '%s'\n" % request_hash
            wrapper += "$runnerFile=Join-Path $root 'scripts\\windows_native.ps1'\n"
            wrapper += '& ([scriptblock]::Create([IO.File]::ReadAllText($runnerFile))) -SkillRoot $root -RequestPath $requestFile -ResultPath $resultFile -ExecutablePath %s' % _ps_quote(executable)
        if process_id is not None:
            wrapper += ' -ProcessId %d' % process_id
        wrapper += '\n'
        encoded = base64.b64encode(wrapper.encode('utf-16le')).decode('ascii')
        if not native_windows and len(encoded) >= PARALLELS_WRAPPER_LIMIT:
            raise ContractError('encoded Parallels wrapper exceeds the local 5000-character bound; choose a shorter visible result directory')
        completed = subprocess.run(command_prefix + [encoded], capture_output=True, text=True, timeout=600, check=False)
        if not result_path.is_file():
            raise ContractError('Windows runner returned without a result file (exit %d); acceptance is unknown: %s' % (completed.returncode, completed.stderr.strip() or completed.stdout.strip()))
        result = read_json(result_path)
        if not isinstance(result, dict) or result.get('status') not in ('ok', 'error'):
            raise ContractError('runner result has no recognized status; acceptance is unknown')
        if result.get('request_sha256') != request_hash:
            raise ContractError('runner result does not match this compiled request; acceptance is unknown')
        if completed.returncode and result.get('status') != 'error':
            raise ContractError('Windows runner exited unsuccessfully despite a non-error result; acceptance is unknown')
        if stage is not None:
            # Host-only provenance added to CLI output; the runner's original
            # result file is retained unchanged. Never clean an uncertain stage.
            result['runtime_stage_path'] = str(stage)
            if result['status'] == 'error':
                result['error'] = '%s; runtime staging retained at %s' % (result.get('error') or 'Windows runner error', stage)
        return result
    except (ContractError, ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as error:
        if stage is not None:
            raise ContractError('%s; runtime staging retained at %s' % (error, stage)) from error
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    plan_parser = commands.add_parser('plan', help='validate a request and construct packets without contacting Windows')
    plan_parser.add_argument('--request', type=Path, required=True)
    plan_parser.add_argument('--output', type=Path)
    execute_parser = commands.add_parser('execute', help='dry-run by default; --send explicitly invokes the local Windows runner')
    execute_parser.add_argument('--request', type=Path, required=True)
    execute_parser.add_argument('--send', action='store_true')
    inspect_parser = commands.add_parser('inspect', help='invoke the runner in read-only inspection mode')
    inspect_parser.add_argument('--encoding', choices=ENCODINGS, default='ascii',
                                help='explicit name-decoding encoding; raw bytes are retained (default: ascii)')
    for sub in (execute_parser, inspect_parser):
        sub.add_argument('--vm', required=True)
        sub.add_argument('--exe', required=True)
        sub.add_argument('--process-id', type=int)
        sub.add_argument('--result', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect':
            plan = {'schema_version': SCHEMA_VERSION, 'mode': 'inspect', 'fps': 25, 'encoding': args.encoding}
        else:
            request = read_json(args.request)
            plan = compile_request(request)
        if args.command == 'plan':
            if args.output:
                atomic_json(args.output, plan)
            result = plan
        elif args.command == 'execute' and not args.send:
            result = {**plan, 'dry_run': True, 'network_used': False, 'windows_runner_invoked': False}
        else:
            result = invoke_windows(plan, vm=args.vm, executable=args.exe,
                                    result_path=args.result, process_id=args.process_id)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        if result.get('status') == 'error':
            return 2
    except (ContractError, ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': str(error), 'runtime_acceptance_verified': False}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read/write reviewed .ses files or plan verified DiGiCo native operations.

File commands and planning never contact a VM, editor, or network. Explicit
editor execution uses the bundled Windows runner and exact build profiles;
no UI automation, installation, VM boot, or arbitrary remote host is exposed.
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
import uuid

from mirror_codec import build_packet
from control_catalog import normalize_inventory, plan_control_write, assert_control_write_allowed
from routing_catalog import build_routing_catalog, plan_input_route, plan_output_route, plan_group_assignment
from preset_adapter import is_preset_request, compile_preset_request
from session_structure import decode_structure_readback
from target_lock import locked_target, target_lock
from operation_journal import read_journal, recovery_report
from build_profiles import select_profile, profile_for_document, profile_identity, use_profile, stamp_profile
from authoring import compile_authoring_request, authoring_catalog
from authoring_workflow import run_authoring

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


def compile_request(request, inventory=None, presets=None):
    requested = request.get('build_profile') if isinstance(request, dict) else None
    profile = select_profile(build_profile=requested) if requested is not None else None
    for artifact in (inventory, presets):
        if artifact is not None:
            profile = profile_for_document(artifact, expected=profile)
    profile = profile or select_profile()
    feature_by_operation = {'set_control':'control_write', 'set_channel_name':'control_write',
        'set_input_route':'routes', 'set_output_route':'routes', 'set_group_assignment':'routes',
        'save':'session_files', 'save_as':'session_files', 'load':'session_files',
        'new_session':'template', 'export_session':'transfer',
        'create':'snapshots', 'delete':'snapshots', 'rename':'snapshots', 'timecode':'snapshots'}
    candidate_operations = request.get('operations') if isinstance(request, dict) else None
    for operation in candidate_operations if isinstance(candidate_operations, list) else ():
        if isinstance(operation, dict):
            name = operation.get('op', '')
            if not isinstance(name, str):
                continue
            feature = 'presets' if name.startswith('preset_') else feature_by_operation.get(name)
            if feature and profile['features'].get(feature) is not True:
                raise ContractError('Selected build has no verified support for ' + feature)
    with use_profile(profile['executable']['sha256'], build_profile=profile['id']):
        return stamp_profile(_compile_request(request, inventory, presets), profile)


def _compile_request(request, inventory=None, presets=None):
    """Validate the entire request and simulate indices before returning any plan."""
    import struct
    if is_preset_request(request):
        if presets is None:
            raise ContractError('Preset writes require --presets from a fresh native presets result')
        preset_request = {key:value for key,value in request.items() if key != 'build_profile'}
        return compile_preset_request(preset_request, presets=presets, inventory=inventory)
    _fields(request, ('schema_version', 'fps', 'encoding', 'expected_count', 'operations'), ('build_profile',))
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
    catalog = None
    control_inventory = inventory.get('inventory', inventory) if isinstance(inventory, dict) else None
    if any(isinstance(op, dict) and op.get('op') in ('set_control', 'set_channel_name', 'set_input_route', 'set_output_route', 'set_group_assignment') for op in operations):
        if control_inventory is None:
            raise ContractError('non-Cue writes require --inventory from a fresh native inventory result')
        catalog = normalize_inventory(control_inventory)
    simulated_controls = {}
    simulated_names = {}
    for position, operation in enumerate(operations):
        if not isinstance(operation, dict) or not isinstance(operation.get('op'), str):
            raise ContractError('each operation must be an object with a string op')
        op = operation['op']
        if op in ('load', 'new_session', 'export_session') and len(operations) != 1:
            raise ContractError('load/new_session/export_session must be the only operation')
        if op in ('save', 'save_as') and position != len(operations) - 1:
            raise ContractError('save/save_as must be the final operation')
        step = {'op': op}
        if op == 'export_session':
            _fields(operation, ('op', 'source', 'destination'), label=op)
            source = session_path(operation['source'])
            destination = session_path(operation['destination'])
            if len(source.encode('ascii')) > 127:
                raise ContractError('native transfer source path exceeds its 127-byte ShortString')
            if source.casefold() == destination.casefold():
                raise ContractError('export source and destination must differ')
            step.update(source=source, destination=destination, frames=[])
        elif op == 'set_input_route':
            _fields(operation, ('op', 'channel_id', 'source'), ('route_kind', 'insert_index'), op)
            route = plan_input_route(inventory, operation['channel_id'], operation['source'], route_kind=operation.get('route_kind', 'main'), insert_index=operation.get('insert_index', 0))
            step.update(route)
            step['frames'] = [build_packet(category=8, control_id=bytes.fromhex(route['id']), list_index=0, payload=bytes.fromhex(route['control_contract']['payload_hex'])).hex()]
        elif op == 'set_output_route':
            _fields(operation, ('op', 'channel_id', 'destination', 'connected'), label=op)
            route = plan_output_route(inventory, operation['channel_id'], operation['destination'], operation['connected'])
            step.update(route)
            step['frames'] = [build_packet(category=8, control_id=bytes.fromhex(route['id']), list_index=0, payload=bytes.fromhex(route['control_contract']['payload_hex'])).hex()]
        elif op == 'set_group_assignment':
            _fields(operation, ('op', 'channel_id', 'group_channel_id', 'enabled'), label=op)
            assignment = plan_group_assignment(inventory, operation['channel_id'], operation['group_channel_id'], operation['enabled'])
            step.update(assignment)
            step['frames'] = [build_packet(category=8, control_id=bytes.fromhex(assignment['id']), list_index=0, payload=bytes.fromhex(assignment['control_contract']['payload_hex'])).hex()]
        elif op == 'set_control':
            _fields(operation, ('op', 'id', 'value'), ('expected',), op)
            key = operation['id'].lower() if isinstance(operation['id'], str) else operation['id']
            item = next((c for c in catalog['controls'] if c['id'] == key), None)
            if item is None:
                raise ContractError('control id does not exist in the native inventory')
            expected = operation.get('expected', simulated_controls.get(key, item['current_value']))
            if expected is None:
                raise ContractError('control has no finite current value for compare-before-write')
            contract = plan_control_write(catalog, key, operation['value'], expected)
            row = item['source']
            contract.update(control_pointer=row.get('pointer'), owner_module_pointer=row.get('owner_module_pointer'))
            step.update(id=key, control_contract=contract, frames=[build_packet(category=8, control_id=bytes.fromhex(key), list_index=0, payload=bytes.fromhex(contract['payload_hex'])).hex()])
            simulated_controls[key] = contract['value']
        elif op == 'set_channel_name':
            _fields(operation, ('op', 'channel_id', 'name'), label=op)
            channel_id = _integer(operation['channel_id'], 'channel_id', high=65535)
            matches = [c for c in control_inventory.get('channels', []) if c.get('id') == channel_id]
            if len(matches) != 1:
                raise ContractError('channel_id must exist exactly once in the inventory')
            channel = matches[0]
            names = [c for c in catalog['controls'] if c['parameter'] == 255 and c['raw_type'] == 4 and int.from_bytes(bytes.fromhex(c['id'])[3:5], 'little') == channel_id]
            if not names:
                raise ContractError('no native channel-name controller was found')
            name_control = sorted(names, key=lambda c: c['id'])[0]
            assert_control_write_allowed(name_control['id'], raw_type=4,
                definition_rva=name_control['source'].get('definition_rva'),
                module_vmt_rva=name_control['module_vmt_rva'],
                process_output_rva=name_control['process_output_rva'])
            encoded = encode_name(operation['name'], encoding)
            step.update(channel_id=channel_id, name=operation['name'], name_hex=encoded.hex(), expected_name_hex=simulated_names.get(channel_id, channel['name_hex']), channel_pointer=channel['pointer'], id=name_control['id'], control_contract={
                'control_pointer': name_control['source']['pointer'], 'owner_module_pointer': name_control['source']['owner_module_pointer'],
                'module_vmt_rva': name_control['module_vmt_rva'], 'process_output_rva': name_control['process_output_rva'],
                'definition_raw_hex': name_control['definition']['raw_hex'], 'definition_rva': name_control['source'].get('definition_rva'), 'raw_type': 4,
                'response_pointer_binding': name_control.get('response_pointer_binding'),
            }, frames=[build_packet(category=8, control_id=bytes.fromhex(name_control['id']), list_index=0, payload=_string_body(encoded)).hex()])
            simulated_names[channel_id] = encoded.hex()
        elif op == 'create':
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
    # Semantic routes bind whole output/buss sets and socket ownership. Reject
    # overlapping plans before sending instead of discovering stale postconditions
    # after an earlier step has already changed that same set.
    semantic_scopes = set()
    # Native mode callbacks can start resend/timer work and alter parameter
    # domains. Finish that request, then inventory again before sibling writes.
    numeric_steps = [step for step in steps if step['op'] == 'set_control']
    for mode_step in numeric_steps:
        raw_id = bytes.fromhex(mode_step['id'])
        is_mode = (raw_id[:2] == bytes((4, 4)) or raw_id[:3] in (bytes((0x3a, 5, 0)), bytes((0x0b, 5, 3))))
        if is_mode and any(other is not mode_step and other['control_contract']['owner_module_pointer'] == mode_step['control_contract']['owner_module_pointer'] for other in numeric_steps):
            raise ContractError('mode changes and sibling controls require separate requests with a fresh inventory')
    has_route = any(step['op'] in ('set_input_route', 'set_output_route') for step in steps)
    if has_route and any(step['op'] == 'set_channel_name' for step in steps):
        raise ContractError('routing and channel naming require separate requests with a fresh inventory between them')
    for step in steps:
        scopes = set()
        if step['op'] in ('set_input_route', 'set_output_route'):
            scopes.add('route-control:' + step['id'])
            if step['op'] == 'set_output_route':
                scopes.add('output-set:' + str(step['channel_id']))
                scopes.add('physical-output:' + str(step['destination']['source_module_pointer']))
            elif step.get('route_kind') == 'insert_send':
                for endpoint in (step.get('source'), step.get('route_contract', {}).get('previous_endpoint')):
                    if endpoint:
                        scopes.add('physical-output:' + str(endpoint['source_module_pointer']))
        if step['op'] == 'set_group_assignment':
            scopes.add('buss-set:' + str(step['channel_id']))
        if step['op'] == 'set_control':
            raw_id = bytes.fromhex(step['id'])
            if raw_id[:2] == bytes((9, 3)):
                scopes.add('buss-set:' + str(int.from_bytes(raw_id[3:5], 'little')))
        if scopes & semantic_scopes:
            raise ContractError('overlapping routing/buss scopes require separate requests and a fresh inventory')
        semantic_scopes.update(scopes)
    result = {
        'schema_version': SCHEMA_VERSION, 'mode': 'execute', 'fps': fps,
        'encoding': encoding, 'expected_count': request['expected_count'], 'steps': steps,
        'final_expected_count': count, 'runtime_acceptance_verified': False,
    }
    if catalog is not None:
        source_state = inventory.get('after', {}) if isinstance(inventory, dict) else {}
        result['control_inventory_binding'] = {'inventory_sha256': catalog['inventory_sha256'], 'session_path': source_state.get('current_session_path'), 'process_id': inventory.get('process_id'), 'image_base': control_inventory.get('image_base')}
    return result


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
    sources = sorted((skill_root / 'scripts').glob('*.ps1'))
    if script not in sources:
        raise ContractError('bundled Windows runner is missing')
    if any(source.is_symlink() or not source.is_file() for source in sources):
        raise ContractError('bundled PowerShell modules must be self-contained regular files')
    for source in sorted(assets.rglob('*')):
        if source.is_symlink():
            raise ContractError('bundled assets must be self-contained regular files, not symlinks')
        if source.is_file():
            sources.append(source)
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


@locked_target
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
    if result_path.exists() or request_path.exists() or Path(str(result_path) + '.journal.jsonl').exists():
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
        if not native_windows:
            # Parallels Tools can reject otherwise valid large command lines.
            # Keep the complete receipt/request gates in a visible stage file;
            # the short bootstrap authenticates that exact file before running
            # it. Never retry a guest invocation after an uncertain outcome.
            launch_path = stage / 'invoke.ps1'
            with launch_path.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(wrapper)
                stream.flush()
                os.fsync(stream.fileno())
            launch_hash = hashlib.sha256(launch_path.read_bytes()).hexdigest()
            wrapper = ("$ErrorActionPreference='Stop';$p=%s;"
                       "if((Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant()-cne '%s')"
                       "{throw 'Runtime launcher hash mismatch'};"
                       "& ([scriptblock]::Create([IO.File]::ReadAllText($p)))\n") % (
                           _ps_quote(mac_guest_path(launch_path)), launch_hash)
        encoded = base64.b64encode(wrapper.encode('utf-16le')).decode('ascii')
        if not native_windows and len(encoded) >= PARALLELS_WRAPPER_LIMIT:
            raise ContractError('encoded Parallels wrapper exceeds the local 5000-character bound; choose a shorter visible result directory')
        completed = subprocess.run(command_prefix + [encoded], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600, check=False)
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
    except subprocess.TimeoutExpired as error:
        location = '; runtime staging retained at %s' % stage if stage is not None else ''
        raise ContractError('Windows runner timed out; acceptance is unknown. Inspect this request/result and the target before retrying' + location) from error
    except (ContractError, ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as error:
        if stage is not None:
            raise ContractError('%s; runtime staging retained at %s' % (error, stage)) from error
        raise


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    file_commands = {'file-inspect': 'inspect', 'file-clone': 'clone', 'file-edit': 'edit',
                     'file-new': 'new', 'file-compare': 'compare',
                     'file-recall-scope': 'recall-scope'}
    if argv and argv[0] in file_commands:
        from ses_file import main as file_main
        return file_main([file_commands[argv[0]], *argv[1:]])
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name, help_text in (
        ('file-inspect', 'read a .ses file directly without an editor, VM or network'),
        ('file-clone', 'write a byte-exact .ses copy to a new file without an editor'),
        ('file-edit', 'edit supported .ses records directly; see file-edit --help'),
        ('file-new', 'build a new .ses from a topology request without an editor'),
        ('file-compare', 'compare two saved Quantum5 V2025/V2242 Sessions locally'),
        ('file-recall-scope', 'explain saved Quantum5 V2025/V2242 Recall Scope and Safe gates'),
    ):
        commands.add_parser(name, help=help_text)
    recover_parser = commands.add_parser('recover', help='inspect flushed operation receipts; does not call or replay a target')
    recover_parser.add_argument('--journal', type=Path, required=True)
    recover_parser.add_argument('--compiled-request', type=Path)
    recover_parser.add_argument('--output', type=Path)
    author_parser = commands.add_parser('author', help='preview named channel/parameter edits with explicit units and refresh barriers')
    author_parser.add_argument('--request', type=Path, required=True)
    author_parser.add_argument('--inventory', type=Path, required=True)
    author_parser.add_argument('--output', type=Path)
    author_execute_parser = commands.add_parser('author-execute', help='dry-run named edits; --send executes ordered stages with fresh inventories')
    author_execute_parser.add_argument('--request', type=Path, required=True)
    author_execute_parser.add_argument('--inventory', type=Path, required=True)
    author_execute_parser.add_argument('--send', action='store_true')
    profiles_parser = commands.add_parser('profile', help='describe one bundled verified build without calling a target')
    profiles_parser.add_argument('--build-profile')
    plan_parser = commands.add_parser('plan', help='validate a request and construct packets without contacting Windows')
    plan_parser.add_argument('--request', type=Path, required=True)
    plan_parser.add_argument('--output', type=Path)
    plan_parser.add_argument('--inventory', type=Path)
    plan_parser.add_argument('--presets', type=Path)
    execute_parser = commands.add_parser('execute', help='dry-run by default; --send explicitly invokes the local Windows runner')
    execute_parser.add_argument('--request', type=Path, required=True)
    execute_parser.add_argument('--send', action='store_true')
    execute_parser.add_argument('--inventory', type=Path)
    execute_parser.add_argument('--presets', type=Path)
    inspect_parser = commands.add_parser('inspect', help='invoke the runner in read-only inspection mode')
    inspect_parser.add_argument('--encoding', choices=ENCODINGS, default='ascii',
                                help='explicit name-decoding encoding; raw bytes are retained (default: ascii)')
    status_parser = commands.add_parser('status', help='read the original loopback TCP2029 status response; send no application payload')
    inventory_parser = commands.add_parser('inventory', help='read native channel/module/control/routing structures without network or memory writes')
    meters_parser = commands.add_parser('meters', help='read copied native meter registry/output state; no meter writes or audio simulation')
    presets_parser = commands.add_parser('presets', help='read native processing Preset metadata and optional stored content')
    presets_parser.add_argument('--encoding', choices=('ascii', 'cp950'), default='ascii')
    presets_parser.add_argument('--content-index', action='append', type=int, default=[])
    structure_parser = commands.add_parser('structure', help='read and decode the native 123-byte Session structure; no restructuring setter')
    structure_parser.add_argument('--encoding', choices=('ascii', 'cp950'), default='ascii')
    meters_parser.add_argument('--encoding', choices=ENCODINGS, default='ascii')
    meters_parser.add_argument('--channel-id', type=lambda value: int(value, 0))
    meters_parser.add_argument('--limit', type=int, default=256)
    for sub in (status_parser, inventory_parser):
        sub.add_argument('--encoding', choices=ENCODINGS, default='ascii')
    inventory_parser.add_argument('--full-output', action='store_true', help='also print the complete inventory; the result file always contains it')
    inventory_parser.add_argument('--channel-id', type=lambda value: int(value, 0), help='read one raw native uint16 channel ID, such as 0x0100')
    inventory_parser.add_argument('--without-response-tables', action='store_true', help='omit copied numeric curves for a faster structural inventory')
    inventory_parser.add_argument('--include-physical-sources', action='store_true', help='include fitted physical socket endpoints with a filtered channel inventory')
    inventory_parser.add_argument('--include-group-outputs', action='store_true', help='include observed Group Output metadata with a filtered inventory')
    routes_parser = commands.add_parser('routes', help='resolve fitted physical routing endpoints from an existing local inventory')
    routes_parser.add_argument('--inventory', type=Path, required=True)
    routes_parser.add_argument('--direction', choices=('input', 'output'), default='input')
    routes_parser.add_argument('--output', type=Path)
    catalog_parser = commands.add_parser('catalog', help='query typed controls from an existing local inventory; no Windows or network access')
    catalog_parser.add_argument('--inventory', type=Path, required=True)
    catalog_parser.add_argument('--output', type=Path)
    catalog_parser.add_argument('--channel-id', type=lambda value: int(value, 0))
    catalog_parser.add_argument('--module', type=lambda value: int(value, 0))
    catalog_parser.add_argument('--query', default='')
    catalog_parser.add_argument('--writable-only', action='store_true')
    catalog_parser.add_argument('--limit', type=int, default=100)
    for sub in (execute_parser, author_execute_parser, inspect_parser, status_parser, inventory_parser, meters_parser, presets_parser, structure_parser):
        sub.add_argument('--build-profile', help='select a bundled verified profile; omitted readers bind the actual executable hash')
        sub.add_argument('--vm', required=True)
        sub.add_argument('--exe', required=True)
        sub.add_argument('--process-id', type=int)
        sub.add_argument('--result', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'recover':
            result = recovery_report(read_journal(args.journal), compiled_request_path=args.compiled_request)
            if args.output:
                atomic_json(args.output, result)
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return 0
        if args.command == 'profile':
            profile = select_profile(build_profile=args.build_profile)
            result = {**profile_identity(profile), 'model': profile['model'], 'release': profile['release'],
                      'validation': dict(profile['validation']), 'features': dict(profile['features']),
                      'network_used': False, 'target_called': False}
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return 0
        if args.command == 'author':
            result = compile_authoring_request(read_json(args.request), read_json(args.inventory))
            if args.output:
                atomic_json(args.output, result)
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return 0
        if args.command == 'author-execute':
            request, inventory = read_json(args.request), read_json(args.inventory)
            preview = compile_authoring_request(request, inventory)
            profile = profile_for_document(inventory)
            if args.build_profile and profile['id'] != args.build_profile:
                raise ContractError('--build-profile differs from the authoring inventory')
            if not args.send:
                result = {**preview, 'dry_run': True, 'network_used': False, 'windows_runner_invoked': False}
            else:
                result_path = args.result
                request_path = Path(str(result_path)+'.request.json')
                if not result_path.is_absolute() or result_path.exists() or request_path.exists():
                    raise ContractError('author-execute requires a fresh absolute --result and .request.json')
                work_dir = result_path.parent/('author-'+uuid.uuid4().hex[:12])
                work_dir.mkdir(parents=True, mode=0o700)
                atomic_json(request_path, {'schema_version':1, 'kind':'authoring_workflow_intent',
                            'request':request, 'work_dir':str(work_dir)}, exclusive=True)
                artifacts = []
                # The entire workflow holds one host lease. Nested native calls
                # use the undecorated implementation under this existing lease.
                invoke_under_lease = invoke_windows.__wrapped__
                def execute_raw(raw, current, index):
                    plan = compile_request(raw, current)
                    path = work_dir/('stage-%03d.json'%index)
                    artifacts.append({'kind':'execute','stage':index,'path':str(path)})
                    value = invoke_under_lease(plan, vm=args.vm, executable=args.exe,
                                      result_path=path, process_id=args.process_id)
                    return value
                def refresh_inventory(ids, index):
                    values=[]
                    for channel_id in ids:
                        plan={'schema_version':1,'mode':'inventory','fps':request['fps'],
                              'encoding':request['encoding'],'channel_id':channel_id,
                              'include_response_tables':True,'include_physical_sources':False,
                              'include_group_outputs':False,**profile_identity(profile)}
                        path=work_dir/('refresh-%03d-%04x.json'%(index,channel_id))
                        artifacts.append({'kind':'inventory','stage':index,'channel_id':channel_id,'path':str(path)})
                        value=invoke_under_lease(plan, vm=args.vm, executable=args.exe,
                                      result_path=path, process_id=args.process_id)
                        values.append(value)
                    return values
                with target_lock(vm=args.vm, executable=args.exe):
                    result=run_authoring(request, inventory, execute_raw, refresh_inventory)
                result.update(work_dir=str(work_dir), artifacts=artifacts, intent_path=str(request_path),
                              dry_run=False, atomic=False)
                atomic_json(result_path,result,exclusive=True)
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return 0 if result.get('status','ok')=='ok' else 2
        if args.command == 'routes':
            catalog = build_routing_catalog(read_json(args.inventory), direction=args.direction)
            if args.output:
                atomic_json(args.output, catalog)
            print(json.dumps(catalog, ensure_ascii=False, indent=2, allow_nan=False))
            return 0
        if args.command == 'catalog':
            document = read_json(args.inventory)
            source = document.get('inventory', document)
            catalog = normalize_inventory(source)
            if args.output:
                atomic_json(args.output, catalog)
            entries = catalog['controls']
            if args.channel_id is not None:
                _integer(args.channel_id, 'channel_id', high=65535)
                entries = [item for item in entries if int.from_bytes(bytes.fromhex(item['id'])[3:5], 'little') == args.channel_id]
            if args.module is not None:
                _integer(args.module, 'module', high=255)
                entries = [item for item in entries if item['module_type'] == args.module]
            if args.query:
                entries = [item for item in entries if args.query.casefold() in item['name'].casefold()]
            if args.writable_only:
                entries = [item for item in entries if item['writable_candidate']]
            limit = _integer(args.limit, 'limit', 1, 1000)
            fields = ('id', 'name', 'units', 'value_units', 'display_units', 'verified_conversion', 'module_type', 'band', 'channel', 'channel_type', 'raw_type', 'payload_type', 'current_value', 'range', 'allowed_values', 'writable_candidate', 'readonly_reason')
            print(json.dumps({'schema_version': 1, 'total_controls': len(catalog['controls']), 'matching_controls': len(entries), 'returned_controls': min(limit, len(entries)), 'runtime_acceptance_verified': False, 'controls': [{key: item.get(key) for key in fields} for item in entries[:limit]]}, ensure_ascii=False, indent=2, allow_nan=False))
            return 0
        if args.command in ('inspect', 'status', 'inventory', 'meters', 'presets', 'structure'):
            plan = {'schema_version': SCHEMA_VERSION, 'mode': args.command, 'fps': 25, 'encoding': args.encoding}
            if args.build_profile:
                plan.update(profile_identity(select_profile(build_profile=args.build_profile)))
            if args.command == 'inventory':
                plan['include_response_tables'] = not args.without_response_tables
                plan['include_physical_sources'] = args.include_physical_sources
                plan['include_group_outputs'] = args.include_group_outputs
                if args.channel_id is not None:
                    plan['channel_id'] = _integer(args.channel_id, 'channel_id', high=65535)
            if args.command == 'meters':
                plan['limit'] = _integer(args.limit, 'limit', 1, 1000)
                if args.channel_id is not None:
                    plan['channel_id'] = _integer(args.channel_id, 'channel_id', high=65535)
            if args.command == 'presets':
                plan['content_indices'] = [_integer(index, 'content index', high=9999) for index in args.content_index]
        else:
            request = read_json(args.request)
            if args.command == 'execute' and args.build_profile:
                if not isinstance(request, dict):
                    raise ContractError('request must be a JSON object')
                if request.get('build_profile') not in (None, args.build_profile):
                    raise ContractError('--build-profile differs from the request build_profile')
                request = {**request, 'build_profile': args.build_profile}
            inventory = read_json(args.inventory) if args.inventory else None
            presets = read_json(args.presets) if args.presets else None
            plan = compile_request(request, inventory, presets)
            if args.command == 'execute' and args.build_profile and plan['build_profile'] != args.build_profile:
                raise ContractError('--build-profile differs from the compiled request/inventory')
        if args.command == 'plan':
            if args.output:
                atomic_json(args.output, plan)
            result = plan
        elif args.command == 'execute' and not args.send:
            result = {**plan, 'dry_run': True, 'network_used': False, 'windows_runner_invoked': False}
        else:
            result = invoke_windows(plan, vm=args.vm, executable=args.exe,
                                    result_path=args.result, process_id=args.process_id)
        output = result
        if args.command == 'structure' and result.get('status') == 'ok':
            result['structure'] = decode_structure_readback(result['structure'])
        if args.command == 'inventory' and not args.full_output and result.get('status') == 'ok':
            data = result.get('inventory', {})
            output = {key: result[key] for key in ('status', 'request_sha256', 'process_id', 'runtime_stage_path') if key in result}
            output.update(result_path=str(args.result), inventory={key: data.get(key) for key in ('group_count', 'channel_count', 'module_count', 'controller_count', 'unknown_node_count', 'structure_verified', 'io_inventory_complete')})
            output['session'] = {key: result.get('after', {}).get(key) for key in ('current_session_path', 'fps', 'snapshot_count')}
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        if result.get('status') == 'error':
            return 2
    except (ContractError, ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': str(error), 'runtime_acceptance_verified': False}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

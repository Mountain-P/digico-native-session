#!/usr/bin/env python3
"""Local stdio MCP adapter for the existing typed DiGiCo CLI.

No socket server, raw packet API, UI automation, or OSC. Mutating calls plan by
 default; only an actual boolean send=true adds CLI --send. MCP stdout contains
one UTF-8 JSON-RPC object per line. Artifacts stay in a private visible work dir.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import uuid

MODERN_VERSION = '2026-07-28'
LEGACY_VERSION = '2025-11-25'
SUPPORTED_VERSIONS = [MODERN_VERSION, LEGACY_VERSION]
SERVER_INFO = {'name': 'digico-native-session', 'version': '0.2.0'}
MAX_MESSAGE_BYTES = 2 * 1024 * 1024
MAX_RESULT_BYTES = 128 * 1024 * 1024
ENCODINGS = ['ascii', 'cp950', 'utf-8']
CLI_PATH = Path(__file__).resolve().with_name('native_session.py')


class ToolError(ValueError):
    pass


class RpcError(ValueError):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def _object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('duplicate JSON field: ' + key)
        out[key] = value
    return out


def parse_json(text):
    return json.loads(text, object_pairs_hook=_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON number')))


def schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required),
            'additionalProperties': False}


def operation(name, properties, required):
    return schema({'op': {'const': name}, **properties}, ('op', *required))


INDEX = {'type': 'integer', 'minimum': 0, 'maximum': 2000}
CHANNEL_ID = {'type': 'integer', 'minimum': 0, 'maximum': 65535,
              'description': 'Raw native uint16 channel ID (e.g. 256 for 0x0100), not a logical channel index.'}
NAME = {'type': 'string', 'minLength': 1, 'description': 'Strict chosen encoding, at most 31 bytes; never truncated.'}
TC = {'type': 'string', 'pattern': r'^[0-9]{2}:[0-9]{2}:[0-9]{2}:[0-9]{2}$'}
SOURCE_SOCKET = schema({key: {'type': 'integer', 'minimum': 0, 'maximum': 255}
                        for key in ('port_type', 'port_number', 'socket_index')},
                       ('port_type', 'port_number', 'socket_index'))
PATH = {'type': 'string', 'description': 'Absolute ASCII Windows drive path ending in .ses; validated by the native CLI.'}
OPERATIONS = [
    operation('export_session', {'source': PATH, 'destination': PATH}, ('source', 'destination')),
    operation('set_input_route', {'channel_id': CHANNEL_ID,
        'source': {'oneOf': [SOURCE_SOCKET, {'type': 'null'}]},
        'route_kind': {'type': 'string', 'enum': ['main', 'alt', 'key', 'mustard_key', 'insert_return', 'insert_send'], 'default': 'main'},
        'insert_index': {'type': 'integer', 'minimum': 0, 'maximum': 1, 'default': 0}},
        ('channel_id', 'source')),
    operation('set_output_route', {
        'channel_id': {'type': 'integer', 'minimum': 256, 'maximum': 511},
        'destination': SOURCE_SOCKET, 'connected': {'type': 'boolean'}},
        ('channel_id', 'destination', 'connected')),
    operation('set_group_assignment', {
        'channel_id': {'type': 'integer', 'minimum': 256, 'maximum': 511},
        'group_channel_id': {'type': 'integer', 'minimum': 1536, 'maximum': 1791},
        'enabled': {'type': 'boolean'}}, ('channel_id', 'group_channel_id', 'enabled')),
    operation('set_control', {'id': {'type': 'string', 'pattern': '^[0-9a-fA-F]{10}$'},
                             'value': {'type': 'number', 'description': 'Native internal value in the catalog current_value/range domain. display_units is a label; never infer ms/s or other conversion.'},
                             'expected': {'type': 'number', 'description': 'Compare-before-write value in the same native internal units.'}}, ('id', 'value')),
    operation('set_channel_name', {'channel_id': CHANNEL_ID, 'name': NAME}, ('channel_id', 'name')),
    operation('create', {'index': {'oneOf': [INDEX, {'const': 'append'}]}, 'name': NAME, 'timecode': TC}, ('index', 'name')),
    operation('delete', {'index': INDEX}, ('index',)),
    operation('rename', {'index': INDEX, 'name': NAME}, ('index', 'name')),
    operation('timecode', {'index': INDEX, 'timecode': TC}, ('index', 'timecode')),
    operation('save_as', {'path': PATH, 'overwrite': {'type': 'boolean', 'default': False}}, ('path',)),
    operation('save', {}, ()),
    operation('load', {'path': PATH}, ('path',)),
    operation('new_session', {'path': PATH}, ('path',)),
]
PRESET_SCOPE_NAMES = ['input', 'input_gain', 'input_route', 'eq', 'dynamics', 'aux', 'group',
                      'tidal_preamp', 'tidal_eq', 'tidal_comp', 'tidal_gate']
PRESET_SCOPES = {'type': 'array', 'minItems': 1, 'maxItems': len(PRESET_SCOPE_NAMES),
                 'uniqueItems': True, 'items': {'type': 'string', 'enum': PRESET_SCOPE_NAMES}}
VERIFY_CONTROL_IDS = {'type': 'array', 'minItems': 1, 'maxItems': 64, 'uniqueItems': True,
                      'items': {'type': 'string', 'pattern': '^[0-9a-fA-F]{10}$'}}
PRESET_INDEX = {'type': 'integer', 'minimum': 0, 'maximum': 9999}
PRESET_INPUT = {'type': 'integer', 'minimum': 256, 'maximum': 511}
PRESET_COMMON = {'index': PRESET_INDEX, 'group_name': NAME}
PRESET_SCHEMAS = [
    operation('preset_capture', {'channel_id': PRESET_INPUT, 'group_name': NAME, 'name': NAME,
        'force_mono': {'type': 'boolean', 'default': False}, 'verify_control_ids': VERIFY_CONTROL_IDS},
        ('channel_id', 'group_name')),
    operation('preset_rename', {**PRESET_COMMON, 'name': NAME}, ('index', 'group_name', 'name')),
    operation('preset_update', {**PRESET_COMMON, 'channel_id': PRESET_INPUT,
        'verify_control_ids': VERIFY_CONTROL_IDS}, ('index', 'group_name', 'channel_id')),
    operation('preset_recall', {**PRESET_COMMON, 'channel_id': PRESET_INPUT,
        'scopes': PRESET_SCOPES, 'verify_control_ids': VERIFY_CONTROL_IDS},
        ('index', 'group_name', 'channel_id', 'scopes', 'verify_control_ids')),
    operation('preset_set_scope', {'scopes': PRESET_SCOPES}, ('scopes',)),
    operation('preset_set_scope', {'scope_hex': {'type': 'string', 'pattern': '^[0-9a-fA-F]{64}$',
        'description': 'Exact copied 32-byte scope from the read-only preset result; named scopes are preferred.'}}, ('scope_hex',)),
    operation('preset_lock', {**PRESET_COMMON, 'locked': {'type': 'boolean'}}, ('index', 'group_name', 'locked')),
    operation('preset_delete', PRESET_COMMON, ('index', 'group_name')),
]
PRESET_NAMES = frozenset(item['properties']['op']['const'] for item in PRESET_SCHEMAS)
OPERATIONS += PRESET_SCHEMAS
REQUEST_SCHEMA = schema({'schema_version': {'type': 'integer', 'const': 1},
                         'fps': {'type': 'integer', 'const': 25},
                         'encoding': {'type': 'string', 'enum': ENCODINGS},
                         'expected_count': INDEX,
                         'operations': {'type': 'array', 'minItems': 1, 'maxItems': 1000,
                                        'items': {'oneOf': OPERATIONS}}},
                        ('schema_version', 'fps', 'encoding', 'expected_count', 'operations'))
PRESET_REQUEST_SCHEMA = schema({**REQUEST_SCHEMA['properties'],
    'encoding': {'type': 'string', 'enum': ['ascii', 'cp950']},
    'operations': {'type': 'array', 'minItems': 1, 'maxItems': 1,
                   'items': {'oneOf': PRESET_SCHEMAS}}}, REQUEST_SCHEMA['required'])
READ_SCHEMA = schema({'encoding': {'type': 'string', 'enum': ENCODINGS, 'default': 'ascii'}})
STRUCTURE_SCHEMA = schema({'encoding': {'type': 'string', 'enum': ['ascii', 'cp950'], 'default': 'ascii'}})
PLAN_SCHEMA = schema({'request': REQUEST_SCHEMA,
                      'inventory_path': {'type': 'string', 'description': 'Absolute local JSON result path from a fresh native inventory.'},
                      'presets_path': {'type': 'string', 'description': 'Absolute local presets/preset_execute result path; required for any preset operation.'}}, ('request',))
EXECUTE_SCHEMA = schema({**PLAN_SCHEMA['properties'],
                        'send': {'type': 'boolean', 'default': False,
                                 'description': 'Only literal true executes. Omitted/false produces a dry-run plan.'}}, ('request',))
INVENTORY_SCHEMA = schema({**READ_SCHEMA['properties'],
    'channel_id': CHANNEL_ID,
    'without_response_tables': {'type': 'boolean', 'default': False},
    'include_physical_sources': {'type': 'boolean', 'default': False},
    'include_group_outputs': {'type': 'boolean', 'default': False}})
METERS_SCHEMA = schema({**READ_SCHEMA['properties'], 'channel_id': CHANNEL_ID,
    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 1000, 'default': 64}})
CATALOG_SCHEMA = schema({
    'inventory_path': PLAN_SCHEMA['properties']['inventory_path'],
    'channel_id': CHANNEL_ID,
    'module': {'type': 'integer', 'minimum': 0, 'maximum': 255},
    'query': {'type': 'string'}, 'writable_only': {'type': 'boolean', 'default': False},
    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 1000, 'default': 25}}, ('inventory_path',))

PRESETS_SCHEMA = schema({'encoding': {'type': 'string', 'enum': ['ascii', 'cp950'], 'default': 'ascii'},
    'content_indices': {'type': 'array', 'maxItems': 64, 'uniqueItems': True,
                        'items': PRESET_INDEX,
                        'description': 'Explicit indices whose copied processing content is needed, e.g. for a later scoped recall.'}})
PRESET_EXECUTE_SCHEMA = schema({**EXECUTE_SCHEMA['properties'], 'request': PRESET_REQUEST_SCHEMA},
                               ('request', 'presets_path'))


def _tool(name, description, input_schema, read_only):
    return {'name': name, 'description': description, 'inputSchema': input_schema,
            'annotations': {'readOnlyHint': read_only, 'destructiveHint': not read_only,
                            'idempotentHint': read_only, 'openWorldHint': False}}


TOOLS = [
    _tool('digico_status', 'Read original local loopback status through the typed CLI. No application payload is sent.', READ_SCHEMA, True),
    _tool('digico_inspect', 'Read editor session state through read-only process memory. Returns a local artifact path and summary.', READ_SCHEMA, True),
    _tool('digico_structure', 'Read the native Session structure configuration through read-only process memory. Returns decoded configuration and its local artifact; no structure setter or memory-address arguments.', STRUCTURE_SCHEMA, True),
    _tool('digico_inventory', 'Read native channel/module/controller definitions and curves. Full inventory stays in its local JSON file; no memory writes.', INVENTORY_SCHEMA, True),
    _tool('digico_meters', 'Read cached native meter registry/output codes through read-only process memory. No subscription, injection or audio simulation. Update age and physical audio are not verified; full raw result stays local.', METERS_SCHEMA, True),
    _tool('digico_catalog', 'Query an existing local inventory by raw channel ID, native module type or name. No VM/process/network access. Returns typed control candidates and a local artifact.', CATALOG_SCHEMA, True),
    _tool('digico_presets', 'Read native input-preset metadata, scope and explicitly selected processing content. Full raw content stays in the local artifact; no preset mutation or library persistence claim.', PRESETS_SCHEMA, True),
    _tool('digico_preset_execute', 'Plan one typed input-preset operation by default; literal send=true executes through the existing CLI. Requires fresh presets_path; recall also requires explicit scope, verify_control_ids and fresh inventory_path. No raw commands.', PRESET_EXECUTE_SCHEMA, False),
    _tool('digico_plan', 'Validate and compile the entire typed request without contacting Windows, a VM or network. Non-Cue edits require inventory_path.', PLAN_SCHEMA, True),
    _tool('digico_execute', 'Dry-run by default. Explicit boolean send=true invokes the verified local Windows CLI; no raw host, packet, OSC or GUI access. Inspect the result after any timeout before retrying.', EXECUTE_SCHEMA, False),
]
TOOL_BY_NAME = {item['name']: item for item in TOOLS}


def validate(value, spec, label='arguments'):
    """Validate only the JSON Schema keywords emitted by this fixed tool set."""
    if 'oneOf' in spec:
        matches = 0
        for alternative in spec['oneOf']:
            try:
                validate(value, alternative, label)
                matches += 1
            except ToolError:
                pass
        if matches != 1:
            raise ToolError(label + ' must match exactly one supported typed operation')
        return
    if 'const' in spec and (type(value) is not type(spec['const']) or value != spec['const']):
        raise ToolError(label + ' has an unsupported constant value')
    kind = spec.get('type')
    valid_type = {'object': isinstance(value, dict), 'array': isinstance(value, list),
                  'string': isinstance(value, str), 'boolean': type(value) is bool,
                  'integer': type(value) is int,
                  'number': type(value) in (int, float), 'null': value is None}
    if kind and not valid_type[kind]:
        raise ToolError(label + ' must be a JSON ' + kind)
    if 'enum' in spec and value not in spec['enum']:
        raise ToolError(label + ' has an unsupported value')
    if kind == 'object':
        missing = set(spec.get('required', ())) - value.keys()
        if missing:
            raise ToolError(label + ' missing fields: ' + ', '.join(sorted(missing)))
        properties = spec.get('properties', {})
        unknown = value.keys() - properties.keys()
        if unknown and spec.get('additionalProperties') is False:
            raise ToolError(label + ' unknown fields: ' + ', '.join(sorted(unknown)))
        for key in value.keys() & properties.keys():
            validate(value[key], properties[key], label + '.' + key)
    elif kind == 'array':
        if not spec.get('minItems', 0) <= len(value) <= spec.get('maxItems', sys.maxsize):
            raise ToolError(label + ' array length is outside the supported bound')
        for item in value:
            validate(item, spec['items'], label + '[]')
        if spec.get('uniqueItems') and len({json.dumps(item, sort_keys=True, allow_nan=False) for item in value}) != len(value):
            raise ToolError(label + ' contains duplicate items')
    elif kind == 'string':
        if len(value) < spec.get('minLength', 0) or '\0' in value:
            raise ToolError(label + ' is empty or contains NUL')
        if 'pattern' in spec and re.fullmatch(spec['pattern'], value) is None:
            raise ToolError(label + ' has an invalid format')
    elif kind in ('integer', 'number'):
        if isinstance(value, float) and (value != value or value in (float('inf'), -float('inf'))):
            raise ToolError(label + ' must be finite')
        if value < spec.get('minimum', -float('inf')) or value > spec.get('maximum', float('inf')):
            raise ToolError(label + ' is outside the supported bound')


def _read_document(path):
    if not path.is_file() or path.stat().st_size > MAX_RESULT_BYTES:
        raise ToolError('CLI result file is absent or exceeds the local size bound')
    try:
        result = parse_json(path.read_text(encoding='utf-8-sig'))
    except (ValueError, UnicodeError) as error:
        raise ToolError('CLI result file is not valid UTF-8 JSON') from error
    if not isinstance(result, dict):
        raise ToolError('CLI result must be a JSON object')
    return result


class Adapter:
    def __init__(self, *, vm, executable, work_dir=None, process_id=None,
                 timeout=180, run_process=subprocess.run, cli_path=CLI_PATH):
        self.vm, self.executable = vm, executable
        self.work_dir = Path(work_dir or (Path.home() / 'Documents' / 'DiGiCoWork')).expanduser().absolute()
        self.process_id, self.timeout = process_id, timeout
        self.run_process, self.cli_path = run_process, Path(cli_path).resolve()
        if not isinstance(vm, str) or not vm or '\0' in vm or not isinstance(executable, str) or not executable or '\0' in executable:
            raise ToolError('configure --vm and --exe for the existing local editor')
        if type(timeout) not in (int, float) or not 1 <= timeout <= 600:
            raise ToolError('timeout must be 1..600 seconds')
        if process_id is not None and (type(process_id) is not int or process_id < 1):
            raise ToolError('process-id must be a positive integer')
        # Parallels Home share cannot reliably expose hidden path components.
        if os.name != 'nt' and any(part.startswith('.') for part in self.work_dir.parts if part not in ('.', '..')):
            raise ToolError('use a visible work directory such as ~/Documents/DiGiCoWork')

    def _workspace(self):
        if self.work_dir.is_symlink():
            raise ToolError('work directory must not be a symlink')
        self.work_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        if not self.work_dir.is_dir():
            raise ToolError('work directory is not a directory')
        if os.name != 'nt' and stat.S_IMODE(self.work_dir.stat().st_mode) & 0o077:
            raise ToolError('work directory must have owner-only permissions (chmod 700), or choose a new directory')
        token = uuid.uuid4().hex
        request_path = self.work_dir / (token + '.request.json')
        result_path = self.work_dir / (token + '.result.json')
        if request_path.exists() or result_path.exists():
            raise ToolError('fresh artifact paths unexpectedly exist')
        return request_path, result_path

    @staticmethod
    def _write_new(path, value):
        encoded = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded)

    def call(self, name, arguments):
        tool = TOOL_BY_NAME.get(name)
        if tool is None:
            raise RpcError(-32602, 'Unknown tool: ' + str(name))
        validate(arguments, tool['inputSchema'])
        tool_mode = name.removeprefix('digico_')
        mode = 'execute' if tool_mode == 'preset_execute' else tool_mode
        operations = arguments.get('request', {}).get('operations', [])
        if any(step.get('op') == 'export_session' for step in operations) and len(operations) != 1:
            raise ToolError('export_session must be the only operation')
        preset_request = any(step.get('op') in PRESET_NAMES for step in operations)
        if preset_request:
            if len(operations) != 1:
                raise ToolError('use one high-level preset operation per request')
            if arguments['request']['encoding'] not in ('ascii', 'cp950'):
                raise ToolError('preset operations require ascii or cp950 without replacement')
            if arguments.get('presets_path') is None:
                raise ToolError('preset operations require presets_path from a fresh query')
        presets_path = arguments.get('presets_path')
        if presets_path is not None:
            presets_path = Path(presets_path)
            if not presets_path.is_absolute() or not presets_path.is_file():
                raise ToolError('presets_path must be an existing absolute local JSON file')
        inventory_path = arguments.get('inventory_path')
        if inventory_path is not None:
            inventory_path = Path(inventory_path)
            if not inventory_path.is_absolute() or not inventory_path.is_file():
                raise ToolError('inventory_path must be an existing absolute local JSON file')
        request_path, result_path = self._workspace()
        command = [sys.executable, str(self.cli_path), mode]
        send = mode == 'execute' and arguments.get('send') is True
        if mode in ('plan', 'execute'):
            self._write_new(request_path, arguments['request'])
            command += ['--request', str(request_path)]
            if inventory_path is not None:
                command += ['--inventory', str(inventory_path)]
            if presets_path is not None:
                command += ['--presets', str(presets_path)]
            if mode == 'plan':
                command += ['--output', str(result_path)]
            elif send:
                command.append('--send')
        if mode == 'catalog':
            command += ['--inventory', str(inventory_path), '--limit', str(arguments.get('limit', 25))]
            for key, option in (('channel_id', '--channel-id'), ('module', '--module'), ('query', '--query')):
                if key in arguments:
                    command += [option, str(arguments[key])]
            if arguments.get('writable_only') is True:
                command.append('--writable-only')
        if mode not in ('plan', 'catalog'):
            command += ['--vm', self.vm, '--exe', self.executable, '--result', str(result_path)]
            if self.process_id is not None:
                command += ['--process-id', str(self.process_id)]
        if mode in ('status', 'inspect', 'structure', 'inventory', 'meters', 'presets'):
            command += ['--encoding', arguments.get('encoding', 'ascii')]
        if mode == 'inventory':
            if 'channel_id' in arguments:
                command += ['--channel-id', str(arguments['channel_id'])]
            if arguments.get('without_response_tables', False) is True:
                command.append('--without-response-tables')
            if arguments.get('include_physical_sources', False) is True:
                command.append('--include-physical-sources')
            if arguments.get('include_group_outputs', False) is True:
                command.append('--include-group-outputs')
        if mode == 'presets':
            for index in arguments.get('content_indices', []):
                command += ['--content-index', str(index)]
        if mode == 'meters':
            command += ['--limit', str(arguments.get('limit', 64))]
            if 'channel_id' in arguments:
                command += ['--channel-id', str(arguments['channel_id'])]
        try:
            completed = self.run_process(command, capture_output=True, text=True,
                                         encoding='utf-8', errors='strict', timeout=self.timeout,
                                         check=False, shell=False, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as error:
            raise ToolError('CLI timed out; execution acceptance is unknown. Inspect before retrying. Artifacts: ' + str(result_path)) from error
        except (OSError, UnicodeError) as error:
            raise ToolError('CLI could not complete; acceptance is unknown. Artifacts: ' + str(result_path)) from error
        if completed.returncode != 0:
            # CLI stdout/stderr are diagnostic only. Never reinterpret failure as success.
            detail = (completed.stderr or completed.stdout or '').strip()[:800]
            raise ToolError('CLI failed (exit %s); acceptance is not confirmed. Artifacts: %s. %s' %
                            (completed.returncode, result_path, detail))
        try:
            stdout = parse_json(completed.stdout)
        except (ValueError, UnicodeError) as error:
            raise ToolError('CLI stdout was not a JSON result; acceptance is unknown. Artifacts: ' + str(result_path)) from error
        if not isinstance(stdout, dict) or stdout.get('status') == 'error' or stdout.get('error') is not None:
            raise ToolError('CLI reported an error; acceptance is not confirmed. Artifacts: ' + str(result_path))
        if mode == 'execute' and not send:
            if stdout.get('dry_run') is not True or stdout.get('windows_runner_invoked') is not False:
                raise ToolError('CLI did not confirm dry-run isolation')
            self._write_new(result_path, stdout)
        if mode == 'catalog':
            self._write_new(result_path, stdout)
        result = _read_document(result_path)
        if mode in ('status', 'inspect', 'structure', 'inventory', 'meters', 'presets') or send:
            if result.get('status') != 'ok':
                raise ToolError('Fresh runner result did not report status=ok: ' + str(result_path))
        return self._summary(mode, result, request_path, result_path, send)

    @staticmethod
    def _summary(mode, result, request_path, result_path, send):
        out = {'mode': mode, 'result_path': str(result_path), 'dry_run': mode == 'plan' or (mode == 'execute' and not send),
               'windows_runner_invoked': mode in ('status', 'inspect', 'structure', 'inventory', 'meters', 'presets') or (mode == 'execute' and send),
               'send_requested': send, 'read_only': mode in ('status', 'inspect', 'structure', 'inventory', 'meters', 'presets', 'catalog', 'plan')}
        if result.get('mode') is not None and result['mode'] != mode:
            out['compiled_mode'] = result['mode']
        if request_path.exists():
            out['request_path'] = str(request_path)
        for key in ('status', 'request_sha256', 'process_id', 'runtime_stage_path',
                    'runtime_acceptance_verified', 'memory_read_only', 'ui_used', 'transport'):
            if key in result:
                out[key] = result[key]
        if 'steps' in result:
            out['operation_count'] = len(result['steps'])
            out['operations'] = [step.get('op') for step in result['steps']]
            out['expected_count'] = result.get('expected_count')
            out['final_expected_count'] = result.get('final_expected_count')
        if isinstance(result.get('verified_steps'), list):
            out['verified_step_count'] = len(result['verified_steps'])
            preset_steps = [step for step in result['verified_steps']
                            if isinstance(step, dict) and step.get('op') in PRESET_NAMES]
            if preset_steps:
                fields = ('op', 'verified', 'preset_count', 'scope_hex', 'metadata_verified',
                          'stored_controller_subset_verified', 'processing_verified',
                          'audio_delivery_verified')
                out['preset_verification'] = {'steps': [
                    {key: step[key] for key in fields if key in step} for step in preset_steps]}
                for key in ('original_preset_contents_verified', 'original_scope_restored'):
                    if key in result:
                        out['preset_verification'][key] = result[key]
        if isinstance(result.get('after'), dict):
            out['state'] = {key: result['after'][key] for key in ('snapshot_count', 'fps', 'current_session_path', 'windows_acp') if key in result['after']}
        if mode == 'structure':
            data = result.get('structure')
            if (not isinstance(data, dict) or data.get('memory_read_only') is not True
                    or data.get('network_used') is not False or data.get('ui_used') is not False
                    or data.get('alias_verified') is not True
                    or data.get('consistent_double_read') is not True
                    or not isinstance(data.get('raw_hex'), str)
                    or re.fullmatch('[0-9a-fA-F]{246}', data['raw_hex']) is None):
                raise ToolError('Session structure result lacks the read-only/consistent 123-byte contract')
            # Use the bundled pure decoder; trust neither an unvalidated nested
            # decoded value nor stdout summaries in place of the fresh artifact.
            decoder_path = Path(__file__).resolve().with_name('session_structure.py')
            spec = importlib.util.spec_from_file_location('_digico_mcp_structure', decoder_path)
            decoder = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(decoder)
                decoder.decode_structure_readback(data)
                decoded = decoder.decode_console_record(bytes.fromhex(data['raw_hex']))
            except (OSError, TypeError, ValueError) as error:
                raise ToolError('Session structure could not be decoded from its copied bytes') from error
            out['structure'] = {key: value for key, value in decoded.items()
                                if key not in ('raw_hex', 'record_rva', 'executable_sha256')}
            out['structure_provenance'] = {key: data[key] for key in (
                'alias_verified', 'consistent_double_read', 'memory_read_only', 'ui_used', 'network_used')}
        if mode == 'catalog':
            for key in ('total_controls', 'matching_controls', 'returned_controls'):
                out[key] = result.get(key)
            # Bound model output; the entire filtered result remains in result_path.
            selected = []
            used = 0
            for control in result.get('controls', []):
                size = len(json.dumps(control, ensure_ascii=True, allow_nan=False).encode('utf-8'))
                if used + size > 60000:
                    break
                selected.append(control)
                used += size
            out['controls'] = selected
            out['model_returned_controls'] = len(selected)
            out['response_truncated'] = len(selected) < len(result.get('controls', []))
        preset_state = result.get('presets_after', result.get('presets'))
        if isinstance(preset_state, dict):
            if (preset_state.get('schema_version') != 1
                    or preset_state.get('subsystem') != 'native-presets-readonly'
                    or preset_state.get('memory_read_only') is not True):
                raise ToolError('Preset result lacks the read-only metadata schema')
            out['presets_snapshot'] = {key: preset_state.get(key) for key in (
                'preset_count', 'scope_hex', 'metadata_sha256', 'current_session_path',
                'library_file_path_established', 'memory_read_only', 'text_encoding')}
            selected = []
            used = 0
            for preset in preset_state.get('presets', []):
                compact = {key: preset.get(key) for key in ('index', 'name', 'group_name',
                    'name_hex', 'group_name_hex', 'lock_flag', 'channel_count', 'mix_preset')}
                if isinstance(preset.get('content'), dict):
                    compact['content'] = {key: preset['content'].get(key) for key in (
                        'channel_snapshot_count', 'controller_count', 'controller_content_sha256')}
                size = len(json.dumps(compact, ensure_ascii=True, allow_nan=False).encode('utf-8'))
                if used + size > 60000:
                    break
                selected.append(compact)
                used += size
            out['preset_metadata'] = selected
            out['model_returned_presets'] = len(selected)
            out['response_truncated'] = len(selected) < len(preset_state.get('presets', []))
        elif mode == 'presets':
            raise ToolError('Read-only presets result is missing its metadata state')
        if mode == 'meters':
            data = result.get('meters')
            if (not isinstance(data, dict) or data.get('memory_read_only') is not True
                    or data.get('network_used') is not False
                    or data.get('physical_audio_verified') is not False
                    or data.get('structure_verified') is not True or data.get('ui_used') is not False):
                raise ToolError('Meter result lacks the required read-only/provenance contract')
            out['meter_snapshot'] = {key: data.get(key) for key in ('sampled_start_utc',
                'sampled_end_utc', 'values_atomic', 'structure_verified', 'freshness',
                'memory_read_only', 'network_used', 'physical_audio_verified',
                'matching_count', 'returned_count', 'truncated', 'manager')}
            selected = []
            used = 0
            for meter in data.get('meters', []):
                compact = {key: meter.get(key) for key in ('raw_id_hex', 'name', 'channel_id',
                    'registry_index', 'stream_index', 'unit', 'freshness', 'physical_audio_verified')}
                for key in ('output', 'secondary'):
                    compact[key] = {field: meter.get(key, {}).get(field) for field in ('raw_hex', 'native_code', 'finite')}
                size = len(json.dumps(compact, ensure_ascii=True, allow_nan=False).encode('utf-8'))
                if used + size > 60000:
                    break
                selected.append(compact)
                used += size
            out['meter_values'] = selected
            out['model_returned_meters'] = len(selected)
            out['response_truncated'] = len(selected) < len(data.get('meters', []))
        if isinstance(result.get('inventory'), dict):
            inv = result['inventory']
            out['inventory'] = {key: inv.get(key) for key in ('group_count', 'channel_count', 'module_count', 'controller_count', 'unknown_node_count', 'structure_verified', 'io_inventory_complete')}
        return out


class Server:
    def __init__(self, adapter):
        self.adapter = adapter
        self.legacy_initialized = False
        self.legacy_ready = False

    @staticmethod
    def error(error, request_id=None):
        value = {'jsonrpc': '2.0', 'error': {'code': error.code, 'message': error.message}}
        if request_id is not None:
            value['id'] = request_id
        if error.data is not None:
            value['error']['data'] = error.data
        return value

    def _version(self, params):
        meta = params.get('_meta')
        if meta is not None:
            if not isinstance(meta, dict):
                raise RpcError(-32602, '_meta must be an object')
            version = meta.get('io.modelcontextprotocol/protocolVersion')
            if version is not None:
                if version != MODERN_VERSION:
                    raise RpcError(-32022, 'Unsupported protocol version', {'supported': SUPPORTED_VERSIONS, 'requested': version})
                if not isinstance(meta.get('io.modelcontextprotocol/clientCapabilities'), dict):
                    raise RpcError(-32602, 'Modern requests require per-request clientCapabilities')
                info = meta.get('io.modelcontextprotocol/clientInfo')
                if info is not None and (not isinstance(info, dict) or not isinstance(info.get('name'), str) or not isinstance(info.get('version'), str)):
                    raise RpcError(-32602, 'clientInfo requires name and version strings')
                return MODERN_VERSION
        if self.legacy_ready:
            return LEGACY_VERSION
        raise RpcError(-32600, 'Send modern per-request metadata or complete the legacy initialize handshake')

    def handle(self, message):
        request_id = message.get('id') if isinstance(message, dict) else None
        try:
            if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
                raise RpcError(-32600, 'Invalid JSON-RPC request')
            if 'id' in message and type(request_id) not in (int, str):
                request_id = None
                raise RpcError(-32600, 'Request id must be a non-null string or integer')
            params = message.get('params', {})
            if not isinstance(params, dict):
                raise RpcError(-32602, 'params must be an object')
            method = message['method']
            if 'id' not in message:
                if method == 'notifications/initialized' and self.legacy_initialized:
                    self.legacy_ready = True
                # Notifications never invoke tools or receive a response.
                return None
            if method == 'initialize':
                if (not isinstance(params.get('protocolVersion'), str)
                        or not isinstance(params.get('capabilities'), dict)
                        or not isinstance(params.get('clientInfo'), dict)
                        or not isinstance(params['clientInfo'].get('name'), str)
                        or not isinstance(params['clientInfo'].get('version'), str)):
                    raise RpcError(-32602, 'initialize requires protocolVersion, capabilities and clientInfo')
                if self.legacy_initialized:
                    raise RpcError(-32600, 'Legacy connection is already initialized')
                self.legacy_initialized = True
                result = {'protocolVersion': LEGACY_VERSION, 'capabilities': {'tools': {'listChanged': False}},
                          'serverInfo': SERVER_INFO,
                          'instructions': 'Local Quantum1 V2126 only. Changes dry-run unless send=true; inspect artifacts after uncertain outcomes.'}
            else:
                # Legacy ping is permitted before the initialization exchange.
                version = (LEGACY_VERSION if method == 'ping' and not params.get('_meta')
                           else self._version(params))
                if method == 'server/discover':
                    if version != MODERN_VERSION:
                        raise RpcError(-32602, 'server/discover requires modern per-request metadata')
                    result = {'supportedVersions': SUPPORTED_VERSIONS, 'capabilities': {'tools': {}},
                              'ttlMs': 300000, 'cacheScope': 'public'}
                elif method == 'ping':
                    result = {}
                elif method == 'tools/list':
                    if params.get('cursor') is not None:
                        raise RpcError(-32602, 'Unknown tools cursor; this fixed list is a single page')
                    result = {'tools': TOOLS}
                    if version == MODERN_VERSION:
                        result.update(ttlMs=300000, cacheScope='public')
                elif method == 'tools/call':
                    if not isinstance(params.get('name'), str) or not isinstance(params.get('arguments', {}), dict):
                        raise RpcError(-32602, 'tools/call requires a name and object arguments')
                    try:
                        summary = self.adapter.call(params['name'], params.get('arguments', {}))
                        result = {'content': [{'type': 'text', 'text': json.dumps(summary, ensure_ascii=False, allow_nan=False)}],
                                  'structuredContent': summary, 'isError': False}
                    except ToolError as error:
                        summary = {'error': str(error), 'runtime_acceptance_verified': False}
                        result = {'content': [{'type': 'text', 'text': json.dumps(summary, ensure_ascii=False)}],
                                  'structuredContent': summary, 'isError': True}
                else:
                    raise RpcError(-32601, 'Method not found: ' + method)
                if version == MODERN_VERSION:
                    result = {**result, 'resultType': 'complete',
                              '_meta': {'io.modelcontextprotocol/serverInfo': SERVER_INFO}}
            return {'jsonrpc': '2.0', 'id': request_id, 'result': result}
        except RpcError as error:
            return self.error(error, request_id)
        except Exception:
            print('MCP adapter internal error; tool outcome is unknown.', file=sys.stderr)
            return self.error(RpcError(-32603, 'Internal adapter error; execution acceptance is unknown'), request_id)


def serve(server, source, sink):
    """Read continuously so cancellation can suppress an in-flight response.

    One CLI call at a time protects the single editor. Cancellation does not
    promise rollback: a started native operation may complete and retains its
    result artifacts. No further reply is emitted for a cancelled request.
    """
    lock = threading.Lock()
    active = {}
    closed = False
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='digico-cli')
    def emit(value, completed_key=None):
        if value is None:
            return
        payload = (json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
        with lock:
            if completed_key is not None:
                state = active.pop(completed_key, None)
                if state is None or state['cancelled']:
                    return
            if not closed:
                sink.write(payload)
                sink.flush()
    def run_call(message, key):
        with lock:
            state = active.get(key)
            if state is None or state['cancelled']:
                return None
        return server.handle(message)
    def finish(future, key):
        try:
            value = future.result()
        except Exception:
            value = Server.error(RpcError(-32603, 'Internal adapter error; execution acceptance is unknown'), key)
        # Cancellation and reply publication use the same lock: no gap between
        # checking cancellation and putting the response on the wire.
        if value is None:
            with lock:
                active.pop(key, None)
        else:
            emit(value, completed_key=key)
    try:
        while True:
            line = source.readline(MAX_MESSAGE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_MESSAGE_BYTES:
                emit(Server.error(RpcError(-32700, 'Message exceeds the local size bound')))
                break
            try:
                message = parse_json(line.decode('utf-8'))
            except (ValueError, UnicodeError):
                emit(Server.error(RpcError(-32700, 'Parse error: expected one UTF-8 JSON object per line')))
                continue
            if isinstance(message, dict) and message.get('jsonrpc') == '2.0' and message.get('method') == 'notifications/cancelled' and 'id' not in message:
                params = message.get('params', {})
                key = params.get('requestId') if isinstance(params, dict) else None
                if type(key) in (int, str):
                    with lock:
                        if key in active:
                            active[key]['cancelled'] = True
                    print('Cancellation received; a started native CLI operation may still complete. Inspect retained artifacts before retrying.', file=sys.stderr)
                continue
            if isinstance(message, dict) and message.get('method') == 'tools/call' and type(message.get('id')) in (int, str):
                key = message['id']
                with lock:
                    busy = bool(active)
                    if not busy:
                        active[key] = {'cancelled': False}
                if busy:
                    emit(Server.error(RpcError(-32600, 'A CLI call is active; wait for its result before another call'), key))
                else:
                    future = pool.submit(run_call, message, key)
                    future.add_done_callback(lambda value, key=key: finish(value, key))
            else:
                emit(server.handle(message))
    finally:
        # A started write cannot be safely rolled back by killing its wrapper.
        # Finish the bounded call, preserve its artifacts, and suppress replies on EOF.
        with lock:
            closed = True
            for state in active.values():
                state['cancelled'] = True
        pool.shutdown(wait=True, cancel_futures=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vm', required=True, help='Existing local Parallels VM name, or Windows-local CLI target label')
    parser.add_argument('--exe', required=True, help='Existing exact Quantum1 V2126 Windows executable path')
    parser.add_argument('--work-dir', type=Path, default=Path.home() / 'Documents' / 'DiGiCoWork')
    parser.add_argument('--process-id', type=int)
    parser.add_argument('--timeout-seconds', type=int, default=180)
    args = parser.parse_args(argv)
    try:
        adapter = Adapter(vm=args.vm, executable=args.exe, work_dir=args.work_dir,
                          process_id=args.process_id, timeout=args.timeout_seconds)
    except (ToolError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    serve(Server(adapter), sys.stdin.buffer, sys.stdout.buffer)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

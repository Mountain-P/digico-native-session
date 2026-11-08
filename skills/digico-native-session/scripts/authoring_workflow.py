"""Callback-driven staged authoring with strict inventory/result boundaries.

No VM, network, target lock, filesystem, retries, or sleeps are implemented.
The caller holds one target lock and supplies synchronous callbacks:

    execute_raw(strict_raw_request, current_inventory_result, stage_index)
    refresh_inventory(sorted_native_channel_ids, stage_index)

refresh_inventory returns one successful native inventory result or a nonempty
list of them. Every stage after the first refreshes and recompiles pinned IDs.
A final mode-only stage also refreshes once (index == number of stages), without
replaying the completed request. Errors retain reviewable partial receipts.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import struct

from authoring import AuthoringError, compile_authoring_request
from control_catalog import CatalogError, normalize_inventory

MAX_SOURCES = 1000
MAX_ROWS = 200000
_CORE_COLLECTIONS = {'controllers': 'raw_id_hex', 'modules': 'raw_id_hex',
                     'definitions': 'pointer', 'channels': 'id', 'groups': 'id'}
_COUNTS = {'controllers': 'controller_count', 'modules': 'module_count',
           'channels': 'channel_count', 'groups': 'group_count',
           'unknown_nodes': 'unknown_node_count', 'edges': 'edge_count'}
_SLICE_METADATA = {'selection', 'performance', 'inventory_detail',
                   'response_tables_requested', 'response_tables_included',
                   'value_views_included', 'edges_included',
                   'physical_source_only_channel_count', 'group_output_only_channel_count'}


class WorkflowError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__('%s: %s' % (code, message))
        self.code, self.message, self.details = code, message, details

    def as_dict(self):
        return {'code': self.code, 'message': self.message, 'details': self.details}


def _fail(code, message, **details):
    raise WorkflowError(code, message, **details)


def _integer(value, label, low=0, high=0xFFFFFFFF):
    if type(value) is not int or not low <= value <= high:
        _fail('invalid_identity', '%s must be an integer in [%d, %d]' % (label, low, high))
    return value


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError) as error:
        _fail('invalid_json', 'documents must contain finite JSON values', reason=str(error))


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _same_claim(values, label, *, required=True):
    values = [value for value in values if value is not None]
    if not values:
        if required:
            _fail('missing_identity', 'missing native target/state identity', field=label)
        return None
    if any(type(value) is not type(values[0]) or value != values[0] for value in values[1:]):
        _fail('conflicting_identity', 'identity aliases disagree', field=label)
    return values[0]


def _state(state, label):
    if not isinstance(state, dict):
        _fail('missing_identity', '%s must contain a native readback state' % label)
    count = _integer(state.get('snapshot_count'), label + '.snapshot_count', high=2000)
    fps = _integer(state.get('fps'), label + '.fps', low=1, high=255)
    path = state.get('current_session_path')
    if not isinstance(path, str) or not path or '\0' in path:
        _fail('missing_identity', '%s.current_session_path must be a nonempty native path' % label)
    return {'snapshot_count': count, 'fps': fps, 'current_session_path': path}


def _inventory_identity(document):
    if (not isinstance(document, dict) or type(document.get('schema_version')) is not int
            or document['schema_version'] != 1 or document.get('status') != 'ok'):
        _fail('invalid_inventory_result', 'requires a successful schema1 native inventory result wrapper')
    if document.get('error') not in (None, '') or document.get('indeterminate_operation') is not None:
        _fail('invalid_inventory_result', 'inventory result carries an error or indeterminate operation')
    inventory = document.get('inventory')
    if not isinstance(inventory, dict) or type(inventory.get('schema_version')) is not int or inventory['schema_version'] != 1:
        _fail('invalid_inventory_result', 'result.inventory must be a schema1 object')
    if inventory.get('structure_verified') is not True or inventory.get('memory_read_only') is not True:
        _fail('invalid_inventory_result', 'inventory requires verified structure and read-only collection')
    profile = _same_claim([document.get('build_profile'), inventory.get('build_profile'), inventory.get('profile')], 'build_profile')
    if not isinstance(profile, str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,63}', profile):
        _fail('invalid_identity', 'build_profile must identify the exact native build')
    digest = _same_claim([document.get('executable_sha256'), inventory.get('executable_sha256')], 'executable_sha256')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        _fail('invalid_identity', 'executable_sha256 must be canonical lower-case SHA-256')
    before, after = _state(document.get('before'), 'before'), _state(document.get('after'), 'after')
    if before != after:
        _fail('unstable_inventory', 'native Session path/fps/count changed during inventory')
    if ('snapshots' in document['before'] and 'snapshots' in document['after']
            and document['before']['snapshots'] != document['after']['snapshots']):
        _fail('unstable_inventory', 'Snapshot invariants changed during inventory')
    for name, native_name in (('session_path', 'current_session_path'), ('fps', 'fps'), ('snapshot_count', 'snapshot_count')):
        if name in inventory and inventory[name] != after[native_name]:
            _fail('conflicting_identity', 'inventory and wrapper state disagree', field=name)
    identity = {'build_profile': profile, 'executable_sha256': digest,
                'process_id': _integer(_same_claim([document.get('process_id'), inventory.get('process_id')], 'process_id'), 'process_id', low=1),
                'image_base': _integer(_same_claim([document.get('image_base'), inventory.get('image_base')], 'image_base'), 'image_base', low=1),
                'console_pointer': _integer(_same_claim([document.get('console_pointer'), inventory.get('console_pointer')], 'console_pointer'), 'console_pointer', low=1),
                **after}
    if 'snapshots' in document['before'] or 'snapshots' in document['after']:
        if not isinstance(document['before'].get('snapshots'), list) or not isinstance(document['after'].get('snapshots'), list):
            _fail('unstable_inventory', 'Snapshot invariant views must exist on both read boundaries')
        identity['snapshot_invariants_sha256'] = _hash(document['after']['snapshots'])
    return identity


def _compare_identity(actual, expected, label):
    differences = sorted(key for key in expected if actual.get(key) != expected[key] or type(actual.get(key)) is not type(expected[key]))
    if differences:
        _fail('target_changed', label + ' belongs to a different target or Session state', fields=differences)


def _merge_value(left, right, path):
    if isinstance(left, dict) and isinstance(right, dict):
        merged = copy.deepcopy(left)
        for key in sorted(right):
            merged[key] = _merge_value(merged[key], right[key], path + '.' + key) if key in merged else copy.deepcopy(right[key])
        return merged
    if type(left) is not type(right) or left != right:
        _fail('inventory_conflict', 'duplicate native observations disagree', field=path)
    return copy.deepcopy(left)


def _merge_rows(sources, collection, key_name):
    rows, pointers = {}, {}
    for source_index, source in enumerate(sources):
        values = source.get(collection, [])
        if not isinstance(values, list) or len(values) > MAX_ROWS:
            _fail('invalid_inventory_rows', 'inventory collection must be a bounded array', collection=collection)
        local_keys = set()
        for value in values:
            if not isinstance(value, dict) or key_name not in value:
                _fail('invalid_inventory_rows', 'native row is missing its identity', collection=collection, key=key_name)
            key = value[key_name]
            if key_name == 'raw_id_hex':
                if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{10}', key):
                    _fail('invalid_inventory_rows', 'native raw ID must be five canonical hex bytes', collection=collection)
            else:
                _integer(key, collection + '.' + key_name, low=1 if key_name == 'pointer' else 0,
                         high=65535 if key_name == 'id' else 0xFFFFFFFF)
            if key in local_keys:
                _fail('inventory_conflict', 'duplicate identities inside one inventory are not admitted', collection=collection, source_index=source_index)
            local_keys.add(key)
            pointer = value.get('pointer')
            if pointer is not None:
                _integer(pointer, collection + '.pointer', low=1)
                if pointer in pointers and pointers[pointer] != key:
                    _fail('inventory_conflict', 'one native pointer has multiple row identities', collection=collection)
                pointers[pointer] = key
            if key in rows:
                rows[key] = _merge_value(rows[key], value, collection + '[' + str(key) + ']')
            else:
                rows[key] = copy.deepcopy(value)
            if len(rows) > MAX_ROWS:
                _fail('invalid_inventory_rows', 'merged row count exceeds local bound', collection=collection)
    return [rows[key] for key in sorted(rows)]


def _merge_edge_rows(sources, collection):
    rows = {}
    for source in sources:
        values = source.get(collection, [])
        if not isinstance(values, list) or len(values) > MAX_ROWS * 8:
            _fail('invalid_inventory_rows', 'edge/unknown-node collection must be a bounded array', collection=collection)
        for value in values:
            if not isinstance(value, dict):
                _fail('invalid_inventory_rows', 'edge/unknown-node row must be an object', collection=collection)
            if collection == 'edges':
                if any(key not in value for key in ('pointer', 'parent', 'role', 'index')):
                    _fail('invalid_inventory_rows', 'edge row lacks its native relation identity')
                key = _canonical([value['pointer'], value['parent'], value['role'], value['index']])
            else:
                _integer(value.get('pointer'), 'unknown node pointer', low=1)
                key = str(value['pointer']).encode('ascii')
            rows[key] = _merge_value(rows[key], value, collection) if key in rows else copy.deepcopy(value)
            if len(rows) > MAX_ROWS * 8:
                _fail('invalid_inventory_rows', 'merged edge/unknown-node count exceeds local bound', collection=collection)
    return [rows[key] for key in sorted(rows)]


def merge_inventory_results(results):
    """Pure union of filtered results from exactly one stable native target.

    Required wrappers supply process_id, before/after Session state, and a
    structure-verified inventory with build/hash/image_base/console_pointer.
    Same-ID conflicts and same-pointer/different-ID aliases reject the merge.
    Missing duplicate fields may be enriched (not overwritten), retaining seed
    points/LUTs. Sequential values are explicitly non-atomic. Source canonical
    result/inventory hashes and slice metadata remain reviewable provenance.
    """
    if not isinstance(results, list) or not 1 <= len(results) <= MAX_SOURCES:
        _fail('invalid_inventory_results', 'results must contain 1..1000 successful inventory wrappers')
    result_hashes = [_hash(document) for document in results]
    identities = [_inventory_identity(document) for document in results]
    anchor = identities[0]
    for identity in identities[1:]:
        _compare_identity(identity, anchor, 'inventory result')
        _compare_identity(anchor, identity, 'inventory result')
    sources = [document['inventory'] for document in results]
    inventory_hashes = [_hash(source) for source in sources]
    for source in sources:
        for name in ('controllers', 'modules', 'definitions', 'channels'):
            if name not in source:
                _fail('invalid_inventory_rows', 'inventory is missing a required native collection', collection=name)
    merged = {'schema_version': 1, 'profile': anchor['build_profile'],
              'build_profile': anchor['build_profile'], 'executable_sha256': anchor['executable_sha256'],
              'process_id': anchor['process_id'], 'image_base': anchor['image_base'],
              'console_pointer': anchor['console_pointer'], 'structure_verified': True,
              'memory_read_only': True, 'values_atomic': False}
    for collection, key in _CORE_COLLECTIONS.items():
        merged[collection] = _merge_rows(sources, collection, key)
    object_pointers = {}
    for collection in ('controllers', 'modules', 'channels', 'groups'):
        for row in merged[collection]:
            pointer = row.get('pointer')
            if pointer is not None and pointer in object_pointers:
                _fail('inventory_conflict', 'one native object pointer appears in different collection types',
                      collections=sorted([collection, object_pointers[pointer]]))
            if pointer is not None:
                object_pointers[pointer] = collection
    for collection in ('edges', 'unknown_nodes'):
        merged[collection] = _merge_edge_rows(sources, collection)
    ignored = (set(_CORE_COLLECTIONS) | set(_COUNTS.values()) | _SLICE_METADATA |
               set(merged) | {'edges', 'unknown_nodes', 'merge_provenance'})
    for source in sources:
        for key in sorted(source.keys() - ignored):
            merged[key] = _merge_value(merged[key], source[key], 'inventory.' + key) if key in merged else copy.deepcopy(source[key])
    for collection, count in _COUNTS.items():
        merged[count] = len(merged[collection])
    merged['inventory_detail'] = 'merged_filtered'
    merged['selection'] = {'full_tree': False, 'merged_filtered': True,
                           'channel_ids': sorted(row['id'] for row in merged['channels'])}
    for field in ('response_tables_requested', 'response_tables_included', 'value_views_included', 'edges_included'):
        merged[field] = all(source.get(field) is True for source in sources)
    provenance = [{'source_index': index, 'result_sha256': result_hashes[index],
                   'inventory_sha256': inventory_hashes[index],
                   'request_sha256': document.get('request_sha256'),
                   'slice_metadata': {key: copy.deepcopy(document['inventory'][key])
                                      for key in sorted(_SLICE_METADATA) if key in document['inventory']}}
                  for index, document in enumerate(results)]
    for entry, document in zip(provenance, results):
        if 'merge_provenance' in document:
            entry['upstream_merge_provenance'] = copy.deepcopy(document['merge_provenance'])
    merged['merge_provenance'] = {'source_count': len(results), 'sources': provenance,
                                'values_atomic': False, 'freshness_source': 'caller_supplied_results'}
    state = {key: anchor[key] for key in ('snapshot_count', 'fps', 'current_session_path')}
    if 'snapshot_invariants_sha256' in anchor:
        state['snapshots'] = copy.deepcopy(results[0]['after']['snapshots'])
    return {'schema_version': 1, 'status': 'ok', 'build_profile': anchor['build_profile'],
            'executable_sha256': anchor['executable_sha256'], 'process_id': anchor['process_id'],
            'image_base': anchor['image_base'], 'console_pointer': anchor['console_pointer'],
            'memory_read_only': True, 'values_atomic': False, 'error': None,
            'before': dict(state), 'after': dict(state), 'inventory': merged,
            'merge_provenance': copy.deepcopy(merged['merge_provenance'])}


def _channels(stage):
    return sorted({operation['channel']['id'] for operation in stage['authoring_request']['operations']})


def _assert_expected(raw_request, inventory):
    try:
        catalog = normalize_inventory(inventory['inventory'])
    except CatalogError as error:
        _fail('invalid_inventory_result', str(error))
    controls = {item['id']: item for item in catalog['controls']}
    simulated = {key: item['current_raw_hex'] for key, item in controls.items()}
    for index, operation in enumerate(raw_request['operations']):
        if operation['op'] != 'set_control':
            continue
        item = controls[operation['id']]
        form = '<i' if item['raw_type'] == 2 else '<f'
        expected = struct.pack(form, operation['expected']).hex()
        if expected != simulated[operation['id']]:
            _fail('stale_expected', 'explicit/simulated expected value differs from the current bound native value',
                  operation_index=index, id=operation['id'])
        simulated[operation['id']] = struct.pack(form, operation['value']).hex()


def _step_value_matches(operation, step, encoding):
    if operation['op'] == 'set_channel_name':
        return step.get('name_hex') == operation['name'].encode(encoding, errors='strict').hex()
    actual, intended = step.get('value'), operation['value']
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return False
    if type(intended) is int:
        return actual == intended
    try:
        return struct.pack('<f', actual) == struct.pack('<f', intended)
    except (ValueError, OverflowError, struct.error):
        return False


def _verified_prefix(receipt, operations, encoding='ascii'):
    verified = receipt.get('verified_steps') if isinstance(receipt, dict) else None
    if not isinstance(verified, list):
        return 0
    count = 0
    for operation, step in zip(operations, verified):
        if not isinstance(step, dict) or step.get('verified') is not True or step.get('op') != operation['op']:
            break
        key = 'id' if operation['op'] == 'set_control' else 'channel_id'
        if step.get(key) != operation[key] or not _step_value_matches(operation, step, encoding):
            break
        count += 1
    return count


def _validate_execution(receipt, raw_request, anchor):
    if (not isinstance(receipt, dict) or receipt.get('status') != 'ok'
            or type(receipt.get('schema_version')) is not int or receipt['schema_version'] != 1):
        _fail('execution_not_ok', 'execution callback did not return a successful native result')
    if receipt.get('error') not in (None, '') or receipt.get('indeterminate_operation') is not None:
        _fail('execution_indeterminate', 'execution callback carries an error or indeterminate operation')
    actual = {key: receipt.get(key) for key in ('process_id', 'executable_sha256')}
    _compare_identity(actual, {key: anchor[key] for key in actual}, 'execution receipt')
    for key in ('build_profile', 'image_base', 'console_pointer'):
        if key in receipt:
            _compare_identity({key: receipt[key]}, {key: anchor[key]}, 'execution receipt')
    for label in ('before', 'after'):
        state = _state(receipt.get(label), 'execution.' + label)
        _compare_identity(state, {key: anchor[key] for key in state}, 'execution ' + label)
        if 'snapshot_invariants_sha256' in anchor:
            if not isinstance(receipt[label].get('snapshots'), list) or _hash(receipt[label]['snapshots']) != anchor['snapshot_invariants_sha256']:
                _fail('target_changed', 'Snapshot invariants changed in execution receipt', boundary=label)
    operations = raw_request['operations']
    if (not isinstance(receipt.get('verified_steps'), list)
            or len(receipt['verified_steps']) != len(operations)
            or _verified_prefix(receipt, operations, raw_request['encoding']) != len(operations)):
        _fail('execution_indeterminate', 'native verified_steps does not completely match the submitted raw operations')


def _error(error):
    if isinstance(error, (WorkflowError, AuthoringError)):
        return error.as_dict()
    return {'code': 'callback_exception', 'message': str(error),
            'details': {'exception_type': type(error).__name__}}


def run_authoring(request, inventory, execute_raw, refresh_inventory):
    """Run pinned stages once, stopping on every error/unknown outcome.

    The result includes the initial plan, submitted strict raw requests, exact
    callback results, per-stage refresh/binding receipts and remaining pinned
    authoring requests. They are review material, not a replay authorization.
    Callback exceptions during execute are indeterminate even if no receipt was
    returned. No later stage executes after any failure.
    """
    result = {'schema_version': 1, 'kind': 'authoring_workflow_result',
              'status': 'error', 'completed_stages': 0, 'attempted_stages': 0,
              'verified_operation_count': 0, 'stages': [], 'refreshes': [],
              'auto_replay': False, 'values_atomic': False,
              'verification_source': 'validated_execute_raw_callback_receipts',
              'error': None, 'failure_phase': None, 'next_stage_index': 0,
              'remaining_authoring_requests': []}
    phase, stage_index, stages = 'planning', 0, []
    execute_started, receipt_received = False, False
    try:
        if not callable(execute_raw) or not callable(refresh_inventory):
            _fail('invalid_callback', 'execute_raw and refresh_inventory must be synchronous callables')
        current = copy.deepcopy(inventory)
        anchor = _inventory_identity(current)
        initial = compile_authoring_request(copy.deepcopy(request), current)
        if request['expected_count'] != anchor['snapshot_count'] or request['fps'] != anchor['fps']:
            _fail('stale_expected_state', 'request count/fps differs from the initial native inventory state')
        stages = copy.deepcopy(initial['stages'])
        result.update(initial_plan=initial, target_binding=anchor, total_stages=len(stages))

        def refresh(channel_ids, index):
            entry = {'before_stage_index': index, 'channel_ids': channel_ids,
                     'status': 'pending', 'freshness_source': 'refresh_inventory_callback'}
            result['refreshes'].append(entry)
            incoming = refresh_inventory(list(channel_ids), index)
            documents = incoming if isinstance(incoming, list) else [incoming]
            merged = merge_inventory_results(documents)
            _compare_identity(_inventory_identity(merged), anchor, 'refreshed inventory')
            entry.update(status='ok', inventory_sha256=_hash(merged['inventory']),
                         merge_provenance=copy.deepcopy(merged['merge_provenance']))
            return merged

        for stage_index, stage in enumerate(stages):
            execute_started, receipt_received = False, False
            phase = 'refresh' if stage_index else 'recompile'
            if stage_index:
                current = refresh(_channels(stage), stage_index)
            phase = 'recompile'
            compiled = compile_authoring_request(stage['authoring_request'], current)
            if len(compiled['stages']) != 1 or compiled['stages'][0]['raw_request'] is None:
                _fail('invalid_stage', 'pinned stage did not recompile to exactly one ready raw request')
            raw_request = compiled['stages'][0]['raw_request']
            _assert_expected(raw_request, current)
            entry = {'index': stage_index, 'operation_indices': stage['operation_indices'],
                     'status': 'submitting', 'raw_request': copy.deepcopy(raw_request),
                     'raw_request_sha256': _hash(raw_request),
                     'inventory_binding': copy.deepcopy(compiled['inventory_binding']),
                     'changes': copy.deepcopy(compiled['changes']), 'execution_result': None,
                     'verified_operation_count': 0}
            result['stages'].append(entry)
            phase, execute_started = 'execute', True
            result['attempted_stages'] += 1
            receipt = execute_raw(copy.deepcopy(raw_request), copy.deepcopy(current), stage_index)
            receipt_received = True
            try:
                _canonical(receipt)
            except WorkflowError:
                entry['execution_result'] = {'status': 'invalid_callback_result',
                                             'python_type': type(receipt).__name__}
                _fail('execution_indeterminate', 'execution callback receipt is not finite JSON')
            entry['execution_result'] = copy.deepcopy(receipt)
            entry['verified_operation_count'] = _verified_prefix(receipt, raw_request['operations'], raw_request['encoding'])
            result['verified_operation_count'] += entry['verified_operation_count']
            phase = 'validate_execution'
            _validate_execution(receipt, raw_request, anchor)
            entry['status'] = 'ok'
            result['completed_stages'] += 1
            result['next_stage_index'] = stage_index + 1
        if stages[-1]['requires_fresh_inventory_after']:
            stage_index, phase, execute_started = len(stages), 'final_refresh', False
            current = refresh(_channels(stages[-1]), stage_index)
            result['final_inventory_binding'] = {'inventory_sha256': _hash(current['inventory']),
                                                 **_inventory_identity(current)}
        result.update(status='ok', next_stage_index=None, remaining_authoring_requests=[],
                      callback_receipts_complete=True)
    except Exception as error:
        indeterminate = (execute_started and not receipt_received) or (
            isinstance(error, WorkflowError) and error.code == 'execution_indeterminate') or phase == 'validate_execution' and (
                bool(result['stages']) and isinstance(result['stages'][-1]['execution_result'], dict)
                and result['stages'][-1]['execution_result'].get('status') == 'ok')
        if result['stages'] and result['stages'][-1]['status'] == 'submitting':
            entry = result['stages'][-1]
            raw_receipt = entry['execution_result']
            if not isinstance(raw_receipt, dict) or raw_receipt.get('status') not in ('ok', 'error'):
                indeterminate = True
            if isinstance(raw_receipt, dict) and raw_receipt.get('indeterminate_operation') is not None:
                indeterminate = True
            entry.update(status='indeterminate' if indeterminate else 'error', error=_error(error))
        if result['refreshes'] and result['refreshes'][-1]['status'] == 'pending':
            result['refreshes'][-1].update(status='error', error=_error(error))
        result.update(status='indeterminate' if indeterminate else 'error',
                      error=_error(error), failure_phase=phase, next_stage_index=stage_index,
                      callback_receipts_complete=False,
                      remaining_authoring_requests=[copy.deepcopy(stage['authoring_request']) for stage in stages[stage_index:]])
    return result

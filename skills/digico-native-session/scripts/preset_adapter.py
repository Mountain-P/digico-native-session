"""Pure adapters for version-bound Preset requests; no process or network I/O."""
from __future__ import annotations

from build_profiles import build_value, profile_bound

import math
import re
import struct

from control_catalog import CatalogError, normalize_inventory
from preset_plan import (TARGET_SHA256, PresetPlanError, _step, plan_delete_preset,
                         plan_lock_preset, plan_recall_input_preset,
                         plan_rename_preset, plan_store_input_preset,
                         plan_update_input_preset, scope_bytes)

PRESET_OPERATIONS = frozenset(('preset_capture', 'preset_rename', 'preset_update',
                               'preset_recall', 'preset_set_scope',
                               'preset_lock', 'preset_delete'))
META_FIELDS = ('name_hex', 'group_name_hex', 'notes_hex', 'time_stored_hex',
               'lock_flag', 'fx_resource', 'channel_count', 'mix_preset')


class PresetAdapterError(ValueError):
    pass


def _fields(value, required, optional=(), label='request'):
    if not isinstance(value, dict):
        raise PresetAdapterError(label+' must be an object')
    missing = set(required)-value.keys()
    unknown = value.keys()-set(required)-set(optional)
    if missing or unknown:
        raise PresetAdapterError('%s fields differ; missing=%s unknown=%s' %
                                 (label, sorted(missing), sorted(unknown)))


def _int(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise PresetAdapterError('%s must be integer %d..%d' % (label, low, high))
    return value


def _hex(value, size, label):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-fA-F]{%d}' % (2*size), value):
        raise PresetAdapterError('%s must contain exactly %d raw bytes' % (label, size))
    return value.lower()


def _text_hex(value, encoding, label):
    if not isinstance(value, str) or not value or '\0' in value:
        raise PresetAdapterError(label+' requires nonempty text without NUL')
    try:
        raw = value.encode(encoding, errors='strict')
    except UnicodeError as error:
        raise PresetAdapterError(label+' is not representable without replacement') from error
    if len(raw) > 31:
        raise PresetAdapterError(label+' exceeds31 encoded bytes')
    return raw.hex()


def is_preset_request(request):
    return (isinstance(request, dict) and isinstance(request.get('operations'), list)
            and any(isinstance(op, dict) and op.get('op') in PRESET_OPERATIONS
                    for op in request['operations']))


@profile_bound(feature='presets', documents=('document',), error_type=PresetAdapterError)
def normalize_presets(document):
    if not isinstance(document, dict):
        raise PresetAdapterError('--presets requires a native presets result')
    state = document.get('presets_after')
    if not isinstance(state, dict):
        state = document.get('presets')
    if not isinstance(state, dict):
        state = document
    if document.get('status', 'ok') != 'ok':
        raise PresetAdapterError('presets result did not finish successfully')
    if document.get('executable_sha256', state.get('executable_sha256')) != build_value('executable.sha256'):
        raise PresetAdapterError('presets executable hash differs from exact V2126')
    if type(state.get('schema_version')) is not int or state['schema_version'] != 1 or state.get('subsystem') != 'native-presets-readonly':
        raise PresetAdapterError('unsupported native presets schema')
    count = _int(state.get('preset_count'), 0, 10000, 'preset_count')
    rows = state.get('presets')
    if not isinstance(rows, list) or len(rows) != count:
        raise PresetAdapterError('presets rows/count differ')
    _int(state.get('list_pointer'), 1, 0xFFFFFFFF, 'list_pointer')
    _hex(state.get('metadata_sha256'), 32, 'metadata_sha256')
    _hex(state.get('scope_hex'), 32, 'scope_hex')
    path_hex = state.get('current_session_path_hex')
    if not isinstance(path_hex, str) or not re.fullmatch('(?:[0-9a-fA-F]{2}){1,255}', path_hex):
        raise PresetAdapterError('presets require their current Session path raw bytes')
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or row.get('index') != index:
            raise PresetAdapterError('preset rows must retain exact native index order')
        _int(row.get('pointer'), 1, 0xFFFFFFFF, 'preset pointer')
        for key, maximum in (('name_hex',31), ('group_name_hex',31), ('notes_hex',255)):
            if not isinstance(row.get(key), str) or not re.fullmatch('(?:[0-9a-fA-F]{2}){0,%d}' % maximum, row[key]):
                raise PresetAdapterError('invalid preset '+key)
        _hex(row.get('time_stored_hex'), 8, 'time_stored_hex')
        _int(row.get('lock_flag'), 0, 255, 'lock_flag')
        _int(row.get('fx_resource'), 0, 65535, 'fx_resource')
        _int(row.get('channel_count'), 0, 65535, 'channel_count')
        _int(row.get('mix_preset'), 0, 255, 'mix_preset')
    return state


def _controls(document, ids, channel_id, session_path_hex):
    if not isinstance(ids, list) or not ids or len(ids) > 64:
        raise PresetAdapterError('verify_control_ids requires1..64 explicit native IDs')
    if not isinstance(document, dict):
        raise PresetAdapterError('processing verification requires --inventory')
    source = document.get('inventory', document)
    state = document.get('after', {})
    path = state.get('current_session_path', source.get('session_path'))
    if not isinstance(path, str) or path.encode('ascii', errors='strict').hex() != session_path_hex.lower():
        raise PresetAdapterError('processing inventory belongs to a different Session path')
    catalog = normalize_inventory(source)
    result = []
    seen = set()
    for key in ids:
        key = _hex(key, 5, 'verify_control_id')
        if key in seen:
            raise PresetAdapterError('duplicated verification control')
        seen.add(key)
        raw = bytes.fromhex(key)
        if int.from_bytes(raw[3:5], 'little') != channel_id:
            raise PresetAdapterError('verification control belongs to another channel')
        found = [row for row in catalog['controls'] if row['id'] == key]
        if len(found) != 1 or found[0]['raw_type'] not in (0,1) or raw[0] == 0xFC or raw[1] == 10 or raw[:2] == b'\x18\x09':
            raise PresetAdapterError('verification requires a known numeric non-Cue processing control')
        item = found[0]
        expected = _hex(item['current_raw_hex'], 4, 'current processing value')
        if not math.isfinite(struct.unpack('<f', bytes.fromhex(expected))[0]):
            raise PresetAdapterError('nonfinite processing verification value')
        row = item['source']
        contract = {'control_pointer': _int(row.get('pointer'),1,0xFFFFFFFF,'controller pointer'),
                    'owner_module_pointer': _int(row.get('owner_module_pointer'),1,0xFFFFFFFF,'module pointer'),
                    'module_vmt_rva': _int(item.get('module_vmt_rva'),1,build_value('executable.image_size')-1,'module VMT RVA'),
                    'process_output_rva': _int(item.get('process_output_rva'),1,build_value('executable.image_size')-1,'callback RVA'),
                    'definition_raw_hex': item['definition']['raw_hex'],
                    'raw_type': item['raw_type']}
        result.append({'id': key, 'control_contract': contract,
                       'before_raw_hex': expected, 'expected_raw_hex': expected})
    return result


def _stored_expectations(entry, checks, selected_scope):
    content = entry.get('content')
    if not isinstance(content, dict) or content.get('channel_snapshot_count') != 1 or not isinstance(content.get('channels'), list) or len(content['channels']) != 1:
        raise PresetAdapterError('recall requires --presets content for its selected single input preset')
    channel = content['channels'][0]
    source_id = _int(channel.get('channel_id'), 0x100, 0x1FF, 'stored input channel ID')
    if entry['mix_preset'] or entry['channel_count'] != 1:
        raise PresetAdapterError('recall supports ordinary single input presets only')
    modules = channel.get('modules')
    if not isinstance(modules, list):
        raise PresetAdapterError('stored modules are missing')
    for check in checks:
        raw = bytes.fromhex(check['id'])
        wanted = (raw[:3]+struct.pack('<H', source_id)).hex()
        found = []
        for module in modules:
            type_id = module.get('type_id_raw')
            if type(type_id) is not int or not 0 <= type_id <= 255:
                continue
            if not selected_scope[type_id//8] & (1 << (type_id%8)):
                continue
            for controller in module.get('controllers', []):
                if controller.get('control_id_hex') == wanted:
                    found.append(controller)
        if len(found) != 1:
            raise PresetAdapterError('verification control is absent/ambiguous or outside selected preset scope')
        value = _hex(found[0].get('value_raw_hex'), 4, 'stored controller value')
        if not math.isfinite(struct.unpack('<f', bytes.fromhex(value))[0]):
            raise PresetAdapterError('stored processing value is nonfinite')
        check.update(expected_raw_hex=value, source_control_id_hex=wanted)


@profile_bound(feature='presets', documents=('request', 'presets', 'inventory'), error_type=PresetAdapterError)
def compile_preset_request(request, *, presets, inventory=None):
    """Compile one high-level operation; callers retain explicit --send boundary."""
    try:
        return _compile(request, presets, inventory)
    except (PresetPlanError, CatalogError, UnicodeError) as error:
        raise PresetAdapterError(str(error)) from error


def _compile(request, document, inventory):
    _fields(request, ('schema_version','fps','encoding','expected_count','operations'), ('build_profile','executable_sha256'))
    if type(request['schema_version']) is not int or request['schema_version'] != 1 or type(request['fps']) is not int or request['fps'] != 25:
        raise PresetAdapterError('Preset adapter requires schema1 and25fps')
    if request['encoding'] not in ('ascii','cp950'):
        raise PresetAdapterError('Preset adapter supports raw ASCII/CP950 only')
    _int(request['expected_count'], 0, 2000, 'expected_count')
    if not isinstance(request['operations'], list) or len(request['operations']) != 1:
        raise PresetAdapterError('use one high-level preset operation per request and inspect between requests')
    op = request['operations'][0]
    if not isinstance(op, dict) or op.get('op') not in PRESET_OPERATIONS:
        raise PresetAdapterError('unknown Preset operation')
    state = normalize_presets(document)
    count = state['preset_count'];encoding = request['encoding'];checks = []
    selected = None
    if op['op'] == 'preset_capture':
        _fields(op, ('op','channel_id','group_name'), ('name','force_mono','verify_control_ids'), op['op'])
        group = _text_hex(op['group_name'], encoding, 'group_name')
        if any(row['group_name_hex'].lower() == group for row in state['presets']):
            raise PresetAdapterError('capture requires a new unique group; existing presets remain protected')
        plan = plan_store_input_preset(channel_id=op['channel_id'], preset_count=count,
                                      group_name=op['group_name'], name=op.get('name'),
                                      force_mono=op.get('force_mono',False), encoding=encoding)
    elif op['op'] == 'preset_set_scope':
        _fields(op, ('op',), ('scopes','scope_hex'), op['op'])
        if ('scopes' in op) == ('scope_hex' in op):
            raise PresetAdapterError('set_scope requires exactly one of scopes or copied scope_hex')
        selected_scope = scope_bytes(op['scopes']) if 'scopes' in op else bytes.fromhex(_hex(op['scope_hex'],32,'scope_hex'))
        step = _step('set_preset_recall_scope',2,0xD3,0,struct.pack('<I',32)+selected_scope,
                     ('0x%X' % build_value('addresses.hex_b7605c')),expected_scope_hex=selected_scope.hex())
        plan = {'steps':[step], 'final_expected_preset_count':count}
    else:
        common = ('op','index','group_name')
        additional = {'preset_rename':('name',), 'preset_update':('channel_id',),
                      'preset_recall':('channel_id','scopes','verify_control_ids'),
                      'preset_lock':('locked',), 'preset_delete':()}[op['op']]
        _fields(op, common+additional, ('verify_control_ids',) if op['op']=='preset_update' else (), op['op'])
        index = _int(op['index'],0,count-1,'preset index');selected = state['presets'][index]
        group = _text_hex(op['group_name'],encoding,'group_name')
        if group != selected['group_name_hex'].lower():
            raise PresetAdapterError('selected preset group bytes differ from explicit group_name')
        if selected['lock_flag'] == 2 and op['op'] != 'preset_recall':
            raise PresetAdapterError('original factory presets are not editable')
        args = {'preset_index':index,'preset_count':count}
        if op['op'] == 'preset_rename':
            _text_hex(op['name'],encoding,'name')
            plan = plan_rename_preset(**args,name=op['name'],encoding=encoding)
        elif op['op'] == 'preset_lock':
            plan = plan_lock_preset(**args,locked=op['locked'])
        elif op['op'] == 'preset_delete':
            plan = plan_delete_preset(**args,group_name=op['group_name'],lock_flag=selected['lock_flag'],encoding=encoding)
        elif op['op'] == 'preset_update':
            plan = plan_update_input_preset(**args,channel_id=op['channel_id'],lock_flag=selected['lock_flag'])
        else:
            plan = plan_recall_input_preset(**args,channel_id=op['channel_id'],scopes=op['scopes'],prior_scope_hex=state['scope_hex'])
    if 'verify_control_ids' in op:
        checks = _controls(inventory,op['verify_control_ids'],op['channel_id'],state['current_session_path_hex'])
        if op['op'] == 'preset_recall':
            _stored_expectations(selected,checks,scope_bytes(op['scopes']))
        for step in plan['steps']:
            if step['op'] in ('store_input_preset','update_input_preset'):
                step['expected_controller_values'] = [{'control_id_hex':c['id'],'value_raw_hex':c['expected_raw_hex']} for c in checks]
            if step['op'] in ('store_input_preset','update_input_preset','recall_input_preset'):
                step['processing_checks'] = checks
    steps = []
    for source_step in plan['steps']:
        step = dict(source_step)
        step['frames'] = [step.pop('frame_hex')]
        step['expected_count_after'] = request['expected_count']
        steps.append(step)
    return {'schema_version':1,'mode':'preset_execute','fps':25,'encoding':encoding,
            'expected_count':request['expected_count'],'final_expected_count':request['expected_count'],
            'preset_operation':op['op'],'steps':steps,'runtime_acceptance_verified':False,
            'preset_inventory_binding':{'preset_count':count,'list_pointer':state['list_pointer'],
                'metadata_sha256':state['metadata_sha256'].lower(),'scope_hex':state['scope_hex'].lower(),
                'current_session_path_hex':state['current_session_path_hex'].lower(),
                'selected_index':selected['index'] if selected else None,
                'selected_pointer':selected['pointer'] if selected else None},
            'final_expected_preset_count':plan['final_expected_preset_count'],
            'final_expected_scope_hex':steps[-1]['expected_scope_hex'] if op['op']=='preset_set_scope' else state['scope_hex'].lower()}

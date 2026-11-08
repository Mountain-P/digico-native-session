"""Pure semantic authoring and diff preview over a copied native inventory.

This module does not open files, start programs, send packets, or contact a
target. A ready raw request still goes through native_session.compile_request
and the existing execution/readback contract. Later stages deliberately have no
raw request: compile their pinned authoring_request against a fresh inventory.
"""
from __future__ import annotations

import copy
import math
import re

from control_catalog import CatalogError, TARGET_SHA256, normalize_inventory, plan_control_write
from build_profiles import BuildProfileError, select_profile

MAX_OPERATIONS = 1000
ENCODINGS = ('ascii', 'cp950', 'utf-8')
UNITS = ('native', 'Hz', 'dB', 'ms', 's')

# Exact Q1 V2126 controls already recovered and read back. A native definition
# label is not used to infer an identity conversion or an unverified HPF setter.
# Tuple is module, parameter, optional fixed native band (not a UI band number).
_ALIASES = {
    'fader': (12, 0x21, None), 'mute': (12, 0x22, None),
    'pan': (13, 0x23, None), 'trim': (1, 1, None),
    'eq.frequency': (4, 5, None), 'eq.q': (4, 6, None),
    'eq.gain': (4, 7, None), 'eq.in': (4, 2, None),
    'eq.shape': (4, 4, None), 'lpf.frequency': (7, 5, 0),
    'delay.time': (9, 8, None), 'delay.fine': (9, 0x17, None),
    'compressor.attack': (5, 0x11, 0),
    'compressor.release': (5, 0x12, 0),
    'compressor.mode': (5, 0x3A, 0),
    'gate.attack': (5, 0x11, 3), 'gate.release': (5, 0x12, 3),
    'gate.mode': (5, 0x0B, 3),
}
_SPELLINGS = {'digital_trim': 'trim', 'digital.trim': 'trim',
              'eq.freq': 'eq.frequency', 'eq_frequency': 'eq.frequency',
              'eq_q': 'eq.q', 'eq_gain': 'eq.gain', 'eq_in': 'eq.in',
              'eq_shape': 'eq.shape', 'lpf': 'lpf.frequency',
              'delay': 'delay.time', 'delay_time': 'delay.time'}


class AuthoringError(ValueError):
    """A deterministic user-facing diagnostic, without fuzzy name guessing."""

    def __init__(self, code, message, **details):
        super().__init__('%s: %s' % (code, message))
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self):
        return {'code': self.code, 'message': self.message, 'details': self.details}


def _fail(code, message, **details):
    raise AuthoringError(code, message, **details)


def _fields(value, required, optional=(), label='request'):
    if not isinstance(value, dict):
        _fail('invalid_schema', '%s must be an object' % label)
    missing = sorted(set(required) - value.keys())
    unknown = sorted(value.keys() - set(required) - set(optional))
    if missing or unknown:
        _fail('invalid_schema', '%s has missing or unknown fields' % label,
              missing=missing, unknown=unknown)


def _int(value, label, low=0, high=65535):
    if type(value) is not int or not low <= value <= high:
        _fail('invalid_schema', '%s must be an integer in [%d, %d]' % (label, low, high))
    return value


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail('invalid_quantity', '%s must be a finite number' % label)
    try:
        number = float(value)
    except (ValueError, OverflowError):
        _fail('invalid_quantity', '%s must be a finite number' % label)
    if not math.isfinite(number):
        _fail('invalid_quantity', '%s must be a finite number' % label)
    return number


def is_authoring_request(request):
    return isinstance(request, dict) and request.get('kind') == 'authoring'


def _validate_request(request):
    _fields(request, ('schema_version', 'kind', 'fps', 'encoding', 'expected_count', 'operations'))
    if type(request['schema_version']) is not int or request['schema_version'] != 1:
        _fail('invalid_schema', 'schema_version must be integer 1')
    if request['kind'] != 'authoring':
        _fail('invalid_schema', 'kind must be authoring')
    if type(request['fps']) is not int or request['fps'] != 25:
        _fail('invalid_schema', 'fps must be integer 25')
    if request['encoding'] not in ENCODINGS:
        _fail('invalid_schema', 'encoding must be ascii, cp950, or utf-8')
    _int(request['expected_count'], 'expected_count', high=2000)
    operations = request['operations']
    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_OPERATIONS:
        _fail('invalid_schema', 'operations must contain 1..1000 entries')


def _inventory(document):
    if not isinstance(document, dict):
        _fail('invalid_inventory', 'inventory must be an object')
    if 'inventory' in document:
        if 'status' in document and document['status'] != 'ok':
            _fail('invalid_inventory', 'inventory result status must be ok')
        source = document['inventory']
    else:
        source = document
    try:
        catalog = normalize_inventory(source)
    except (CatalogError, TypeError, ValueError) as error:
        _fail('invalid_inventory', str(error))
    channels = source.get('channels')
    if not isinstance(channels, list):
        _fail('invalid_inventory', 'inventory.channels must be an array')
    seen = set()
    for channel in channels:
        if not isinstance(channel, dict):
            _fail('invalid_inventory', 'each channel must be an object')
        channel_id = _int(channel.get('id'), 'inventory channel id')
        if channel_id in seen:
            _fail('invalid_inventory', 'duplicate native channel ID', channel_id=channel_id)
        seen.add(channel_id)
        if 'channel_type' in channel and channel['channel_type'] != channel_id >> 8:
            _fail('invalid_inventory', 'channel_type disagrees with native channel ID', channel_id=channel_id)
    return source, catalog, channels


def _channel_summary(channel):
    return {'id': channel['id'], 'type': channel['id'] >> 8,
            'logical': channel.get('logical_channel'), 'name': channel.get('name'),
            'name_decoded': channel.get('name_decoded', True)}


def _resolve_channel(selector, channels):
    _fields(selector, (), ('id', 'type', 'logical', 'name'), 'channel selector')
    if 'id' in selector:
        if len(selector) != 1:
            _fail('invalid_selector', 'id cannot be combined with type, logical, or name')
        native_id = _int(selector['id'], 'channel.id')
        matches = [channel for channel in channels if channel['id'] == native_id]
    else:
        if ('logical' in selector) == ('name' in selector):
            _fail('invalid_selector', 'select exactly one of logical or name, optionally with type')
        if 'type' in selector:
            _int(selector['type'], 'channel.type', high=255)
        if 'logical' in selector:
            _int(selector['logical'], 'channel.logical')
        elif not isinstance(selector['name'], str) or not selector['name']:
            _fail('invalid_selector', 'channel.name must be a nonempty exact name')
        matches = [channel for channel in channels
                   if ('type' not in selector or channel['id'] >> 8 == selector['type'])
                   and ('logical' not in selector or channel.get('logical_channel') == selector['logical'])
                   and ('name' not in selector or
                        (channel.get('name_decoded') is not False and channel.get('name') == selector['name']))]
    matches.sort(key=lambda channel: channel['id'])
    if not matches:
        _fail('unknown_channel', 'channel selector has no exact match', selector=selector)
    if len(matches) != 1:
        _fail('ambiguous_channel', 'channel selector has multiple exact matches; add type or use id',
              selector=selector, candidates=[_channel_summary(channel) for channel in matches])
    return matches[0]


def _aliases(item, target_hash):
    enabled = set(_ALIASES) if target_hash == TARGET_SHA256 else set()
    if target_hash != TARGET_SHA256:
        try:
            profile = select_profile(executable_sha256=target_hash)
        except BuildProfileError:
            return []
        # New reviewed layouts must opt into each independently proved alias.
        # Only fader is currently recovered across those builds. Declaring a
        # label never grants units or bypasses the separate native write gate.
        declared = profile.get('authoring_aliases', ())
        if profile.get('implementation') == 'quantum-layout-v2' and isinstance(declared, (list, tuple)) and 'fader' in declared:
            enabled.add('fader')
    return sorted(alias for alias, (module, parameter, band) in _ALIASES.items()
                  if alias in enabled and item['module_type'] == module and item['parameter'] == parameter
                  and (band is None or item['band'] == band))


def _resolve_parameter(operation, channel, catalog):
    parameter = operation['parameter']
    if not isinstance(parameter, str) or not parameter:
        _fail('invalid_selector', 'parameter must be a nonempty string')
    if 'band' in operation:
        _int(operation['band'], 'band', high=255)
    candidates = [item for item in catalog['controls']
                  if int.from_bytes(bytes.fromhex(item['id'])[3:5], 'little') == channel['id']
                  and ('band' not in operation or item['band'] == operation['band'])]
    if parameter.startswith('id:'):
        control_id = parameter[3:]
        if not re.fullmatch(r'[0-9a-fA-F]{10}', control_id):
            _fail('invalid_selector', 'id: parameter must contain exactly five hexadecimal bytes')
        matches = [item for item in candidates if item['id'] == control_id.lower()]
    elif parameter.startswith('native:'):
        name = parameter[7:]
        if not name:
            _fail('invalid_selector', 'native: requires an exact copied definition name')
        matches = [item for item in candidates if item['name'] == name]
    else:
        alias = _SPELLINGS.get(parameter.lower(), parameter.lower())
        if alias not in _ALIASES:
            _fail('unknown_parameter', 'unknown or unverified semantic parameter; use native:<exact definition name> or id:<hex>',
                  parameter=parameter, known_aliases=sorted(_ALIASES))
        matches = [item for item in candidates if alias in _aliases(item, catalog['executable_sha256'])]
    matches.sort(key=lambda item: item['id'])
    if not matches:
        _fail('unknown_parameter', 'parameter has no exact match on the selected channel',
              channel_id=channel['id'], parameter=parameter, band=operation.get('band'))
    if len(matches) != 1:
        _fail('ambiguous_parameter', 'parameter has multiple matches; specify native band or exact control ID',
              channel_id=channel['id'], parameter=parameter,
              candidates=[{'id': item['id'], 'name': item['name'], 'band': item['band']} for item in matches])
    return matches[0]


def _quantity(value, item, label):
    _fields(value, ('value', 'unit'), label=label)
    number = _number(value['value'], label + '.value')
    unit = value['unit']
    if unit not in UNITS:
        _fail('invalid_quantity', 'unit must be native, Hz, dB, ms, or s', unit=unit)
    if unit == 'native':
        # Packed int32 controls must keep their strict integer JSON type.
        return value['value']
    conversion = item.get('verified_conversion') or {}
    # Fine delay is samples even when a reused definition carries an S/mS
    # descriptor. No runtime sample-rate conversion is implemented here.
    fine_samples = item['module_type'] == 9 and item['parameter'] == 0x17
    if not fine_samples and conversion.get('native_unit') == 'seconds' and unit in ('s', 'ms'):
        return number if unit == 's' else number * 0.001
    identity = item.get('verified_identity_units') or {}
    if unit in ('Hz', 'dB') and identity.get('native_unit') == unit and identity.get('evidence'):
        return number
    _fail('unit_unverified', 'requested unit has no verified native conversion; use native after checking the catalog',
          id=item['id'], requested_unit=unit, display_units=item.get('display_units'),
          verified_conversion=item.get('verified_conversion'),
          verified_identity_units=item.get('verified_identity_units'))


def _encode_name(name, encoding):
    if not isinstance(name, str) or '\0' in name:
        _fail('invalid_name', 'name must be a string without NUL')
    try:
        raw = name.encode(encoding, errors='strict')
    except UnicodeError:
        _fail('invalid_name', 'name cannot be encoded without replacement', encoding=encoding)
    if not 1 <= len(raw) <= 31:
        _fail('invalid_name', 'name must contain 1..31 encoded bytes; no truncation is performed',
              encoded_length=len(raw), encoding=encoding)
    return raw


def _is_mode(item):
    raw = bytes.fromhex(item['id'])
    return raw[:2] == bytes((4, 4)) or raw[:3] in (bytes((0x3A, 5, 0)), bytes((0x0B, 5, 3)))


def _envelope(request, operations, authoring=False):
    result = {key: request[key] for key in ('schema_version', 'fps', 'encoding', 'expected_count')}
    if authoring:
        result['kind'] = 'authoring'
    result['operations'] = operations
    return result


def authoring_catalog(inventory):
    """Return deterministic selector choices; labels never authorize writes."""
    _, catalog, channels = _inventory(inventory)
    controls = []
    for item in sorted(catalog['controls'], key=lambda item: item['id']):
        controls.append({'id': item['id'], 'channel_id': int.from_bytes(bytes.fromhex(item['id'])[3:5], 'little'),
                         'native_name': item['name'], 'band': item['band'],
                         'aliases': _aliases(item, catalog['executable_sha256']),
                         'current_value': item['current_value'], 'range': item['range'],
                         'allowed_values': item['allowed_values'],
                         'display_units': item['display_units'],
                         'verified_conversion': item.get('verified_conversion'),
                         'verified_identity_units': item.get('verified_identity_units'),
                         'writable_candidate': item['writable_candidate'],
                         'readonly_reason': item['readonly_reason']})
    return {'schema_version': 1, 'inventory_sha256': catalog['inventory_sha256'],
            'channels': [_channel_summary(channel) for channel in sorted(channels, key=lambda channel: channel['id'])],
            'controls': controls, 'runtime_acceptance_verified': False}


def compile_authoring_request(request, inventory):
    """Resolve a declarative request and compile only the initial safe stage.

    Stages preserve input order. A mode/shape operation is isolated. Every later
    stage requires a new inventory and recompilation; its raw_request is None.
    The diff is a comparison with copied/current or simulated preceding values,
    not an atomic observation and not a claim of native runtime acceptance.
    """
    _validate_request(request)
    source, catalog, channels = _inventory(inventory)
    resolved = []
    for index, operation in enumerate(request['operations']):
        if not isinstance(operation, dict) or operation.get('op') not in ('set', 'rename'):
            _fail('invalid_schema', 'authoring operations must use set or rename', operation_index=index)
        if operation['op'] == 'set':
            _fields(operation, ('op', 'channel', 'parameter', 'value'), ('band', 'expected'), 'set')
        else:
            _fields(operation, ('op', 'channel', 'name'), label='rename')
        channel = _resolve_channel(operation['channel'], channels)
        pinned = copy.deepcopy(operation)
        pinned['channel'] = {'id': channel['id']}
        entry = {'index': index, 'channel': channel, 'operation': pinned, 'mode': False}
        if operation['op'] == 'set':
            item = _resolve_parameter(operation, channel, catalog)
            entry.update(item=item, native_value=_quantity(operation['value'], item, 'value'), mode=_is_mode(item))
            if 'expected' in operation:
                entry['expected'] = _quantity(operation['expected'], item, 'expected')
            pinned['parameter'] = 'id:' + item['id']
            pinned.pop('band', None)
        else:
            entry['name_hex'] = _encode_name(operation['name'], request['encoding']).hex()
            names = [item for item in catalog['controls'] if item['parameter'] == 255
                     and item['raw_type'] == 4
                     and int.from_bytes(bytes.fromhex(item['id'])[3:5], 'little') == channel['id']]
            if not names:
                _fail('unknown_parameter', 'selected channel has no native name controller', channel_id=channel['id'])
        resolved.append(entry)

    groups = []
    for entry in resolved:
        if entry['mode']:
            groups.append([entry])
        elif groups and not groups[-1][-1]['mode']:
            groups[-1].append(entry)
        else:
            groups.append([entry])
    stages, changes = [], []
    simulated_values, simulated_names = {}, {}
    for stage_index, group in enumerate(groups):
        ready = stage_index == 0
        raw_operations, stage_changes = [], []
        for entry in group:
            channel, operation = entry['channel'], entry['operation']
            change = {'operation_index': entry['index'], 'stage_index': stage_index,
                      'channel': _channel_summary(channel), 'op': operation['op'],
                      'validation': 'compiled' if ready else 'requires_fresh_inventory',
                      'comparison_basis': 'copied_inventory_and_preceding_intentions'}
            if operation['op'] == 'set':
                item = entry['item']
                before = simulated_values.get(item['id'], item['current_value'])
                expected = entry.get('expected', before)
                change.update(id=item['id'], parameter_name=item['name'],
                              current={'value': before, 'unit': 'native'},
                              intended=copy.deepcopy(operation['value']),
                              intended_native={'value': entry['native_value'], 'unit': 'native'},
                              expected_is_current=(expected == before))
                if ready:
                    if expected is None:
                        _fail('missing_current_value', 'control requires a finite current value for compare-before-write', id=item['id'])
                    try:
                        contract = plan_control_write(catalog, item['id'], entry['native_value'], expected)
                    except CatalogError as error:
                        _fail('native_contract', str(error), id=item['id'], operation_index=entry['index'])
                    raw_operations.append({'op': 'set_control', 'id': item['id'],
                                           'value': contract['value'], 'expected': contract['expected']})
                    change['intended_native']['value'] = contract['value']
                    change['payload_hex'] = contract['payload_hex']
                    change['changed'] = contract['payload_hex'] != contract['expected_payload_hex'] if expected == before else contract['value'] != before
                    simulated_values[item['id']] = contract['value']
                else:
                    change['changed'] = entry['native_value'] != before
                    change['current_may_change_at_barrier'] = True
                    simulated_values[item['id']] = entry['native_value']
            else:
                before_name, before = simulated_names.get(channel['id'], (channel.get('name'), channel.get('name_hex')))
                change.update(current={'name': before_name, 'name_hex': before},
                              intended={'name': operation['name'], 'name_hex': entry['name_hex']},
                              changed=before != entry['name_hex'])
                simulated_names[channel['id']] = (operation['name'], entry['name_hex'])
                if ready:
                    raw_operations.append({'op': 'set_channel_name', 'channel_id': channel['id'], 'name': operation['name']})
            stage_changes.append(change)
            changes.append(change)
        stages.append({'index': stage_index, 'status': 'ready' if ready else 'requires_fresh_inventory',
                       'operation_indices': [entry['index'] for entry in group],
                       'barrier_before': None if ready else 'fresh_inventory_and_recompile',
                       'requires_fresh_inventory_after': group[-1]['mode'] or stage_index < len(groups) - 1,
                       'reason': 'native_mode_or_shape_change' if group[-1]['mode'] else 'ordinary_controls',
                       'authoring_request': _envelope(request, [entry['operation'] for entry in group], True),
                       'raw_request': _envelope(request, raw_operations) if ready else None,
                       'inventory_sha256': catalog['inventory_sha256'] if ready else None,
                       'changes': stage_changes})
    after = inventory.get('after', {})
    session_path = after.get('current_session_path') if isinstance(after, dict) else None
    return {'schema_version': 1, 'kind': 'authoring_plan',
            'inventory_binding': {'inventory_sha256': catalog['inventory_sha256'],
                                  'session_path': session_path or source.get('session_path')},
            'stages': stages, 'changes': changes,
            'fully_compiled': len(stages) == 1, 'values_atomic': False,
            'runtime_acceptance_verified': False}


def preview_authoring_request(request, inventory):
    """Pure current-versus-intended preview; same validation as compilation."""
    return compile_authoring_request(request, inventory)

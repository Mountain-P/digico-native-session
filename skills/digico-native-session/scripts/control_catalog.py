"""Pure, version-bound native controller inventory and payload contracts.

No target process, UI, socket, file I/O, or OSC is used here. Definitions describe
capabilities; they do not establish live mirror acceptance. The execution runner
must independently bind the target, controller, definition, owner VMT/callback,
current value and post-write readback. Cue operations are excluded.
"""
from __future__ import annotations

from build_profiles import build_value, profile_bound, select_profile

import hashlib
import json
import math
import re
import struct

TARGET_SHA256 = build_value('executable.sha256')
SCHEMA_VERSION = 1
MAX_CONTROLLERS = 200000
# Exact native engineering-time descriptor: base S with mS/uS prefixes.
TIME_UNIT_DESCRIPTOR = '15000000000000000000530000006d53000075530000000000000000000000'
CURVE_NAMES = ('cv_Int', 'cv_Lin', 'cv_Log10', 'cv_RevLog10', 'cv_Sqr',
               'cv_RevSqr', 'cv_Log2', 'cv_Custom', 'cv_Sine', 'cv_Spread')
# tController.GetValidValues C7E160, exact V2126 PE. Values are numeric labels;
# display strings are not inferred. EQ shape and Mustard shape have band cases.
_ENUMS = {int(key): tuple(value) for key, value in build_value('control_enums').items()}


class CatalogError(ValueError):
    """The inventory or requested value lacks a supported native contract."""


def _int(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise CatalogError('%s must be an integer in [%s, %s]' % (label, low, high))
    return value


def _hex(value, size, label):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{%d}' % (2 * size), value):
        raise CatalogError('%s must be exactly %d hexadecimal bytes' % (label, size))
    return bytes.fromhex(value)


def _finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise CatalogError('%s must be a finite number, not a boolean' % label)
    try:
        number = float(value)
    except (OverflowError, ValueError) as error:
        raise CatalogError('%s must be finite' % label) from error
    if not math.isfinite(number):
        raise CatalogError('%s must be finite' % label)
    return number


def _float32_claim_matches(claimed, raw):
    """PS Single JSON decimals can round one ULP; copied bytes stay authoritative."""
    number = _finite(claimed, 'response value')
    try:
        encoded = struct.pack('<f', number)
    except (OverflowError, struct.error):
        return False
    if encoded == raw:
        return True
    def ordered(data):
        bits = struct.unpack('<I', data)[0]
        return 0xFFFFFFFF - bits if bits & 0x80000000 else bits + 0x80000000
    return abs(ordered(encoded) - ordered(raw)) <= 1


def _short(data, offset, maximum, label):
    size = data[offset]
    if size > maximum:
        raise CatalogError('%s shortstring length exceeds %d' % (label, maximum))
    return data[offset + 1:offset + 1 + size].decode('latin1')


def parse_definition(raw_hex):
    """Decode the selected build's complete tControllerLibrary record."""
    layout = build_value('layouts.definition')
    raw = _hex(raw_hex, layout['size'], 'definition_raw_hex')
    default = struct.unpack_from('<f', raw, layout['default'])[0]
    data_flags = layout.get('data_flags')
    colour = layout.get('colour')
    return {
        'raw_hex': raw.hex(), 'raw_type': raw[layout['data_type']],
        'default_raw_hex': raw[layout['default']:layout['default']+4].hex(),
        'default_f32': default if math.isfinite(default) else None,
        'band': raw[layout['band']], 'parameter': raw[layout['parameter']],
        'name': _short(raw, layout['name'], layout['name_maximum'], 'definition name'),
        'units': _short(raw, layout['units'], layout['units_maximum'], 'definition units'),
        'auto_flags': raw[layout['auto_flags']], 'snapshot_type': raw[layout['snapshot_type']],
        'gang_flag': raw[layout['gang_flag']], 'macro_flag': raw[layout['macro_flag']],
        'colour': struct.unpack_from('<i', raw, colour)[0] if colour is not None else None,
        'response_seed_pointer': struct.unpack_from('<I', raw, layout['seed'])[0],
        'response_lut_pointer': struct.unpack_from('<I', raw, layout['lut'])[0],
        'data_flags': raw[data_flags] if data_flags is not None else None,
    }


def parse_response_seed(raw_hex, points):
    """Decode a copied seed plus copied points, never dereferencing its pointer.

    UnitDesc is retained as raw bytes: it contains formatter control characters
    for some curves. Bounds are extrema of finite response points, not assumed
    min/max from the first/last point. This does not implement interpolation.
    """
    raw = _hex(raw_hex, 38, 'response_seed_raw_hex')
    count = struct.unpack_from('<H', raw, 32)[0]
    point_pointer = struct.unpack_from('<I', raw, 34)[0]
    if point_pointer == 0:
        raise CatalogError('response seed point pointer must be nonzero')
    if not 1 <= count <= 4096 or not isinstance(points, list) or len(points) != count:
        raise CatalogError('response seed requires exactly its 1..4096 copied points')
    normalized = []
    previous = -1
    for point in points:
        if not isinstance(point, dict):
            raise CatalogError('response point must be an object')
        if 'raw_hex' in point:
            data = _hex(point['raw_hex'], 8, 'response point raw_hex')
            position, value = struct.unpack('<if', data)
            if 'position' in point and point['position'] != position:
                raise CatalogError('response point position disagrees with raw bytes')
            if 'value' in point:
                claimed = point['value']
                if isinstance(claimed, dict):
                    claimed_raw = _hex(claimed.get('raw_hex'), 4, 'response value raw_hex')
                    if claimed_raw != data[4:]:
                        raise CatalogError('response value bytes disagree with point bytes')
                    claimed = claimed.get('float32')
                if not _float32_claim_matches(claimed, data[4:]):
                    raise CatalogError('response point value disagrees with raw bytes')
        else:
            position = point.get('position')
            value = _finite(point.get('value'), 'response point value')
        position = _int(position, 'response position', 0, 255)
        value = _finite(value, 'response value')
        if position <= previous:
            raise CatalogError('response positions must be strictly increasing')
        previous = position
        normalized.append({'position': position, 'value': value})
    values = [point['value'] for point in normalized]
    curve = raw[0]
    return {'raw_hex': raw.hex(), 'curve_type': curve,
            'curve_name': CURVE_NAMES[curve] if curve < len(CURVE_NAMES) else None,
            'unit_desc_hex': raw[1:32].hex(), 'point_count': count,
            'points_pointer': point_pointer, 'points': normalized,
            'minimum': min(values), 'maximum': max(values),
            'bounds_source': 'native_response_point_extrema'}


def parse_response_lut(raw_hex):
    """Decode the generated native 256-float response table without I/O."""
    raw = _hex(raw_hex, 1024, 'response_lut_raw_hex')
    values = list(struct.unpack('<256f', raw))
    if not all(math.isfinite(value) for value in values):
        raise CatalogError('native response LUT contains nonfinite values')
    return {'raw_hex': raw.hex(), 'values': values,
            'minimum': min(values), 'maximum': max(values),
            'bounds_source': 'native_response_lut_extrema'}


def discrete_values(raw_id_hex, console_type=None):
    """Return native numeric enums, or None when console/band cases are unknown."""
    raw = _hex(raw_id_hex, 5, 'raw_id_hex')
    idlo = int.from_bytes(raw[:2], 'little')
    if build_value('implementation') == 'quantum-layout-v2':
        explicit = build_value('control_enums').get(str(idlo))
        return list(explicit) if explicit is not None else None
    if idlo == 0x0404:
        band = raw[2]
        if band == 0:
            return [1, 4]
        if band == 3:
            return [1, 3]
        if band not in (1, 2, 4, 5, 6, 7) or console_type is None:
            return None
        console_type = _int(console_type, 'console_type', 0, 255)
        extended = console_type > 14 and console_type not in (19, 20, 21, 24)
        if band == 1:
            return [1] if extended else [1, 4]
        if band == 2:
            return [1] if extended else [1, 3]
        if console_type not in (14, 17, 19, 20, 21, 24):
            return [1]
        if band in (4, 5):
            return [0, 1]
        if band in (6, 7):
            return [1, 2]
    if idlo == 0x1C04:
        return {0: [1, 4], 1: [1, 9], 2: [1, 9], 3: [1, 3]}.get(raw[2])
    return list(build_value('control_enums').get(str(idlo), (0, 1)))


def _f32(bits):
    return struct.unpack('<f', struct.pack('<I', bits))[0]


def semantic_limits(profile, parameter, mode=None):
    """Exact per-build RangeCheck constants, never display/curve guesses."""
    if type(parameter) is not int:
        raise CatalogError('native semantic parameter must be an integer')
    try:
        modes = build_value('semantic_limits')[profile][str(parameter)]
        key = 'default' if 'default' in modes else str(mode)
        if key != 'default' and type(mode) is not int:
            raise KeyError(key)
        low, high = modes[key]
    except (KeyError, TypeError) as error:
        raise CatalogError('unsupported native semantic profile/parameter/mode') from error
    return {'minimum': _f32(low), 'maximum': _f32(high),
            'minimum_raw_hex': struct.pack('<I', low).hex(),
            'maximum_raw_hex': struct.pack('<I', high).hex(),
            'source': 'native_callback_context'}


def _verified_identity_units(raw, row, definition, response):
    """Attach engineering identity only for exact seed/code/runtime witnesses."""
    contract = build_value('unit_contracts').get(raw[:2].hex())
    if contract is None or response is None:
        return None
    if (definition['raw_type'] != contract['raw_type']
            or row.get('module_vmt_rva') != contract['module_vmt_rva']
            or row.get('process_output_rva') != contract['process_output_rva']
            or response['curve_type'] != contract['curve_type']
            or response['unit_desc_hex'] != contract['unit_descriptor_raw_hex']
            or response['minimum'] != contract['minimum']
            or response['maximum'] != contract['maximum']):
        return None
    return {'native_unit': contract['native_unit'], 'identity': True,
            'evidence': list(contract['evidence']), 'physical_audio_verified': False}


@profile_bound(feature='control_write', error_type=CatalogError)
def assert_control_write_allowed(control_id, *, raw_type, definition_rva,
                                 module_vmt_rva, process_output_rva):
    """Authorize an exact per-build control family; does not access a target."""
    raw = _hex(control_id, 5, 'control_id')
    raw_type = _int(raw_type, 'raw_type', 0, 4)
    profile = select_profile()
    rules = profile.get('control_write_allowlist')
    if rules is None and profile['implementation'] == 'quantum1-v2126-layout-v1':
        return {'mode':'legacy_profile_domain'}
    for name,value in (('definition_rva',definition_rva),('module_vmt_rva',module_vmt_rva),
                       ('process_output_rva',process_output_rva)):
        _int(value,name,1,profile['executable']['image_size']-1)
    wanted = {'id_prefix_hex':raw[:3].hex(), 'raw_type':raw_type,
              'definition_rva':definition_rva, 'module_vmt_rva':module_vmt_rva,
              'process_output_rva':process_output_rva}
    matches = [rule for rule in (rules or ()) if all(rule.get(key) == value for key,value in wanted.items())]
    if len(matches) != 1:
        raise CatalogError('control is not admitted by the selected build control_write_allowlist')
    return {'mode':'exact_profile_allowlist', **wanted}


def _response_pointer_binding(row, definition):
    """v2 only: admit the definition domain when fresh current pointers agree."""
    if build_value('implementation') != 'quantum-layout-v2':
        return None, None
    if row.get('response_pointer_binding_verified') is not True:
        return None, 'current response pointer binding was not copied and verified'
    pointers = {}
    for name in ('current_response_seed_pointer','current_response_lut_pointer'):
        value = row.get(name)
        if type(value) is not int or not 0 <= value <= 0xffffffff:
            return None, 'current response pointer binding was not copied and verified'
        pointers[name] = value
    seed = definition['response_seed_pointer']; lut = definition['response_lut_pointer']
    if ((pointers['current_response_seed_pointer'] not in (0,seed))
            or (pointers['current_response_lut_pointer'] not in (0,lut))):
        return None, 'runtime response override differs from definition; override domain is not admitted'
    return {**pointers,'definition_response_seed_pointer':seed,'definition_response_lut_pointer':lut,
            'effective_response_seed_pointer':seed,'effective_response_lut_pointer':lut,
            'overrides_admitted':False}, None


def _semantic_profile(raw):
    def copied(name):
        value = build_value('contexts').get(name)
        return {'unavailable': name} if value is None else dict(value)
    if raw[1] == 4 and raw[0] == 6 and raw[2] <= 7:
        result = copied('eq_q')
        if 'unavailable' in result:
            return result
        result['mode_band'] = raw[2]
        result['mode_slot'] = result['mode_slot_base'] + result['mode_slot_stride'] * raw[2]
        return result
    if raw[1] == 5 and raw[0] in (0x11, 0x12):
        if raw[2] in (0, 1, 2):
            return copied('compressor')
        if raw[2] == 3:
            return copied('gate')
    if raw[1] == 9 and raw[0] == 8:
        return copied('delay')
    return None


def _control_context(row, raw, rows):
    profile = _semantic_profile(raw)
    if profile is None:
        return None, None, None
    if 'unavailable' in profile:
        return None, None, 'native semantic callback context is not admitted for this build'
    if row.get('module_vmt_rva') != profile['vmt'] or row.get('process_output_rva') != profile['callback']:
        return None, None, 'known semantic control has an unrecognized native callback binding'
    owner = row.get('owner_module_pointer')
    if owner is None:
        return None, None, 'native semantic owner pointer is unavailable'
    owner = _int(owner, 'owner_module_pointer', 1, 0xFFFFFFFF)
    if profile['profile'] == build_value('semantic_profiles.delay_v2126'):
        limits = semantic_limits(profile['profile'], raw[0])
        return {'profile': profile['profile'], 'owner_module_pointer': owner,
                'limits': limits}, limits, None
    copied = row.get('owner_native_context', row.get('native_context'))
    if copied is None:
        return None, None, 'native mode-dependent context is required for this control'
    if not isinstance(copied, dict) or copied.get('profile') != profile['profile']:
        raise CatalogError('native owner context profile disagrees with controller')
    if copied.get('owner_module_pointer') != owner:
        raise CatalogError('native context owner pointer disagrees with controller')
    if ('owner_native_context' in row and 'native_context' in row
            and row['owner_native_context'] != row['native_context']):
        raise CatalogError('native context aliases disagree')
    resending = _int(copied.get('resending', 0 if profile['resending_offset'] is None else None), 'context resending', 0, 1)
    mode_pointer = _int(copied.get('mode_controller_pointer'), 'mode controller pointer', 1, 0xFFFFFFFF)
    mode_id = _hex(copied.get('mode_id_hex'), 5, 'context mode_id_hex')
    if mode_id != bytes((profile['mode_parameter'], profile.get('mode_module', 5), profile['mode_band'], raw[3], raw[4])):
        raise CatalogError('native mode controller ID does not match the recovered owner slot')
    mode_raw = _hex(copied.get('mode_output_raw_hex'), 4, 'context mode output')
    mode_value = struct.unpack('<f', mode_raw)[0]
    supported_modes = (0., 1., 2., 3., 4.) if profile['profile'] == build_value('semantic_profiles.eq_q_v2126') else (0., 1., 2.)
    if not math.isfinite(mode_value) or mode_value not in supported_modes:
        raise CatalogError('native mode output is not a supported discrete value')
    matches = [item for item in rows if item.get('pointer') == mode_pointer]
    if len(matches) != 1:
        return None, None, 'native mode controller is absent or ambiguous in copied inventory'
    mode_row = matches[0]
    if (_hex(mode_row.get('raw_id_hex'), 5, 'mode row ID') != mode_id
            or mode_row.get('owner_module_pointer') != owner
            or _hex(mode_row.get('output_value_raw_hex'), 4, 'mode row output') != mode_raw):
        raise CatalogError('native mode context and copied controller disagree')
    if mode_row.get('vmt_rva') != build_value('addresses.hex_8680fc') or mode_row.get('definition_pointer') is None:
        return None, None, 'native mode controller VMT/definition binding is unavailable'
    mode_definition = parse_definition(mode_row.get('definition_raw_hex'))
    if (mode_definition['raw_type'] != 0 or mode_definition['parameter'] != profile['mode_parameter']
            or mode_definition['band'] != profile['mode_band']):
        raise CatalogError('native mode definition does not match its typed discriminator')
    context = {'profile': profile['profile'], 'owner_module_pointer': owner,
               'mode_controller_pointer': mode_pointer, 'mode_id_hex': mode_id.hex(),
               'mode_output_raw_hex': mode_raw.hex(), 'mode_value': int(mode_value),
               'mode_controller_slot': profile['mode_slot'], 'resending_offset': profile['resending_offset'],
               'resending': resending, 'mode_vmt_rva': build_value('addresses.hex_8680fc'),
               'mode_definition_pointer': _int(mode_row['definition_pointer'], 'mode definition pointer', 1, 0xFFFFFFFF),
               'mode_definition_raw_hex': mode_definition['raw_hex']}
    if profile['profile'] == build_value('semantic_profiles.gate_v2126'):
        timer = _int(copied.get('mode_changed_timer_count'), 'gate mode-changed timer', 0, 255)
        context['mode_changed_timer_count'] = timer
        context['timer_offset'] = profile['timer_offset']
        if timer != 0:
            return context, None, 'native gate mode-change timer is active; time writes would be ignored'
    if resending:
        return context, None, 'native owner is resending; semantic limit branch is not stable'
    limits = semantic_limits(profile['profile'], raw[0], int(mode_value))
    context['limits'] = limits
    return context, limits, None


def _rows(inventory):
    if 'controllers' in inventory:
        rows = inventory['controllers']
        if not isinstance(rows, list):
            raise CatalogError('inventory.controllers must be an array')
        return rows
    rows = []
    def walk(obj):
        if isinstance(obj, dict):
            if 'raw_id_hex' in obj and 'definition_raw_hex' in obj:
                rows.append(obj)
            else:
                for key in ('groups', 'channels', 'modules', 'controllers'):
                    if key in obj:
                        walk(obj[key])
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
    walk(inventory)
    return rows


@profile_bound(feature='inventory', documents=('inventory',), error_type=CatalogError)
def normalize_inventory(inventory):
    """Build a typed inventory from copied runtime records, with no target access.

    Required envelope: schema_version=1, executable_sha256 and controllers[] (or
    groups/channels/modules hierarchy). Rows require raw_id_hex and the selected
    definition_raw_hex. VMT/callback RVAs bind writable entries. Type1 also needs
    response_seed_raw_hex and response_points. Packed route type2 needs explicit
    allowed_values (never arbitrary integers). Additional metadata is retained in
    each row's source field and does not itself grant write access.
    """
    if not isinstance(inventory, dict) or type(inventory.get('schema_version')) is not int or inventory.get('schema_version') != 1:
        raise CatalogError('inventory.schema_version must be 1')
    target_hash = inventory.get('executable_sha256')
    if target_hash != build_value('executable.sha256'):
        raise CatalogError('inventory executable_sha256 does not match Quantum1 V2126 profile')
    rows = _rows(inventory)
    if len(rows) > MAX_CONTROLLERS:
        raise CatalogError('inventory controller count exceeds local resource bound')
    console_type = inventory.get('console_type')
    if console_type is None and isinstance(inventory.get('console'), dict):
        console_type = inventory['console'].get('type')
    definitions = {}
    copied_definitions = inventory.get('definitions', [])
    if not isinstance(copied_definitions, list):
        raise CatalogError('inventory.definitions must be an array')
    for copied in copied_definitions:
        if not isinstance(copied, dict) or 'raw_hex' not in copied:
            raise CatalogError('copied definition must contain raw_hex')
        parse_definition(copied['raw_hex'])
        pointer = copied.get('pointer')
        if pointer in definitions:
            raise CatalogError('duplicate copied definition pointer')
        definitions[pointer] = copied
    module_contexts = {}
    for module in inventory.get('modules', []):
        if isinstance(module, dict) and module.get('owner_native_context') is not None:
            module_contexts[module.get('pointer')] = module['owner_native_context']
    controls = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise CatalogError('controller must be an object')
        row = dict(row)
        if row.get('owner_native_context') is None and row.get('owner_module_pointer') in module_contexts:
            row['owner_native_context'] = module_contexts[row['owner_module_pointer']]
        copied = definitions.get(row.get('definition_pointer'))
        if copied is not None:
            if copied['raw_hex'].lower() != str(row.get('definition_raw_hex', '')).lower():
                raise CatalogError('controller and shared definition bytes disagree')
            for field in ('response_seed_raw_hex', 'response_points', 'response_lut_raw_hex'):
                if field in copied:
                    if field in row and row[field] != copied[field]:
                        raise CatalogError('controller and copied response data disagree')
                    row[field] = copied[field]
        raw = _hex(row.get('raw_id_hex'), 5, 'raw_id_hex')
        key = raw.hex()
        if key in seen:
            raise CatalogError('duplicate controller ID: ' + key)
        seen.add(key)
        definition = parse_definition(row.get('definition_raw_hex'))
        if 'data_type' in row and _int(row['data_type'], 'data_type', 0, 255) != definition['raw_type']:
            raise CatalogError('controller datatype disagrees with definition')
        if row.get('module_id_hex') is not None:
            owner_id = _hex(row['module_id_hex'], 5, 'module_id_hex')
            if owner_id[1] != raw[1] or owner_id[3:] != raw[3:]:
                raise CatalogError('controller ID disagrees with owner module identity')
        if raw[0] != definition['parameter']:
            raise CatalogError('controller parameter does not match copied definition: ' + key)
        if (definition['band'] != 0 or raw[1] == 5) and raw[2] != definition['band']:
            raise CatalogError('controller band does not match copied definition: ' + key)
        raw_type = definition['raw_type']
        if row.get('response_seed_raw_hex') is not None and definition['response_seed_pointer'] == 0:
            raise CatalogError('response seed copy supplied for a null definition pointer')
        item = {'id': key, 'parameter': raw[0], 'module_type': raw[1],
                'band': raw[2], 'channel': raw[3], 'channel_type': raw[4],
                'raw_type': raw_type, 'name': definition['name'],
                'units': definition['units'], 'display_units': definition['units'],
                'value_units': 'native_internal', 'verified_conversion': None,
                'verified_identity_units': None,
                'response_pointer_binding': None,
                'definition': definition,
                'payload_type': {0: 'float32_discrete', 1: 'float32', 2: 'int32',
                                 3: 'meter_opaque', 4: 'string'}.get(raw_type, 'unknown'),
                'allowed_values': None, 'range': None, 'response': None, 'response_lut': None,
                'value_domain_source': None, 'semantic_context': None, 'response_range': None,
                'current_raw_hex': None, 'current_value': None,
                'readonly_reason': None, 'source': dict(row)}
        if row.get('response_lut_raw_hex') is not None:
            if definition['response_lut_pointer'] == 0:
                raise CatalogError('response LUT copy supplied for a null definition pointer')
            item['response_lut'] = parse_response_lut(row['response_lut_raw_hex'])
        current = row.get('value_raw_hex')
        if current is not None:
            data = _hex(current, 4, 'value_raw_hex')
            item['current_raw_hex'] = data.hex()
            if raw_type in (0, 1):
                value = struct.unpack('<f', data)[0]
                item['current_value'] = value if math.isfinite(value) else None
            elif raw_type == 2:
                item['current_value'] = struct.unpack('<i', data)[0]
        if raw_type == 0:
            seed = row.get('response_seed_raw_hex')
            if seed is not None:
                response = parse_response_seed(seed, row.get('response_points'))
                item['response'] = response
                low, high = response['minimum'], response['maximum']
                if (response['curve_type'] != 0 or not low.is_integer()
                        or not high.is_integer() or high - low > 65535):
                    item['readonly_reason'] = 'discrete response requires a bounded cv_Int domain'
                else:
                    item['allowed_values'] = list(range(int(low), int(high) + 1))
                    item['range'] = {'minimum': low, 'maximum': high,
                                     'source': 'native_integer_response'}
                    item['value_domain_source'] = 'native_integer_response'
            elif definition['response_seed_pointer'] != 0:
                item['readonly_reason'] = 'discrete response seed was not copied'
            else:
                item['allowed_values'] = discrete_values(key, console_type)
                item['value_domain_source'] = ('profile_restricted_enum' if build_value('implementation') == 'quantum-layout-v2'
                                               else 'native_getvalidvalues')
                if item['allowed_values'] is None:
                    item['readonly_reason'] = ('native enum has no explicit v2 profile restriction'
                        if build_value('implementation') == 'quantum-layout-v2'
                        else 'native enum depends on unavailable console/band profile')
                elif definition['default_f32'] not in item['allowed_values']:
                    item['readonly_reason'] = 'default contradicts the generic native enum; semantic domain is unresolved'
        elif raw_type == 1:
            seed = row.get('response_seed_raw_hex')
            if seed is None:
                item['readonly_reason'] = 'numeric response seed and bounds are unavailable'
            else:
                response = parse_response_seed(seed, row.get('response_points'))
                item['response'] = response
                if response['curve_name'] is None:
                    item['readonly_reason'] = 'unrecognized native response curve'
                else:
                    item['range'] = {'minimum': response['minimum'], 'maximum': response['maximum'],
                                     'source': response['bounds_source']}
                    item['value_domain_source'] = response['bounds_source']
        elif raw_type == 2:
            allowed = row.get('allowed_values')
            if not isinstance(allowed, list) or not 1 <= len(allowed) <= 65536:
                item['readonly_reason'] = 'packed route domain requires an explicit runtime whitelist'
            else:
                item['allowed_values'] = sorted({_int(x, 'route allowed value', -2**31, 2**31-1) for x in allowed})
                item['value_domain_source'] = 'runtime_route_whitelist'
        elif raw_type == 3:
            item['readonly_reason'] = 'native meter; no Category8 write receiver'
        elif raw_type == 4:
            item['readonly_reason'] = 'string controller requires a dedicated semantic operation'
        else:
            item['readonly_reason'] = 'unrecognized native datatype'
        context, limits, context_error = _control_context(row, raw, rows)
        item['semantic_context'] = context
        if limits is not None:
            item['response_range'] = item['range']
            item['range'] = dict(limits)
            item['value_domain_source'] = 'native_callback_context'
        if context_error:
            item['readonly_reason'] = context_error
        pointer_binding,pointer_error = _response_pointer_binding(row,definition)
        item['response_pointer_binding'] = pointer_binding
        if pointer_error and item['readonly_reason'] is None:
            item['readonly_reason'] = pointer_error
        item['verified_identity_units'] = _verified_identity_units(raw, row, definition, item['response'])
        if item['response'] is not None and raw_type == 1:
            if item['response']['unit_desc_hex'] == TIME_UNIT_DESCRIPTOR:
                item['verified_conversion'] = {
                    'native_unit': 'seconds', 'reference_display_unit': 'milliseconds',
                    'native_to_reference_scale': 1000.0, 'reference_to_native_scale': 0.001,
                    'automatic_conversion': False,
                    'evidence': ['exact native S/mS/uS response descriptor',
                                 'C286D4..C286DA: fine samples/sample-rate plus main seconds',
                                 'C7C7FA: native delay display multiplies by 1000']}
            elif raw[1] == 9 and raw[0] == 0x17:
                item['verified_conversion'] = {
                    'native_unit': 'samples', 'reference_display_unit': 'seconds',
                    'requires_runtime_sample_rate': True, 'automatic_conversion': False,
                    'evidence': ['C286D4..C286DA: fine delay divided by runtime sample rate']}
        if raw[1] == 9 and raw[0] == 0x18:
            item['readonly_reason'] = 'derived total delay; native callback computes main seconds plus fine samples/sample-rate'
        if raw[1] == 10:
            item['readonly_reason'] = 'Cue/Automation operations are excluded from this catalog planner'
        if raw[0] == 0xFC:
            # C7B9CD binds FC to channel+1BE; C77BBC derives 0..5 from state.
            # This is a status indicator, not a user-selected RGB/palette control.
            item['readonly_reason'] = 'derived native channel label-colour status; not a palette setter'
        if row.get('read_only') is True:
            item['readonly_reason'] = 'runtime inventory explicitly marks this controller read-only'
        for binding in ('module_vmt_rva', 'process_output_rva'):
            value = row.get(binding)
            if value is None:
                if item['readonly_reason'] is None:
                    item['readonly_reason'] = 'native owner VMT/callback binding is unavailable'
            else:
                item[binding] = _int(value, binding, 1, 0x7FFFFFFF)
        item['build_write_admitted'] = build_value('features').get('control_write') is True
        if not item['build_write_admitted'] and item['readonly_reason'] is None:
            item['readonly_reason'] = 'selected build profile has not admitted native control writes'
        item['control_write_authorization'] = None
        if item['build_write_admitted'] and item['readonly_reason'] is None:
            try:
                item['control_write_authorization'] = assert_control_write_allowed(key,
                    raw_type=raw_type, definition_rva=row.get('definition_rva'),
                    module_vmt_rva=row.get('module_vmt_rva'), process_output_rva=row.get('process_output_rva'))
            except CatalogError as error:
                item['readonly_reason'] = str(error)
        item['writable_candidate'] = item['readonly_reason'] is None
        controls.append(item)
    return {'schema_version': 1, 'executable_sha256': target_hash,
            'build_write_admitted': build_value('features').get('control_write') is True,
            'controls': controls, 'runtime_acceptance_verified': False,
            'inventory_sha256': hashlib.sha256(json.dumps(inventory, sort_keys=True,
                separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('utf-8')).hexdigest()}


def _encode(item, value):
    raw_type = item['raw_type']
    if raw_type == 2:
        value = _int(value, 'value', -2**31, 2**31-1)
        if value not in item['allowed_values']:
            raise CatalogError('packed route value is outside the runtime whitelist')
        return value, struct.pack('<i', value), 0.0
    value = _finite(value, 'value')
    if raw_type == 0 and (not value.is_integer() or int(value) not in item['allowed_values']):
        raise CatalogError('value is outside the native numeric enum')
    try:
        data = struct.pack('<f', value)
    except (OverflowError, struct.error) as error:
        raise CatalogError('value cannot be encoded as finite float32') from error
    encoded = struct.unpack('<f', data)[0]
    if not math.isfinite(encoded):
        raise CatalogError('value cannot be encoded as finite float32')
    if raw_type == 1:
        limits = item['range']
        if not limits['minimum'] <= encoded <= limits['maximum']:
            raise CatalogError('value is outside the native response/semantic bounds')
    # Exact readback bytes remain available. Numeric tolerance covers one f32 ULP
    # at the encoded magnitude; it is not a claim that the target accepts/clamps.
    tolerance = max(abs(encoded) * 2**-23, 2**-149) if raw_type == 1 else 0.0
    return (int(encoded) if raw_type == 0 else encoded), data, tolerance


@profile_bound(feature='control_write', documents=('catalog',), error_type=CatalogError)
def plan_control_write(catalog, control_id, value, expected=None):
    """Produce only a typed 4-byte native payload, never a packet or a send.

    expected is an optional compare-before-write value in the same native units.
    Returned runtime_acceptance_verified is always false; the caller must perform
    independent native pointer/profile checks and post-write readback.
    """
    key = _hex(control_id, 5, 'control_id').hex()
    if (not isinstance(catalog, dict) or type(catalog.get('schema_version')) is not int
            or catalog.get('schema_version') != 1 or catalog.get('executable_sha256') != build_value('executable.sha256')):
        raise CatalogError('catalog must be a normalized Quantum1 V2126 inventory')
    matches = [item for item in catalog.get('controls', []) if item.get('id') == key]
    if len(matches) != 1:
        raise CatalogError('control ID must exist exactly once in the runtime inventory')
    item = matches[0]
    if item.get('readonly_reason') or not item.get('writable_candidate'):
        raise CatalogError(item.get('readonly_reason') or 'controller is not a writable candidate')
    authorization = assert_control_write_allowed(key, raw_type=item['raw_type'],
        definition_rva=item['source'].get('definition_rva'), module_vmt_rva=item.get('module_vmt_rva'),
        process_output_rva=item.get('process_output_rva'))
    pointer_binding,pointer_error = _response_pointer_binding(item['source'],item['definition'])
    if pointer_error:
        raise CatalogError(pointer_error)
    typed, data, tolerance = _encode(item, value)
    result = {'id': key, 'raw_type': item['raw_type'], 'payload_type': item['payload_type'],
              'payload_hex': data.hex(), 'value': typed, 'tolerance': tolerance,
              'value_units': 'native_internal', 'display_units': item['display_units'],
              'verified_conversion': item['verified_conversion'],
              'verified_identity_units': item['verified_identity_units'],
              'control_write_authorization': authorization,
              'response_pointer_binding': pointer_binding,
              'semantic_context': item['semantic_context'], 'response_range': item['response_range'],
              'expected': None, 'expected_payload_hex': None,
              'module_vmt_rva': item['module_vmt_rva'],
              'process_output_rva': item['process_output_rva'],
              'definition_raw_hex': item['definition']['raw_hex'],
              'definition_rva': item['source'].get('definition_rva'),
              'allowed_values': item['allowed_values'], 'range': item['range'],
              'value_domain_source': item['value_domain_source'],
              'response': item['response'], 'response_lut': item['response_lut'],
              'inventory_sha256': catalog['inventory_sha256'],
              'runtime_acceptance_verified': False}
    if expected is not None:
        result['expected'], expected_data, _ = _encode(item, expected)
        result['expected_payload_hex'] = expected_data.hex()
    return result

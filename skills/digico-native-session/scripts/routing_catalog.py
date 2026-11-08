"""Pure, conservative native input-routing domain for exact Quantum1 V2126.

No sockets, process reads/writes, filesystem access, UI, or OSC. This deliberately
supports mono Input main/alt/key/insert routes, physical direct outputs and
Group assignments with exact code/owner/domain bindings. Physical sockets need
an actual channel/socket module, matching port/device and a fitted type. Plans
are not runtime or audio validation and do not authorize arbitrary integers.
"""
from __future__ import annotations

from build_profiles import build_value, profile_bound

import hashlib
import json
import re
import struct

TARGET_SHA256 = build_value('executable.sha256')
INPUT_VMT_RVA = build_value('addresses.hex_81aa44')
INPUT_CALLBACK_RVA = build_value('addresses.hex_826848')
SOURCE_DEFINITION_RVA = build_value('addresses.hex_8d16c0')
INPUT_SOCKET_VMT_RVA = build_value('addresses.hex_759440')
OUTPUT_SOCKET_VMT_RVA = build_value('addresses.hex_75a0e0')
SOURCE_PROFILES = build_value('routing.source_profiles')


class RoutingError(ValueError):
    pass


def _integer(value, label, low=0, high=0xffffffff):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise RoutingError('%s must be an integer in [%d, %d]' % (label, low, high))
    return value


def _raw(value, size, label):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{%d}' % (size * 2), value):
        raise RoutingError('%s must contain exactly %d hexadecimal bytes' % (label, size))
    return bytes.fromhex(value)


def _rows(value, label, limit):
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(x, dict) for x in value):
        raise RoutingError('%s must be a bounded object list' % label)
    return value


def _unique(rows, key, label):
    answer = {}
    for row in rows:
        value = _integer(row.get(key), label)
        if value in answer:
            raise RoutingError('ambiguous duplicate %s' % label)
        answer[value] = row
    return answer


def _inventory(value):
    if not isinstance(value, dict):
        raise RoutingError('inventory must be an object')
    if 'inventory' in value:
        if value.get('status') != 'ok' or value.get('tx_bytes') != 0:
            raise RoutingError('require successful read-only inventory envelope')
        value = value['inventory']
    if (not isinstance(value, dict) or value.get('schema_version') != 1
            or value.get('executable_sha256') != build_value('executable.sha256')
            or value.get('memory_read_only') is not True
            or value.get('structure_verified') is not True
            or value.get('io_inventory_complete') is not True):
        raise RoutingError('require the verified Quantum1 V2126 object/I/O inventory')
    return value


@profile_bound(feature='routes', documents=('inventory',), error_type=RoutingError)
def build_routing_catalog(inventory, direction='input'):
    """Return a domain of fitted physical sources, never a generic int32 range.

    Deliberately excludes internal sources, runtime-output socket groups, local
    ports without a configured IODevice, absent modules and socket types 0/0x1B.
    Names are display aids; IDs, owner VMT and port/device fields bind the domain.
    """
    inv = _inventory(inventory)
    if direction not in ('input', 'output'):
        raise RoutingError('routing direction must be input or output')
    input_direction = direction == 'input'
    channels = _unique(_rows(inv.get('channels'), 'channels', 8192), 'id', 'channel ID')
    modules = _rows(inv.get('modules'), 'modules', 65536)
    controls = _unique(_rows(inv.get('controllers'), 'controllers', 200000), 'pointer', 'controller pointer')
    ports = _unique(_rows(inv.get('ports'), 'ports', 49), 'index', 'port index')
    io_tables = _rows(inv.get('io_devices'), 'io_devices', 8)
    tables = [x for x in io_tables if x.get('role') == 'session_io_configuration']
    if len(tables) != 1 or tables[0].get('initialized') is not True:
        raise RoutingError('require exactly one configured I/O table')
    devices = _rows(tables[0].get('devices'), 'configured devices', 4096)
    devices_by_port = {}
    for device in devices:
        key = (_integer(device.get('port_type'), 'device port_type', 0, 255),
               _integer(device.get('port_number'), 'device port_number', 0, 255))
        if key in devices_by_port:
            raise RoutingError('ambiguous configured I/O port')
        count = _integer(device.get(direction+'_count'), 'device socket count', 0, 65536)
        types = _raw(device.get(direction+'_socket_types_hex'), count, direction+' socket types')
        devices_by_port[key] = (device, types)
    group_tables = _rows(inv.get('socket_groups'), 'socket_groups', 8)
    role = 'session_socket_groups' if input_direction else 'runtime_socket_groups'
    group_tables = [x for x in group_tables if x.get('role') == role]
    if len(group_tables) != 1 or group_tables[0].get('initialized') is not True:
        raise RoutingError('require exactly one configured socket-group table')
    banks = _unique(_rows(group_tables[0].get('groups'), 'socket-group names', 4096),
                    'object_value_raw', 'socket-group base ID')
    sources = []
    for channel_id, channel in channels.items():
        channel_id = _integer(channel_id, 'channel id', 0, 65535)
        group_id, socket_index = channel_id >> 8, channel_id & 255
        first_group, last_group = (0x1c, 0x4d) if input_direction else (0x4e, 0x7f)
        if not first_group <= group_id < last_group:
            continue
        index = group_id - first_group
        port = ports.get(index)
        if port is None:
            continue
        raw_port = _raw(port.get('raw_hex'), 0xd2, 'port record')
        port_type = _integer(port.get('port_type'), 'port_type', 0, 255)
        port_number = _integer(port.get('port_number'), 'port_number', 0, 255)
        if (raw_port[0xd0], raw_port[0xd1]) != (port_type, port_number):
            raise RoutingError('port fields disagree with copied native record')
        if port.get('configuration_group_id' if input_direction else 'runtime_group_id') != group_id:
            raise RoutingError('source group disagrees with port-index encoding')
        configured = devices_by_port.get((port_type, port_number))
        if configured is None:
            continue
        device, socket_types = configured
        if socket_index >= len(socket_types) or socket_types[socket_index] in (0, 0x1b):
            continue
        pointer = _integer(channel.get('pointer'), 'source channel pointer', 1)
        expected_class = 'tInputSocketModule' if input_direction else 'tOutputSocketModule'
        expected_vmt = build_value('addresses.hex_759440') if input_direction else build_value('addresses.hex_75a0e0')
        owners = [m for m in modules if m.get('parent_pointer') == pointer
                  and m.get('class_name') == expected_class and m.get('vmt_rva') == expected_vmt]
        if len(owners) != 1 or owners[0].get('n_signals') != 1:
            continue
        module = owners[0]
        extra = {}
        if input_direction:
            listen_pointer = module.get('listen_to_copy_control_pointer')
            listen = controls.get(listen_pointer)
            if (listen is None or listen.get('owner_module_pointer') != module.get('pointer')
                    or listen.get('target_raw_hex') not in ('00000000', '00000080')):
                continue
            extra = {'listen_to_copy_control_pointer': listen_pointer,
                     'listen_to_copy_next_raw_hex': listen['target_raw_hex']}
        else:
            route_pointer = module.get('route_control_pointer')
            route_control = controls.get(route_pointer)
            if (module.get('socket_active') is not True or route_control is None
                    or route_control.get('owner_module_pointer') != module.get('pointer')
                    or route_control.get('data_type') != 2):
                continue
            extra = {'route_control_pointer': route_pointer,
                     'current_source_raw_hex': _raw(route_control.get('output_value_raw_hex'), 4, 'socket current source').hex()}
        module_id = _raw(module.get('raw_id_hex'), 5, 'source module ID')
        if (module_id[3] | module_id[4] << 8) != channel_id:
            raise RoutingError('source module ID disagrees with owner channel')
        bank = banks.get(channel_id & 0xfff8)
        if bank is None or not isinstance(bank.get('name'), str) or not bank['name']:
            continue
        route = channel_id << 16
        sources.append({'direction': direction, 'channel_id': channel_id,
            'module_pointer': module.get('pointer'), 'module_vmt_rva': expected_vmt,
            'module_id_hex': module_id.hex(), 'port_type': port_type, 'port_number': port_number,
            'port_index': index, 'socket_index': socket_index,
            'socket_type': socket_types[socket_index], 'source_channel_id': channel_id,
            'channel_pointer': pointer, 'channel_name': channel.get('name'),
            'source_module_pointer': module.get('pointer'),
            'source_module_vmt_rva': expected_vmt,
            'source_module_id_hex': module_id.hex(), 'port_record_pointer': port.get('pointer'),
            'port_record_raw_hex': raw_port.hex(), 'configured_device_pointer': device.get('pointer'),
            'bank_name': bank['name'], 'slot_in_bank': (socket_index & 7) + 1,
            'route_uint32': route, 'route_raw_hex': struct.pack('<I', route).hex(), **extra})
    keys = [(x['port_type'], x['port_number'], x['socket_index']) for x in sources]
    if len(set(keys)) != len(keys):
        raise RoutingError('more than one native source resolves to the same port/socket')
    digest = hashlib.sha256(json.dumps(inv, sort_keys=True, separators=(',', ':'),
        ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    result = {'schema_version': 1, 'executable_sha256': build_value('executable.sha256'),
        'inventory_sha256': digest, 'scope': 'configured-physical-'+direction+'-sockets',
        'sources': sorted(sources, key=lambda x: (x['port_type'], x['port_number'], x['socket_index'])),
        'runtime_acceptance_verified': False}
    if not input_direction:
        result['destinations'] = result['sources']
    return result


@profile_bound(feature='routes', documents=('inventory',), error_type=RoutingError)
def plan_set_input_route(inventory, destination_channel_id, source, route_kind='main', insert_index=0):
    """Plan one mono Input source/key/insert route; None disconnects that route.

    source must be exactly {port_type, port_number, socket_index}; insert_send
    uses that tuple as a physical output destination. Independent
    runtime preflight must rebind all pointers/definition/port data and check the
    previous value; success requires both controller+2C and channel+B2 readback.
    """
    inv = _inventory(inventory)
    if not isinstance(route_kind, str) or route_kind not in build_value('routing.source_profiles'):
        raise RoutingError('unsupported semantic input route_kind')
    insert_index = _integer(insert_index, 'insert_index', 0, 1)
    profile = dict(build_value('routing.source_profiles')[route_kind])
    if route_kind.startswith('insert_'):
        profile['band'] = insert_index;profile['owner_band'] = insert_index
    elif insert_index != 0:
        raise RoutingError('insert_index only applies to insert routing')
    destination = _integer(destination_channel_id, 'destination_channel_id', 0, 65535)
    channels = [x for x in _rows(inv.get('channels'), 'channels', 8192) if x.get('id') == destination]
    if len(channels) != 1 or destination >> 8 != 1:
        raise RoutingError('destination must be one observed Input channel')
    channel = channels[0]
    if channel.get('stereo_mode') != 1:
        raise RoutingError('this routing operation currently supports mono Input channels only')
    pointer = _integer(channel.get('pointer'), 'destination pointer', 1)
    owners = [x for x in _rows(inv.get('modules'), 'modules', 65536)
        if x.get('parent_pointer') == pointer and x.get('class_name') == profile['class']
        and x.get('vmt_rva') == profile['vmt'] and x.get('n_signals') == 1
        and _raw(x.get('raw_id_hex'), 5, 'owner module ID')[2] == profile.get('owner_band', 0)]
    if len(owners) != 1:
        raise RoutingError('destination lacks the exact native Input owner binding')
    owner = owners[0]
    control_id = bytes((profile['parameter'], profile['module'], profile['band'], destination & 255, 1)).hex()
    controls = [x for x in _rows(inv.get('controllers'), 'controllers', 200000)
        if x.get('raw_id_hex') == control_id]
    if len(controls) != 1:
        raise RoutingError('native main source-route controller must be unique')
    control = controls[0]
    definition = _raw(control.get('definition_raw_hex'), build_value('layouts.definition.size'), 'source-route definition')
    if (definition[0], definition[5], definition[6]) != (2, profile.get('definition_band', profile['band']), profile['parameter']):
        raise RoutingError('source-route definition datatype/band/parameter mismatch')
    if (control.get('definition_rva') != profile['definition']
            or control.get('module_vmt_rva') != profile['vmt']
            or control.get('process_output_rva') != profile['callback']
            or control.get('owner_module_pointer') != owner.get('pointer')
            or control.get('module_id_hex') != owner.get('raw_id_hex')
            or control.get('definition_binding_verified') is not True):
        raise RoutingError('source-route controller does not have the exact code/owner binding')
    old_raw = _raw(control.get('output_value_raw_hex'), 4, 'current route value')
    if (_raw(control.get('value_raw_hex'), 4, 'Value route') != old_raw
            or _raw(control.get('target_raw_hex'), 4, 'NextValue route') != old_raw):
        raise RoutingError('route Value/NextValue/OutputValue are not stable and identical')
    old_route = struct.unpack('<i', old_raw)[0]
    if profile['field'] is not None:
        old_route = _integer(channel.get(profile['field']), 'channel '+profile['field'], -2**31, 2**31-1)
    if struct.pack('<i', old_route) != old_raw:
        raise RoutingError('controller and channel disagree on the previous main route')
    direction = profile.get('direction', 'input')
    catalog = build_routing_catalog(inv, direction=direction)
    selected = None
    if source is None:
        route = 0
    else:
        if not isinstance(source, dict) or set(source) != {'port_type', 'port_number', 'socket_index'}:
            raise RoutingError('source requires exactly port_type, port_number and socket_index')
        key = tuple(_integer(source[k], k, 0, 255) for k in ('port_type', 'port_number', 'socket_index'))
        matches = [x for x in catalog['sources'] if (x['port_type'], x['port_number'], x['socket_index']) == key]
        if len(matches) != 1:
            raise RoutingError('source is not an observed, fitted, configured native input socket')
        selected = matches[0]
        route = selected['route_uint32']
        if direction == 'output':
            expected_source = destination << 16 | insert_index << 12 | 8
            current_source = struct.unpack('<I', bytes.fromhex(selected['current_source_raw_hex']))[0]
            if current_source not in (0, expected_source):
                raise RoutingError('insert-send destination is occupied by another source')
    contract = {'id': control_id, 'raw_type': 2, 'payload_type': 'int32',
        'payload_hex': struct.pack('<I', route).hex(), 'value': route, 'tolerance': 0.0,
        'expected': old_route, 'expected_payload_hex': old_raw.hex(),
        'control_pointer': control.get('pointer'), 'owner_module_pointer': owner.get('pointer'),
        'module_vmt_rva': profile['vmt'], 'process_output_rva': profile['callback'],
        'definition_pointer': control.get('definition_pointer'),
        'definition_raw_hex': definition.hex(), 'definition_rva': profile['definition'],
        'owner_controller_offset': profile['slot'],
        'module_id_hex': owner.get('raw_id_hex'), 'allowed_values': [0] + [x['route_uint32'] for x in catalog['sources']],
        'range': None, 'response': None, 'response_lut': None,
        'value_domain_source': 'native_physical_socket_code_and_inventory_binding',
        'inventory_sha256': catalog['inventory_sha256'], 'runtime_acceptance_verified': False}
    previous_endpoint = None
    if direction == 'output' and old_route != 0:
        previous = [x for x in catalog['sources'] if x['route_uint32'] == old_route]
        if len(previous) != 1:
            raise RoutingError('previous insert-send destination lacks a verified physical output binding')
        previous_endpoint = previous[0]
        if previous_endpoint['current_source_raw_hex'] != struct.pack('<I', destination << 16 | insert_index << 12 | 8).hex():
            raise RoutingError('previous insert-send target is not owned by this insert')
    return {'op': 'set_input_route', 'id': control_id, 'channel_id': destination,
        'control_contract': contract, 'expected_input_route': old_route,
        'route_kind': route_kind, 'insert_index': insert_index, 'category': 8,
        'raw_id_hex': control_id, 'payload_type': 'native-int32-raw-bits',
        'payload_hex': struct.pack('<I', route).hex(), 'route_uint32': route,
        'destination_channel_id': destination, 'destination_channel_pointer': pointer,
        'controller_pointer': control.get('pointer'), 'definition_pointer': control.get('definition_pointer'),
        'definition_raw_hex': definition.hex(), 'definition_rva': profile['definition'],
        'module_vmt_rva': profile['vmt'], 'process_output_rva': profile['callback'],
        'owner_module_pointer': owner.get('pointer'), 'module_id_hex': owner.get('raw_id_hex'),
        'expected_payload_hex': old_raw.hex(), 'source': selected,
        'route_contract': {'direction': direction, 'previous_endpoint': previous_endpoint,
                           'target_source_raw_hex': (struct.pack('<I', destination << 16 | insert_index << 12 | 8).hex()
                                                     if direction == 'output' else None)},
        'postcondition': {'controller_output_raw_hex': struct.pack('<I', route).hex(),
                         'channel_route_field': profile['field'], 'channel_route_value': route},
        'inventory_sha256': catalog['inventory_sha256'], 'runtime_acceptance_verified': False,
        'audio_delivery_verified': False}


plan_input_route = plan_set_input_route


def _output_sockets(channel):
    values = channel.get('output_sockets')
    # Windows PowerShell 5.1 may preserve the adapted array wrapper in JSON.
    if isinstance(values, dict) and set(values) == {'value', 'Count'}:
        if not isinstance(values['value'], list) or values['Count'] != len(values['value']):
            raise RoutingError('output socket wrapper count mismatch')
        values = values['value']
    if not isinstance(values, list) or len(values) > 384:
        raise RoutingError('require the complete bounded native output-socket set')
    result = [_integer(x, 'output socket value') for x in values]
    if len(result) != len(set(result)):
        raise RoutingError('duplicate output-socket entry')
    return sorted(result)


@profile_bound(feature='routes', documents=('inventory',), error_type=RoutingError)
def plan_set_output_route(inventory, channel_id, destination, connected):
    """Plan one incremental mono Input direct-output connection/disconnection.

    Native Output FE differs from insert send: positive adds, negative removes
    exactly the destination. The full output-socket set is the postcondition.
    A route occupied by another source is not stolen or cleared.
    """
    inv = _inventory(inventory)
    channel_id = _integer(channel_id, 'channel_id', 0x100, 0x1ff)
    if type(connected) is not bool:
        raise RoutingError('connected must be a boolean')
    if not isinstance(destination, dict) or set(destination) != {'port_type', 'port_number', 'socket_index'}:
        raise RoutingError('destination requires exactly port_type, port_number and socket_index')
    key = tuple(_integer(destination[k], k, 0, 255) for k in ('port_type', 'port_number', 'socket_index'))
    catalog = build_routing_catalog(inv, direction='output')
    endpoints = [x for x in catalog['destinations'] if (x['port_type'], x['port_number'], x['socket_index']) == key]
    if len(endpoints) != 1:
        raise RoutingError('destination is not an observed, active, configured native output socket')
    endpoint = endpoints[0]
    channels = [x for x in _rows(inv.get('channels'), 'channels', 8192) if x.get('id') == channel_id]
    if len(channels) != 1 or channels[0].get('stereo_mode') != 1:
        raise RoutingError('output routing currently requires one observed mono Input channel')
    channel = channels[0]
    modules = [x for x in _rows(inv.get('modules'), 'modules', 65536)
        if x.get('parent_pointer') == channel.get('pointer') and x.get('class_name') == 'tOutputModule'
        and x.get('vmt_rva') == build_value('addresses.hex_81a3cc') and x.get('n_signals') == 1]
    if len(modules) != 1:
        raise RoutingError('output module code/owner binding is unavailable')
    owner = modules[0]
    control_id = bytes((0xfe, 14, 0, channel_id & 255, 1)).hex()
    controls = _rows(inv.get('controllers'), 'controllers', 200000)
    matches = [x for x in controls if x.get('raw_id_hex') == control_id]
    if len(matches) != 1:
        raise RoutingError('output route controller must be unique')
    control = matches[0]
    raw = _raw(control.get('definition_raw_hex'), build_value('layouts.definition.size'), 'output definition')
    if ((raw[0], raw[5], raw[6]) != (2, 0, 0xfe)
            or control.get('definition_rva') != build_value('addresses.hex_8d738f')
            or control.get('module_vmt_rva') != build_value('addresses.hex_81a3cc')
            or control.get('process_output_rva') != build_value('addresses.hex_824f9c')
            or control.get('owner_module_pointer') != owner.get('pointer')
            or control.get('module_id_hex') != owner.get('raw_id_hex')
            or control.get('definition_binding_verified') is not True):
        raise RoutingError('output route controller definition/callback binding differs')
    old_raw = _raw(control.get('output_value_raw_hex'), 4, 'output command value')
    if (_raw(control.get('value_raw_hex'), 4, 'output Value') != old_raw
            or _raw(control.get('target_raw_hex'), 4, 'output NextValue') != old_raw):
        raise RoutingError('output command is not stable')
    sockets = _output_sockets(channel)
    target_route = endpoint['route_uint32'];source_route = channel_id << 16
    target_source = struct.unpack('<I', bytes.fromhex(endpoint['current_source_raw_hex']))[0]
    present = target_route in sockets
    if present != (target_source == source_route):
        raise RoutingError('source output-socket set and destination source controller disagree')
    if connected and target_source not in (0, source_route):
        raise RoutingError('physical output is occupied by another source')
    if connected and not present and len(sockets) >= 384:
        raise RoutingError('native output-socket capacity reached')
    post_sockets = sorted(set(sockets) | {target_route}) if connected else [x for x in sockets if x != target_route]
    signed_command = target_route if connected else -target_route
    payload = struct.pack('<i', signed_command).hex()
    contract = {'id': control_id, 'raw_type': 2, 'payload_type': 'int32',
        'payload_hex': payload, 'value': signed_command, 'tolerance': 0.0,
        'expected': struct.unpack('<i', old_raw)[0], 'expected_payload_hex': old_raw.hex(),
        'control_pointer': control.get('pointer'), 'owner_module_pointer': owner.get('pointer'),
        'module_vmt_rva': build_value('addresses.hex_81a3cc'), 'process_output_rva': build_value('addresses.hex_824f9c'),
        'definition_pointer': control.get('definition_pointer'), 'definition_rva': build_value('addresses.hex_8d738f'),
        'definition_raw_hex': raw.hex(), 'module_id_hex': owner.get('raw_id_hex'),
        'allowed_values': [signed_command], 'range': None, 'response': None, 'response_lut': None,
        'value_domain_source': 'native_incremental_output_route',
        'inventory_sha256': catalog['inventory_sha256'], 'runtime_acceptance_verified': False}
    return {'op': 'set_output_route', 'id': control_id, 'channel_id': channel_id,
        'destination': endpoint, 'connected': connected, 'route_kind': 'output',
        'control_contract': contract, 'expected_input_route': None,
        'route_contract': {'direction': 'output', 'previous_output_sockets': sockets,
            'expected_output_sockets': post_sockets, 'target_source_raw_hex': struct.pack('<I', source_route if connected else (0 if present else target_source)).hex(),
            'no_change': post_sockets == sockets},
        'runtime_acceptance_verified': False, 'audio_delivery_verified': False}


plan_output_route = plan_set_output_route


@profile_bound(feature='routes', documents=('step', 'inventory'), error_type=RoutingError)
def verify_output_route_postcondition(step, inventory):
    """Reconcile a sent output command using a new, independently read inventory.

    Removal may settle its transient FE controller to zero. That exception only
    applies to a negative output remove; the complete set and physical socket's
    source must still exactly satisfy the requested postcondition. No replay.
    """
    inv = _inventory(inventory)
    if (not isinstance(step, dict) or step.get('op') != 'set_output_route'
            or type(step.get('connected')) is not bool):
        raise RoutingError('require a compiled output-route step')
    contract = step['control_contract'];endpoint = step['destination'];semantic = step['route_contract']
    controls = [c for c in _rows(inv.get('controllers'), 'controllers', 200000)
        if c.get('raw_id_hex') == step.get('id') and c.get('pointer') == contract.get('control_pointer')]
    if len(controls) != 1:
        raise RoutingError('output command identity changed before reconciliation')
    control = controls[0]
    if (control.get('owner_module_pointer') != contract.get('owner_module_pointer')
            or control.get('module_vmt_rva') != build_value('addresses.hex_81a3cc')
            or control.get('process_output_rva') != build_value('addresses.hex_824f9c')
            or control.get('definition_raw_hex') != contract.get('definition_raw_hex')):
        raise RoutingError('output command code/owner/definition changed')
    want = _raw(contract.get('payload_hex'), 4, 'output command payload').hex()
    copied = [_raw(control.get(k), 4, k).hex() for k in ('value_raw_hex', 'target_raw_hex', 'output_value_raw_hex')]
    echoed = all(x == want for x in copied)
    cleared = (not step['connected'] and struct.unpack('<i', bytes.fromhex(want))[0] < 0
               and all(x == '00000000' for x in copied))
    channels = [c for c in _rows(inv.get('channels'), 'channels', 8192) if c.get('id') == step.get('channel_id')]
    if len(channels) != 1:
        raise RoutingError('source channel is unavailable for reconciliation')
    expected_set = sorted(semantic.get('expected_output_sockets', []))
    set_matches = _output_sockets(channels[0]) == expected_set
    endpoints = [e for e in build_routing_catalog(inv, direction='output')['destinations']
        if e['channel_pointer'] == endpoint.get('channel_pointer')
        and e['source_module_pointer'] == endpoint.get('source_module_pointer')
        and e['route_control_pointer'] == endpoint.get('route_control_pointer')]
    target_matches = (len(endpoints) == 1 and endpoints[0]['current_source_raw_hex'] == semantic.get('target_source_raw_hex'))
    return {'verified': bool((echoed or cleared) and set_matches and target_matches),
        'command_state': 'echoed' if echoed else ('cleared_after_remove' if cleared else 'unexpected'),
        'output_set_matches': set_matches, 'target_source_matches': target_matches,
        'replayed': False, 'audio_delivery_verified': False}


@profile_bound(feature='routes', documents=('inventory',), error_type=RoutingError)
def plan_group_assignment(inventory, channel_id, group_channel_id, enabled):
    """Plan one GroupSend Param09 float32 toggle plus one BussRoutes bit.

    Runtime heap definitions are accepted only with copied bytes/parameter/band
    and exact module VMT/callback/owner binding. This does not accept a bitmask.
    """
    inv = _inventory(inventory)
    channel_id = _integer(channel_id, 'channel_id', 0x100, 0x1ff)
    group_channel_id = _integer(group_channel_id, 'group_channel_id', 0x600, 0x6ff)
    if type(enabled) is not bool:
        raise RoutingError('enabled must be a boolean')
    rows = _rows(inv.get('channels'), 'channels', 8192)
    sources = [x for x in rows if x.get('id') == channel_id]
    targets = [x for x in rows if x.get('id') == group_channel_id]
    if len(sources) != 1 or sources[0].get('stereo_mode') != 1 or len(targets) != 1:
        raise RoutingError('require an observed mono Input and an observed Group Output')
    channel, group = sources[0], targets[0]
    target_mode = group.get('stereo_mode')
    if target_mode not in (1, 2):
        raise RoutingError('group assignment currently supports mono/stereo Group outputs only')
    modules = _rows(inv.get('modules'), 'modules', 65536)
    owners = [x for x in modules if x.get('parent_pointer') == channel.get('pointer')
        and x.get('class_name') == 'tGroupSendModule' and x.get('vmt_rva') == build_value('addresses.hex_81d950')
        and x.get('n_signals') == 1 and x.get('raw_id_hex') == bytes((0, 3, 0, channel_id & 255, 1)).hex()]
    faders = [x for x in modules if x.get('parent_pointer') == group.get('pointer')
        and x.get('class_name') == 'tFaderModule' and x.get('vmt_rva') == build_value('addresses.hex_81e510')
        and x.get('n_signals') == target_mode]
    if len(owners) != 1 or len(faders) != 1:
        raise RoutingError('source GroupSend or target Group Fader code/owner binding is unavailable')
    owner, fader = owners[0], faders[0]
    fader_id = _raw(fader.get('raw_id_hex'), 5, 'group fader ID')
    if (fader_id[1], fader_id[2], fader_id[3], fader_id[4]) != (12, 0, group_channel_id & 255, 6):
        raise RoutingError('target group fader ID disagrees with target channel')
    index = group_channel_id & 255
    key = bytes((9, 3, index, channel_id & 255, 1)).hex()
    controls = [x for x in _rows(inv.get('controllers'), 'controllers', 200000) if x.get('raw_id_hex') == key]
    if len(controls) != 1:
        raise RoutingError('group assignment controller must exist exactly once')
    control = controls[0]
    definition = _raw(control.get('definition_raw_hex'), build_value('layouts.definition.size'), 'group definition')
    if ((definition[0], definition[5], definition[6]) != (0, index, 9)
            or control.get('module_vmt_rva') != build_value('addresses.hex_81d950')
            or control.get('process_output_rva') != build_value('addresses.hex_83042c')
            or control.get('owner_module_pointer') != owner.get('pointer')
            or control.get('module_id_hex') != owner.get('raw_id_hex')
            or control.get('definition_binding_verified') is not True):
        raise RoutingError('group assignment controller code/definition/owner binding differs')
    current = _raw(control.get('output_value_raw_hex'), 4, 'group current value')
    if (current.hex() not in ('00000000', '0000803f')
            or _raw(control.get('value_raw_hex'), 4, 'group Value') != current
            or _raw(control.get('target_raw_hex'), 4, 'group NextValue') != current):
        raise RoutingError('group controller does not have a stable native boolean value')
    before = _raw(channel.get('buss_routes_hex'), 32, 'BussRoutes')
    bit_set = bool(before[index // 8] & (1 << (index & 7)))
    if bit_set != (current.hex() == '0000803f'):
        raise RoutingError('group controller and BussRoutes bit disagree')
    after = bytearray(before)
    if enabled:
        after[index // 8] |= 1 << (index & 7)
    else:
        after[index // 8] &= 255 ^ (1 << (index & 7))
    payload = struct.pack('<f', float(enabled)).hex()
    digest = hashlib.sha256(json.dumps(inv, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
        allow_nan=False).encode()).hexdigest()
    contract = {'id': key, 'raw_type': 0, 'payload_type': 'float32_discrete',
        'payload_hex': payload, 'value': int(enabled), 'tolerance': 0.0,
        'expected': int(bit_set), 'expected_payload_hex': current.hex(),
        'control_pointer': control.get('pointer'), 'owner_module_pointer': owner.get('pointer'),
        'module_vmt_rva': build_value('addresses.hex_81d950'), 'process_output_rva': build_value('addresses.hex_83042c'),
        'definition_pointer': control.get('definition_pointer'), 'definition_rva': control.get('definition_rva'),
        'definition_raw_hex': definition.hex(), 'module_id_hex': owner.get('raw_id_hex'),
        'allowed_values': [0, 1], 'range': None, 'response': None, 'response_lut': None,
        'value_domain_source': 'native_groupsend_parameter09_bussroutes_bit',
        'inventory_sha256': digest, 'runtime_acceptance_verified': False}
    return {'op': 'set_group_assignment', 'id': key, 'channel_id': channel_id,
        'group_channel_id': group_channel_id, 'enabled': enabled, 'control_contract': contract,
        'group_contract': {'source_channel_pointer': channel.get('pointer'),
            'target_channel_pointer': group.get('pointer'), 'target_stereo_mode': target_mode,
            'target_buss_id': group.get('buss_id'), 'target_fader_pointer': fader.get('pointer'),
            'target_fader_vmt_rva': build_value('addresses.hex_81e510'), 'target_fader_id_hex': fader_id.hex(),
            'target_n_signals': target_mode, 'bit_index': index,
            'expected_buss_routes_hex': before.hex(), 'post_buss_routes_hex': after.hex()},
        'runtime_acceptance_verified': False, 'audio_delivery_verified': False}

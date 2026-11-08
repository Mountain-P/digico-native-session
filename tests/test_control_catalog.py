"""Synthetic contract vectors; no executable, editor, network or VM required."""
import importlib.util
from pathlib import Path
import struct
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/digico-native-session/scripts/control_catalog.py'
spec = importlib.util.spec_from_file_location('control_catalog', SCRIPT)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def definition(datatype=1, parameter=0x21, band=0, name='synthetic', units='dB'):
    raw = bytearray(58)
    raw[0] = datatype
    struct.pack_into('<f', raw, 1, -20.0 if datatype == 1 else 0.0)
    raw[5:7] = bytes((band, parameter))
    text = name.encode('ascii');raw[7] = len(text);raw[8:8+len(text)] = text
    text = units.encode('ascii');raw[39] = len(text);raw[40:40+len(text)] = text
    struct.pack_into('<I', raw, 50, 0x500000 if datatype == 1 else 0)
    return raw.hex()


def seed(points=((0, -150.0), (255, 10.0)), curve=1):
    raw = bytearray(38);raw[0] = curve;raw[1:4] = b'\x02dB'
    struct.pack_into('<HI', raw, 32, len(points), 0x600000)
    copied = []
    for position,value in points:
        data = struct.pack('<if', position, value)
        copied.append({'position': position, 'value': {'raw_hex': data[4:].hex(),
                       'float32': value}, 'raw_hex': data.hex()})
    return raw.hex(), copied


def inventory(datatype=1, parameter=0x21, module=12, band=0):
    sh,points = seed()
    return {'schema_version': 1, 'executable_sha256': c.TARGET_SHA256,
            'session_path': r'C:\Synthetic\Test.ses',
            'controllers': [{'raw_id_hex': bytes((parameter,module,band,0,1)).hex(),
                'definition_raw_hex': definition(datatype, parameter, band),
                'definition_pointer': 0x500100, 'definition_rva': 0x100100,
                'data_type': datatype, 'module_id_hex': bytes((0,module,0,0,1)).hex(),
                'module_vmt_rva': 0x1000, 'process_output_rva': 0x2000,
                'value_raw_hex': struct.pack('<f', -20.0).hex()}],
            'definitions': [{'pointer': 0x500100,
                'raw_hex': definition(datatype, parameter, band),
                **({'response_seed_raw_hex': sh, 'response_points': points} if datatype == 1 else {})}]}


def contextual_inventory(profile='compressor_v2126',parameter=0x11,mode=0,resending=0,timer=0):
    gate=profile=='gate_v2126';band=3 if gate else 0
    j=inventory(1,parameter,5,band);row=j['controllers'][0]
    owner=0x700000;mode_pointer=0x800100;mode_parameter=0x0b if gate else 0x3a
    row.update(pointer=0x800000,owner_module_pointer=owner,
        module_vmt_rva=0x81C8C0 if gate else 0x81C240,
        process_output_rva=0x82D3DC if gate else 0x82BAC0)
    mode_id=bytes((mode_parameter,5,band if gate else 0,0,1)).hex()
    mode_def=definition(0,mode_parameter,band if gate else 0,name='synthetic mode',units='')
    mode_raw=struct.pack('<f',mode).hex()
    copied={'profile':profile,'owner_module_pointer':owner,'mode_controller_pointer':mode_pointer,
        'mode_id_hex':mode_id,'mode_output_raw_hex':mode_raw,'resending':resending}
    if gate:copied['mode_changed_timer_count']=timer
    row['owner_native_context']=copied
    j['controllers'].append({'pointer':mode_pointer,'raw_id_hex':mode_id,
        'definition_pointer':0x500200,'definition_raw_hex':mode_def,'data_type':0,
        'vmt_rva':0x8680FC,'module_vmt_rva':row['module_vmt_rva'],
        'process_output_rva':row['process_output_rva'],'owner_module_pointer':owner,
        'value_raw_hex':mode_raw,'output_value_raw_hex':mode_raw})
    j['definitions'].append({'pointer':0x500200,'raw_hex':mode_def})
    if parameter==0x11:
        low,high=(0.00005,0.1) if gate else (0.0005,0.1)
    else:low,high=(0.005,5.) if gate else (0.01,10.)
    raw,points=seed(((0,low),(255,high)));data=bytearray.fromhex(raw)
    data[1:32]=bytes.fromhex(c.TIME_UNIT_DESCRIPTOR)
    for point in points:point['value']['float32']=struct.unpack('<f',bytes.fromhex(point['raw_hex'])[4:])[0]
    j['definitions'][0]['response_seed_raw_hex']=data.hex();j['definitions'][0]['response_points']=points
    return j


def eq_context_inventory(shape=1,band=0):
    j=inventory(1,6,4,band);row=j['controllers'][0];owner=0x700000;mode_pointer=0x800100
    row.update(pointer=0x800000,owner_module_pointer=owner,module_vmt_rva=0x81BD7C,process_output_rva=0x82A938)
    mode_id=bytes((4,4,band,0,1)).hex();mode_def=definition(0,4,band,name='synthetic shape',units='')
    dr=bytearray.fromhex(mode_def);struct.pack_into('<f',dr,1,1.);mode_def=dr.hex()
    mode_raw=struct.pack('<f',shape).hex()
    row['owner_native_context']={'profile':'eq_q_v2126','owner_module_pointer':owner,
        'mode_controller_pointer':mode_pointer,'mode_id_hex':mode_id,'mode_output_raw_hex':mode_raw}
    j['controllers'].append({'pointer':mode_pointer,'raw_id_hex':mode_id,'definition_pointer':0x500200,
        'definition_raw_hex':mode_def,'data_type':0,'vmt_rva':0x8680FC,
        'module_vmt_rva':0x81BD7C,'process_output_rva':0x82A938,'owner_module_pointer':owner,
        'value_raw_hex':mode_raw,'output_value_raw_hex':mode_raw})
    j['definitions'].append({'pointer':0x500200,'raw_hex':mode_def})
    raw,points=seed(((0,0.10000000149011612),(255,20.)))
    j['definitions'][0]['response_seed_raw_hex']=raw;j['definitions'][0]['response_points']=points
    return j


class DefinitionTests(unittest.TestCase):
    def test_exact_packed_offsets_and_retained_bytes(self):
        raw = definition()
        parsed = c.parse_definition(raw)
        self.assertEqual((parsed['raw_type'],parsed['parameter'],parsed['name'],parsed['units']),
                         (1,0x21,'synthetic','dB'))
        self.assertEqual(parsed['default_raw_hex'], '0000a0c1')
        self.assertEqual(parsed['response_seed_pointer'], 0x500000)
        self.assertEqual(parsed['raw_hex'], raw)

    def test_malformed_definition_length_and_shortstring(self):
        for raw in ('00', 'g'*116):
            with self.assertRaises(c.CatalogError): c.parse_definition(raw)
        raw = bytearray.fromhex(definition());raw[7] = 32
        with self.assertRaises(c.CatalogError): c.parse_definition(raw.hex())

    def test_response_bounds_use_all_points_not_endpoints(self):
        raw,points = seed(((0,0.0),(100,-20.0),(255,10.0)))
        r = c.parse_response_seed(raw, points)
        self.assertEqual((r['minimum'],r['maximum']),(-20.0,10.0))
        self.assertEqual(r['curve_name'],'cv_Lin')
        self.assertEqual(r['unit_desc_hex'][:6], '026442')

    def test_response_bad_count_order_and_raw_disagreement(self):
        raw,points = seed()
        for bad in (points[:-1], list(reversed(points))):
            with self.assertRaises(c.CatalogError): c.parse_response_seed(raw,bad)
        points[0]['value']['raw_hex'] = '00000000'
        with self.assertRaises(c.CatalogError): c.parse_response_seed(raw, points)

    def test_powershell_single_json_decimal_is_not_exact_double(self):
        raw,points=seed(((0,0.7099999785423279),(255,10.)))
        points[0]['value']['float32']=0.71
        parsed=c.parse_response_seed(raw,points)
        self.assertEqual(parsed['points'][0]['value'],0.7099999785423279)
        # A JSON decimal one single-precision step away is tolerated; raw is authority.
        original=bytes.fromhex(points[0]['raw_hex'])[4:]
        bits=struct.unpack('<I',original)[0]
        points[0]['value']['float32']=struct.unpack('<f',struct.pack('<I',bits+1))[0]
        self.assertEqual(c.parse_response_seed(raw,points)['points'][0]['value'],0.7099999785423279)
        points[0]['value']['float32']=0.72
        with self.assertRaises(c.CatalogError): c.parse_response_seed(raw,points)

    def test_lut_exact_size_and_nonfinite_boundaries(self):
        raw=struct.pack('<256f',*range(256)).hex()
        parsed=c.parse_response_lut(raw)
        self.assertEqual((len(parsed['values']),parsed['minimum'],parsed['maximum']),(256,0.,255.))
        for bad in (raw[:-8],struct.pack('<256f',float('nan'),*range(255)).hex()):
            with self.assertRaises(c.CatalogError): c.parse_response_lut(bad)

    def test_native_numeric_enums_are_not_all_booleans(self):
        self.assertEqual(c.discrete_values('0201000001'),[0,1,2,3])
        self.assertEqual(c.discrete_values('0402000001'),list(range(6)))
        self.assertEqual(c.discrete_values('220c000001'),[0,1])
        self.assertEqual(c.discrete_values('0404000001'),[1,4])
        self.assertIsNone(c.discrete_values('0404010001'))
        self.assertEqual(c.discrete_values('0404010001',14),[1,4])
        self.assertEqual(c.discrete_values('0404010001',15),[1])
        self.assertEqual(c.discrete_values('0404010001',24),[1,4])
        self.assertEqual(c.discrete_values('041c010001'),[1,9])


class InventoryTests(unittest.TestCase):
    def test_shared_definition_join_and_float_packet_vector(self):
        j=inventory();catalog=c.normalize_inventory(j)
        p=c.plan_control_write(catalog,'210c000001',-5,expected=-20)
        self.assertEqual(p['payload_hex'],'0000a0c0')
        self.assertEqual(p['expected_payload_hex'],'0000a0c1')
        self.assertEqual(p['range'],{'minimum':-150.,'maximum':10.,'source':'native_response_point_extrema'})
        self.assertFalse(p['runtime_acceptance_verified'])
        self.assertEqual(p['value_units'],'native_internal')
        self.assertEqual(p['display_units'],'dB')
        self.assertIsNone(p['verified_conversion'])
        self.assertEqual(p['definition_rva'],0x100100)
        self.assertEqual(j,inventory()) # Pure/no mutation.

    def test_numeric_discrete_is_encoded_as_float32_not_int32(self):
        j=inventory(0,0x22)
        j['definitions'][0].pop('response_seed_raw_hex',None);j['definitions'][0].pop('response_points',None)
        p=c.plan_control_write(c.normalize_inventory(j),'220c000001',1)
        self.assertEqual(p['payload_hex'],'0000803f')
        for bad in (2,0.5,True):
            with self.assertRaises(c.CatalogError): c.plan_control_write(c.normalize_inventory(j),'220c000001',bad)

    def test_rejects_nonfinite_values_and_outside_range(self):
        cat=c.normalize_inventory(inventory())
        for bad in (-151,11,float('nan'),float('inf'),True,'-10',10**1000):
            with self.assertRaises(c.CatalogError): c.plan_control_write(cat,'210c000001',bad)
        for value in (-150,10):
            self.assertEqual(c.plan_control_write(cat,'210c000001',value)['value'],value)

    def test_expected_is_validated_under_same_contract(self):
        cat=c.normalize_inventory(inventory())
        with self.assertRaises(c.CatalogError): c.plan_control_write(cat,'210c000001',0,expected=11)

    def test_schema_and_collection_types_are_strict(self):
        for version in (True,1.0,'1',None):
            j=inventory();j['schema_version']=version
            with self.assertRaises(c.CatalogError): c.normalize_inventory(j)
        for field,value in (('definitions',{}),('controllers',{})):
            j=inventory();j[field]=value
            with self.assertRaises(c.CatalogError): c.normalize_inventory(j)

    def test_wrong_target_duplicate_id_or_bad_definition_rejected(self):
        j=inventory();j['executable_sha256']='0'*64
        with self.assertRaises(c.CatalogError): c.normalize_inventory(j)
        j=inventory();j['controllers']*=2
        with self.assertRaises(c.CatalogError): c.normalize_inventory(j)
        j=inventory();j['controllers'][0]['raw_id_hex']='220c000001'
        with self.assertRaises(c.CatalogError): c.normalize_inventory(j)
        j=inventory();j['definitions'][0]['raw_hex']=definition(name='mismatch')
        with self.assertRaises(c.CatalogError): c.normalize_inventory(j)

    def test_binding_owner_mismatch_and_datatype_mismatch(self):
        j=inventory();j['controllers'][0]['data_type']=0
        with self.assertRaises(c.CatalogError): c.normalize_inventory(j)
        j=inventory();j['controllers'][0]['module_id_hex']='000d000001'
        with self.assertRaises(c.CatalogError): c.normalize_inventory(j)
        j=inventory();j['controllers'][0].pop('module_vmt_rva')
        cat=c.normalize_inventory(j)
        self.assertIn('binding',cat['controls'][0]['readonly_reason'])
        with self.assertRaises(c.CatalogError): c.plan_control_write(cat,'210c000001',0)

    def test_cues_meters_strings_and_unknown_are_read_only(self):
        for datatype,module in ((1,10),(3,1),(4,1),(255,1)):
            j=inventory(datatype,0x21,module)
            cat=c.normalize_inventory(j)
            self.assertFalse(cat['controls'][0]['writable_candidate'])
            with self.assertRaises(c.CatalogError): c.plan_control_write(cat,j['controllers'][0]['raw_id_hex'],0)

    def test_numeric_missing_or_unknown_response_cannot_write(self):
        j=inventory();j.pop('definitions')
        cat=c.normalize_inventory(j)
        with self.assertRaises(c.CatalogError): c.plan_control_write(cat,'210c000001',0)
        j=inventory();raw,points=seed(curve=255)
        j['definitions'][0]['response_seed_raw_hex']=raw
        cat=c.normalize_inventory(j)
        with self.assertRaises(c.CatalogError): c.plan_control_write(cat,'210c000001',0)

    def test_time_units_are_internal_and_conversion_is_metadata_only(self):
        j=inventory(1,8,9)
        j['controllers'][0].update(owner_module_pointer=0x700000,module_vmt_rva=0x81B2F4,process_output_rva=0x828640)
        raw,points=seed(((0,0.),(255,1.2999999523162842)))
        data=bytearray.fromhex(raw);data[1:32]=bytes.fromhex(c.TIME_UNIT_DESCRIPTOR)
        j['definitions'][0]['response_seed_raw_hex']=data.hex();j['definitions'][0]['response_points']=points
        cat=c.normalize_inventory(j)
        plan=c.plan_control_write(cat,'0809000001',0.01)
        self.assertEqual(plan['value_units'],'native_internal')
        self.assertEqual(plan['verified_conversion']['native_unit'],'seconds')
        self.assertEqual(plan['verified_conversion']['native_to_reference_scale'],1000.)
        self.assertFalse(plan['verified_conversion']['automatic_conversion'])
        self.assertEqual(plan['payload_hex'],struct.pack('<f',0.01).hex())
        # An arbitrary display label never grants a conversion.
        j=inventory();dr=bytearray.fromhex(j['controllers'][0]['definition_raw_hex']);dr[39:42]=b'\x02mS'
        j['controllers'][0]['definition_raw_hex']=j['definitions'][0]['raw_hex']=dr.hex()
        self.assertIsNone(c.normalize_inventory(j)['controls'][0]['verified_conversion'])

    def test_compressor_mode_context_overrides_response_curve_limits(self):
        normal=c.normalize_inventory(contextual_inventory(mode=0))
        alternate=c.normalize_inventory(contextual_inventory(mode=2))
        key='1105000001'
        self.assertEqual(c.plan_control_write(normal,key,0.0005)['payload_hex'],struct.pack('<f',0.0005).hex())
        with self.assertRaises(c.CatalogError):c.plan_control_write(normal,key,0.00002)
        alternate_plan=c.plan_control_write(alternate,key,0.00002)
        self.assertEqual(alternate_plan['semantic_context']['mode_id_hex'],'3a05000001')
        self.assertEqual(alternate_plan['semantic_context']['mode_controller_slot'],0x54)
        self.assertEqual(alternate_plan['range']['maximum'],c._f32(0x3ca3d70a))
        self.assertGreater(alternate_plan['response_range']['minimum'],alternate_plan['range']['minimum'])
        with self.assertRaises(c.CatalogError):c.plan_control_write(alternate,key,0.05)
        release=c.normalize_inventory(contextual_inventory(parameter=0x12,mode=2))
        with self.assertRaises(c.CatalogError):c.plan_control_write(release,'1205000001',0.3)

    def test_encoded_float32_minimum_and_outside_ulp_boundary(self):
        cat=c.normalize_inventory(contextual_inventory(mode=0));key='1105000001'
        self.assertEqual(c.plan_control_write(cat,key,0.0005)['payload_hex'],'6f12033a')
        outside=struct.unpack('<f',struct.pack('<I',0x3a03126f-2))[0]
        with self.assertRaises(c.CatalogError):c.plan_control_write(cat,key,outside)
        alternate=c.normalize_inventory(contextual_inventory(mode=2))
        self.assertEqual(c.plan_control_write(alternate,key,0.00002)['payload_hex'],'acc5a737')

    def test_eq_q_is_gated_by_actual_band_shape(self):
        key='0604000001'
        bell=c.normalize_inventory(eq_context_inventory(shape=1))
        self.assertEqual(c.plan_control_write(bell,key,10)['value'],10.)
        shelf=c.normalize_inventory(eq_context_inventory(shape=4))
        self.assertEqual(c.plan_control_write(shelf,key,0.85)['payload_hex'],'9a99593f')
        with self.assertRaises(c.CatalogError):c.plan_control_write(shelf,key,1.)
        self.assertEqual(shelf['controls'][0]['semantic_context']['mode_controller_slot'],0x58)
        missing=eq_context_inventory();missing['controllers'][0].pop('owner_native_context')
        with self.assertRaises(c.CatalogError):c.plan_control_write(c.normalize_inventory(missing),key,1.)

    def test_missing_stale_or_resending_context_never_plans_time_write(self):
        missing=contextual_inventory();missing['controllers'][0].pop('owner_native_context')
        cat=c.normalize_inventory(missing)
        self.assertIn('context',cat['controls'][0]['readonly_reason'])
        busy=c.normalize_inventory(contextual_inventory(resending=1))
        self.assertIn('resending',busy['controls'][0]['readonly_reason'])
        with self.assertRaises(c.CatalogError):c.plan_control_write(busy,'1105000001',0.01)
        stale=contextual_inventory();stale['controllers'][1]['output_value_raw_hex']=struct.pack('<f',1).hex()
        with self.assertRaises(c.CatalogError):c.normalize_inventory(stale)
        unknown=contextual_inventory();unknown['controllers'][0]['module_vmt_rva']=0x1234
        self.assertIn('unrecognized',c.normalize_inventory(unknown)['controls'][0]['readonly_reason'])

    def test_gate_timer_and_mode_dependent_ranges(self):
        key='1105030001'
        normal=c.normalize_inventory(contextual_inventory('gate_v2126',mode=0))
        self.assertEqual(c.plan_control_write(normal,key,0.00005)['payload_hex'],struct.pack('<f',0.00005).hex())
        alternate=c.normalize_inventory(contextual_inventory('gate_v2126',mode=2))
        with self.assertRaises(c.CatalogError):c.plan_control_write(alternate,key,0.00005)
        release=c.normalize_inventory(contextual_inventory('gate_v2126',parameter=0x12,mode=2))
        self.assertEqual(c.plan_control_write(release,'1205030001',10)['value'],10.)
        pending=c.normalize_inventory(contextual_inventory('gate_v2126',timer=1))
        self.assertIn('timer',pending['controls'][0]['readonly_reason'])

    def test_context_can_join_from_owner_module_copy(self):
        source=contextual_inventory()
        copied=source['controllers'][0].pop('owner_native_context')
        source['modules']=[{'pointer':copied['owner_module_pointer'],'owner_native_context':copied}]
        plan=c.plan_control_write(c.normalize_inventory(source),'1105000001',0.01)
        self.assertEqual(plan['semantic_context']['owner_module_pointer'],0x700000)

    def test_fine_delay_is_samples_and_total_delay_is_derived_readonly(self):
        fine=c.normalize_inventory(inventory(1,0x17,9))['controls'][0]
        self.assertEqual(fine['verified_conversion']['native_unit'],'samples')
        self.assertTrue(fine['verified_conversion']['requires_runtime_sample_rate'])
        total=c.normalize_inventory(inventory(1,0x18,9))
        self.assertIn('derived total delay',total['controls'][0]['readonly_reason'])
        with self.assertRaises(c.CatalogError):c.plan_control_write(total,'1809000001',0)

    def test_label_colour_is_derived_status_not_generic_boolean_write(self):
        for module in (1,11,16,17,18,20):
            j=inventory(0,0xfc,module)
            cat=c.normalize_inventory(j)
            self.assertIn('derived',cat['controls'][0]['readonly_reason'])
            key=bytes((0xfc,module,0,0,1)).hex()
            with self.assertRaises(c.CatalogError):c.plan_control_write(cat,key,1)

    def test_type2_routes_require_explicit_int32_domain(self):
        j=inventory(2,0,1);key='0001000001'
        cat=c.normalize_inventory(j)
        with self.assertRaises(c.CatalogError): c.plan_control_write(cat,key,1)
        j['controllers'][0]['allowed_values']=[-1,0,1024]
        cat=c.normalize_inventory(j)
        self.assertEqual(c.plan_control_write(cat,key,-1)['payload_hex'],'ffffffff')
        for bad in (1,True,1024.0):
            with self.assertRaises(c.CatalogError): c.plan_control_write(cat,key,bad)

    def test_type0_integer_response_overrides_boolean_enum(self):
        j=inventory(0,0x11,1);raw,points=seed(((0,0.),(255,6.)),curve=0)
        j['definitions'][0]['response_seed_raw_hex']=raw;j['definitions'][0]['response_points']=points
        dr=bytearray.fromhex(j['controllers'][0]['definition_raw_hex']);struct.pack_into('<I',dr,50,0x500000)
        j['controllers'][0]['definition_raw_hex']=j['definitions'][0]['raw_hex']=dr.hex()
        cat=c.normalize_inventory(j)
        p=c.plan_control_write(cat,'1101000001',6)
        self.assertEqual(p['payload_hex'],'0000c040')
        self.assertEqual(p['allowed_values'],list(range(7)))
        self.assertEqual(p['value_domain_source'],'native_integer_response')
        for bad in (7,0.5):
            with self.assertRaises(c.CatalogError): c.plan_control_write(cat,'1101000001',bad)

    def test_discrete_contradictory_default_fails_closed(self):
        j=inventory(0,0xfb,17)
        j['definitions'][0].pop('response_seed_raw_hex',None);j['definitions'][0].pop('response_points',None)
        raw=bytearray.fromhex(j['controllers'][0]['definition_raw_hex']);struct.pack_into('<f',raw,1,-1.)
        j['controllers'][0]['definition_raw_hex']=j['definitions'][0]['raw_hex']=raw.hex()
        cat=c.normalize_inventory(j)
        self.assertIn('contradicts',cat['controls'][0]['readonly_reason'])

    def test_absent_control_cannot_be_guessed_from_a_definition(self):
        cat=c.normalize_inventory(inventory())
        with self.assertRaises(c.CatalogError): c.plan_control_write(cat,'210c000101',0)

    def test_unknown_console_dependent_enum_stays_read_only(self):
        j=inventory(0,4,4,1);key='0404010001'
        j['definitions'][0].pop('response_seed_raw_hex',None);j['definitions'][0].pop('response_points',None)
        # EQ shape default must itself be in the native enum.
        raw=bytearray.fromhex(j['controllers'][0]['definition_raw_hex']);struct.pack_into('<f',raw,1,1.)
        j['controllers'][0]['definition_raw_hex']=j['definitions'][0]['raw_hex']=raw.hex()
        cat=c.normalize_inventory(j)
        with self.assertRaises(c.CatalogError): c.plan_control_write(cat,key,1)
        j['console_type']=14
        self.assertEqual(c.plan_control_write(c.normalize_inventory(j),key,4)['payload_hex'],'00008040')


if __name__=='__main__':
    unittest.main()

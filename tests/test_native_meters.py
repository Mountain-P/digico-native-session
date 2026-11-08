"""Copied meter-record vectors; no PowerShell/VM/device/audio execution."""
import copy
import importlib.util
from pathlib import Path
import struct
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('meter_catalog',ROOT/'skills/digico-native-session/scripts/meter_catalog.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def scalar(value):return m.scalar(struct.pack('<f',value).hex())


def snapshot():
    return {'schema_version':1,'executable_sha256':m.TARGET_SHA256,
            'memory_read_only':True,'structure_verified':True,'network_used':False,
            'ui_used':False,'physical_audio_verified':False,'values_atomic':False,
            'manager':{'vmt_rva':m.MANAGER_VMT_RVA,'registered_count':2,
                       'stream_count':1,'active':True,'synthetic_cycle_active':False,
                       'meter_standard':0,'reference_level':16},
            'matching_count':2,'returned_count':2,'truncated':False,
            'meters':[{'registry_index':i,'stream_index':0,'raw_id_hex':bytes((0x20,1,0,i,1)).hex(),
                'channel_id':256+i,'raw_type':3,'audio_id_raw_hex':'0102030405060708',
                'name':'synthetic meter','meter_type':scalar(1),'output':scalar(195),
                'secondary':scalar(200),'ordinary_current':scalar(1)} for i in range(2)]}


class RawMeterVectorTests(unittest.TestCase):
    def test_category9_values_are_unsigned_words_not_float(self):
        self.assertEqual(m.category9_words('ffff0080'),{'word0':65535,'word1':32768,'signed_interpretation':False})
        self.assertEqual(m.category9_words('c800c300')['word1'],195)
        with self.assertRaises(m.MeterError):m.category9_words('ff')

    def test_streamer_mapping_is_index_plus_two_bytes(self):
        self.assertEqual(m.streamer_word('80640002'),{'stream_index':512,'level_code':128,'secondary_code':100})
        self.assertEqual(m.streamer_word('ffff0000'),{'stream_index':0,'level_code':255,'secondary_code':255})

    def test_float_meter_code_is_numeric_float_not_bitcast_integer(self):
        value=m.scalar('00004343') # f32 195, as stored by fild/fstp in UpdatedMeter.
        self.assertEqual(value['native_code'],195)
        self.assertEqual(value['float32'],195.)
        self.assertNotEqual(value['native_code'],int.from_bytes(bytes.fromhex(value['raw_hex']),'little'))

    def test_nonfinite_and_fractional_values_remain_raw_unknown(self):
        for raw in ('0000807f','0000c07f'):
            value=m.scalar(raw);self.assertFalse(value['finite']);self.assertIsNone(value['native_code'])
        self.assertIsNone(m.scalar(struct.pack('<f',1.5).hex())['native_code'])
        self.assertIsNone(m.scalar(struct.pack('<f',-1).hex())['native_code'])

    def test_native_standard0_conversion_is_scoped_and_not_physical_dbfs(self):
        for code,wanted in ((255,0.),(195,-30.),(135,-60.)):
            value=m.standard0_display(code,1)
            self.assertEqual(value['value'],wanted)
            self.assertFalse(value['physical_dbfs_verified'])
        for code,kind in ((0,1),(1,1),(256,1),(195,4),(195,13),(195,True)):
            self.assertIsNone(m.standard0_display(code,kind))


class SnapshotTests(unittest.TestCase):
    def test_registry_alias_mapping_and_source_fields_are_separate(self):
        source=snapshot();original=copy.deepcopy(source)
        out=m.normalize_meter_snapshot(source)
        self.assertEqual(len(out['meters']),2)
        self.assertEqual(out['meters'][0]['stream_index'],out['meters'][1]['stream_index'])
        meter=out['meters'][0]
        self.assertEqual(meter['output']['native_code'],195)
        self.assertEqual(meter['ordinary_current']['native_code'],1)
        self.assertEqual(meter['display']['value'],-30.)
        self.assertEqual(out['freshness'],'not_observable')
        self.assertFalse(out['physical_audio_verified']);self.assertFalse(out['values_atomic'])
        self.assertEqual(source,original)

    def test_synthetic_cycle_is_preserved_without_audio_claim(self):
        source=snapshot();source['manager']['synthetic_cycle_active']=True
        out=m.normalize_meter_snapshot(source)
        self.assertTrue(out['manager']['synthetic_cycle_active'])
        self.assertFalse(out['physical_audio_verified'])

    def test_unknown_standard_or_meter_kind_has_no_db_conversion(self):
        source=snapshot();source['manager']['meter_standard']=6
        self.assertIsNone(m.normalize_meter_snapshot(source)['meters'][0]['display'])
        source=snapshot();source['meters'][0]['meter_type']=scalar(13)
        self.assertIsNone(m.normalize_meter_snapshot(source)['meters'][0]['display'])

    def test_wrong_target_mutability_and_audio_claim_are_rejected(self):
        for field,value in (('executable_sha256','0'*64),('memory_read_only',False),
                            ('network_used',True),('physical_audio_verified',True),
                            ('schema_version',True)):
            source=snapshot();source[field]=value
            with self.assertRaises(m.MeterError):m.normalize_meter_snapshot(source)

    def test_bad_counts_duplicates_and_stale_identity_are_rejected(self):
        source=snapshot();source['returned_count']=1
        with self.assertRaises(m.MeterError):m.normalize_meter_snapshot(source)
        source=snapshot();source['meters'][1]['registry_index']=0
        with self.assertRaises(m.MeterError):m.normalize_meter_snapshot(source)
        source=snapshot();source['meters'][0]['channel_id']=257
        with self.assertRaises(m.MeterError):m.normalize_meter_snapshot(source)
        source=snapshot();source['meters'][0]['raw_type']=1
        with self.assertRaises(m.MeterError):m.normalize_meter_snapshot(source)

    def test_alias_audioid_and_integer_summary_must_agree(self):
        source=snapshot();source['meters'][1]['audio_id_raw_hex']='0902030405060708'
        with self.assertRaises(m.MeterError):m.normalize_meter_snapshot(source)
        source=snapshot();source['meters'][0]['output']['native_code']=194
        with self.assertRaises(m.MeterError):m.normalize_meter_snapshot(source)

    def test_empty_registry_is_a_valid_empty_read_not_signal_silence(self):
        source=snapshot();source['meters']=[];source['returned_count']=0;source['matching_count']=0
        source['manager'].update(registered_count=0,stream_count=0,active=False)
        out=m.normalize_meter_snapshot(source)
        self.assertEqual(out['meters'],[]);self.assertFalse(out['physical_audio_verified'])


if __name__=='__main__':unittest.main()

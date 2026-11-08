"""Synthetic raw-record readback tests; no memory, target or .ses access."""
from pathlib import Path
from copy import deepcopy
import struct
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'digico-native-session' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import session_structure as structure


class SessionStructureTests(unittest.TestCase):
    def test_fixed_record_slots_and_named_counts(self):
        raw = bytearray(123)
        struct.pack_into('<8H', raw, 2, *range(1, 9))
        struct.pack_into('<8H', raw, 0x12, *range(11, 19))
        struct.pack_into('<8H', raw, 0x22, *range(21, 29))
        struct.pack_into('<ff', raw, 0x53, 48000, 25)
        raw[0x6E:0x74] = bytes([1, 12, 8, 2, 16, 4])
        result = structure.decode_console_record(raw)
        self.assertEqual(result['input_count_slots'], list(range(1, 9)))
        self.assertEqual(result['aux_count_slots'], list(range(11, 19)))
        self.assertEqual(result['group_count_slots'], list(range(21, 29)))
        self.assertEqual((result['matrix_outputs'], result['control_groups'],
                          result['matrix_inputs'], result['graphic_eqs']), (12, 8, 16, 4))
        self.assertEqual(result['sample_frequency'], 48000)
        self.assertEqual(result['video_frame_rate'], 25)
        self.assertFalse(result['headless_structure_setter_established'])
        self.assertFalse(result['count_slot_labels_verified'])
        self.assertEqual(result['raw_hex'], raw.hex())
        self.assertEqual(result['native_count_slot_indices'], list(range(1,9)))

    def test_length_and_nonfinite_data_are_rejected(self):
        for length in (0, 122, 124):
            with self.subTest(length=length), self.assertRaises(ValueError):
                structure.decode_console_record(bytes(length))
        for value in (float('nan'), float('inf')):
            raw = bytearray(123)
            struct.pack_into('<f', raw, 0x53, value)
            with self.assertRaises(ValueError):
                structure.decode_console_record(raw)

    def test_rpm_envelope_preserves_provenance_and_decodes_only_validated_bytes(self):
        envelope={'schema_version':1,'subsystem':'native-session-structure-readonly',
            'executable_sha256':structure.TARGET_SHA256,
            'alias_slot_rva':structure.ALIAS_SLOT_RVA,'record_rva':structure.RECORD_RVA,
            'record_bytes':123,'raw_hex':bytes(123).hex(),
            'executable_hash_verified_by_caller':True,'alias_verified':True,
            'consistent_double_read':True,'memory_read_only':True,
            'ui_used':False,'network_used':False,'headless_structure_setter_established':False,
            'offset_layout':[{'name':'input_count_slots','offset':2,'bytes':16}]}
        result=structure.decode_structure_readback(envelope)
        self.assertEqual(result['input_count_slots'],[0]*8)
        self.assertEqual(result['offset_layout'],envelope['offset_layout'])
        for key,value in (('alias_slot_rva',0),('record_rva',0),('record_bytes',122),
                          ('schema_version',True),('executable_sha256','00'*32),
                          ('alias_verified',False),('consistent_double_read',False),
                          ('memory_read_only',False),('network_used',True),
                          ('raw_hex','00'*122)):
            bad=deepcopy(envelope);bad[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                structure.decode_structure_readback(bad)


if __name__ == '__main__':
    unittest.main()

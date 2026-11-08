"""Static candidate contract tests with synthetic bytes; no target execution."""
from pathlib import Path
import struct
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'digico-native-session' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import preset_plan as preset


def independent_frame(category, command, index, body):
    raw = struct.pack('<IHBHBHi', 18+len(body), 255, category, command, 0, 0x1B6, index)+body
    return raw+struct.pack('<H', sum(raw) & 65535)


class PresetPlanTests(unittest.TestCase):
    def test_store_uses_channel_id_and_group_then_optional_rename(self):
        plan = preset.plan_store_input_preset(channel_id=0x100, preset_count=3,
                                               group_name='Test', name='Synthetic')
        create, rename = plan['steps']
        self.assertEqual(bytes.fromhex(create['frame_hex']),
                         independent_frame(3, 0xC9, 0x100, b'\x04\0\0\0Test'))
        self.assertEqual(bytes.fromhex(rename['frame_hex']),
                         independent_frame(3, 0xCE, 3, b'\x09\0\0\0Synthetic'))
        self.assertEqual(create['name_source'], 'current_channel_title')
        self.assertEqual(plan['final_expected_preset_count'], 4)
        self.assertFalse(plan['runtime_acceptance_verified'])

    def test_force_mono_flag_has_its_actual_highword(self):
        plan = preset.plan_store_input_preset(channel_id=0x101, preset_count=0,
                                               force_mono=True)
        self.assertEqual(plan['steps'][0]['list_index'], 0x10101)
        self.assertEqual(bytes.fromhex(plan['steps'][0]['frame_hex']),
                         independent_frame(3, 0xC9, 0x10101, bytes(4)))

    def test_update_packs_one_channel_and_enforces_native_lock(self):
        plan = preset.plan_update_input_preset(preset_index=1, preset_count=2,
                                                channel_id=0x102, lock_flag=0)
        self.assertEqual(bytes.fromhex(plan['steps'][0]['frame_hex']),
                         independent_frame(1, 0xCC, 1, b'\x02\x01\0\0'))
        with self.assertRaises(preset.PresetPlanError):
            preset.plan_update_input_preset(preset_index=1, preset_count=2,
                                             channel_id=0x102, lock_flag=1)

    def test_recall_sets_proved_checkbox_scope_and_restores_copied_scope(self):
        prior = bytes(range(32))
        plan = preset.plan_recall_input_preset(preset_index=2, preset_count=3,
                                                channel_id=0x100,
                                                scopes=['eq', 'dynamics', 'input_gain', 'input_route'],
                                                prior_scope_hex=prior.hex())
        selected = bytearray(32)
        selected[0] = 0xB0  # EQ4/7, dynamics5.
        selected[2] = 2     # Gain17.
        selected[16] = 1    # Input route128.
        self.assertEqual(bytes.fromhex(plan['steps'][0]['frame_hex']),
                         independent_frame(2, 0xD3, 0, b'\x20\0\0\0'+selected))
        self.assertEqual(bytes.fromhex(plan['steps'][1]['frame_hex']),
                         independent_frame(1, 0xC8, 2, b'\0\x01\0\0'))
        self.assertEqual(bytes.fromhex(plan['steps'][2]['frame_hex']),
                         independent_frame(2, 0xD3, 0, b'\x20\0\0\0'+prior))
        self.assertEqual(plan['final_expected_preset_count'], 3)

    def test_scope_translation_is_not_a_guessed_all_modules_range(self):
        self.assertEqual(preset.scope_bytes(['input']), b'\x02\x0a'+bytes(30))
        tidal = preset.scope_bytes(['tidal_preamp', 'tidal_eq', 'tidal_comp', 'tidal_gate'])
        self.assertEqual(tidal, bytes(3)+b'\x7c'+bytes(28))
        for scopes in ([], ['eq', 'eq'], ['insert'], ['unknown'], 'eq'):
            with self.subTest(scopes=scopes), self.assertRaises(preset.PresetPlanError):
                preset.scope_bytes(scopes)

    def test_rename_and_lock_wire_contracts(self):
        rename = preset.plan_rename_preset(preset_index=0, preset_count=1, name='A')
        self.assertEqual(bytes.fromhex(rename['steps'][0]['frame_hex']),
                         independent_frame(3, 0xCE, 0, b'\x01\0\0\0A'))
        lock = preset.plan_lock_preset(preset_index=0, preset_count=1, locked=True)
        self.assertEqual(bytes.fromhex(lock['steps'][0]['frame_hex']),
                         independent_frame(1, 0xD2, 0, b'\x01\0\0\0'))

    def test_delete_binds_explicit_lab_group_and_native_index(self):
        plan = preset.plan_delete_preset(preset_index=1, preset_count=3,
                                          group_name='Synthetic Lab', lock_flag=0)
        self.assertEqual(bytes.fromhex(plan['steps'][0]['frame_hex']),
                         independent_frame(1, 0xD1, 1, bytes(4)))
        self.assertEqual(plan['steps'][0]['expected_group_name_hex'],
                         b'Synthetic Lab'.hex())
        self.assertEqual(plan['final_expected_preset_count'], 2)
        for changes in ({'group_name': ''}, {'lock_flag': 1}, {'lock_flag': 2}):
            args = dict(preset_index=1, preset_count=3,
                        group_name='Synthetic Lab', lock_flag=0)
            args.update(changes)
            with self.subTest(changes=changes), self.assertRaises(preset.PresetPlanError):
                preset.plan_delete_preset(**args)

    def test_names_have_raw31_byte_limits_and_strict_encoding(self):
        preset.plan_store_input_preset(channel_id=0x100, preset_count=0,
                                        group_name='G'*31, name='N'*31)
        for changes in ({'group_name': 'G'*32}, {'name': 'N'*32},
                        {'name': 'Bad\0Name'}, {'name': '\u4e2d', 'encoding': 'ascii'},
                        {'name': '\u4e2d'*16, 'encoding': 'cp950'}):
            with self.subTest(changes=changes), self.assertRaises(preset.PresetPlanError):
                preset.plan_store_input_preset(channel_id=0x100, preset_count=0, **changes)
        plan = preset.plan_store_input_preset(channel_id=0x100, preset_count=0,
                                               name='\u4e2d', encoding='cp950')
        self.assertEqual(plan['steps'][1]['name_hex'], '\u4e2d'.encode('cp950').hex())

    def test_incomplete_or_wrong_scope_and_index_are_rejected(self):
        cases = [
            lambda: preset.plan_store_input_preset(channel_id=True, preset_count=0),
            lambda: preset.plan_store_input_preset(channel_id=0x500, preset_count=0),
            lambda: preset.plan_store_input_preset(channel_id=0x100, preset_count=-1),
            lambda: preset.plan_store_input_preset(channel_id=0x100, preset_count=10000),
            lambda: preset.plan_lock_preset(preset_index=0, preset_count=0, locked=True),
            lambda: preset.plan_lock_preset(preset_index=0, preset_count=1, locked=1),
            lambda: preset.plan_recall_input_preset(preset_index=0, preset_count=1,
                channel_id=0x100, scopes=['eq'], prior_scope_hex='00'),
        ]
        for case in cases:
            with self.assertRaises(preset.PresetPlanError):
                case()


if __name__ == '__main__':
    unittest.main()

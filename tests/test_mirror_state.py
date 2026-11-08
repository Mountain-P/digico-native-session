"""Synthetic passive Mirror evidence tests; no native target or network."""
from dataclasses import replace
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/digico-native-session/scripts'))
import mirror_state as m
from mirror_codec import build_packet
from native_transfer import SessionVerifyRecord, build_session_verify, build_session_verified

ID = '210c000001'
TYPES = {ID: 1}


def baseline(**extra):
    values = dict(console_record=bytes(range(123)), state_checksum=0x12345678,
                  session_reference=b'C:\\Synthetic\\Test.ses', binary_signature=bytes(range(100)),
                  mode_flag_a=0, mode_flag_b=0, baseline_id='synthetic-independent-oracle',
                  session_sha256='a' * 64)
    values.update(extra)
    return m.VerifyBaseline(**values)


def verify_record(direction=0, **extra):
    local = baseline()
    values = dict(direction_raw=direction, console_record=local.console_record,
                  state_checksum=local.state_checksum,
                  strings=(local.session_reference.lower(), b'synthetic secondary', local.binary_signature))
    values.update(extra)
    return SessionVerifyRecord(**values)


def identity(version=2126):
    return build_packet(category=0, control_id=b'\x01\0\0' + struct.pack('<H', version),
                        payload=b'\x02\x02\xff\0\x09Synthetic')


def batch(records=None, discriminator=0, opaque=0xB6):
    if records is None:
        # Independently laid out record: ID5, uninitialized byte5, flags16,
        # native four-byte value. This is not an int32-to-float inference.
        records = bytes.fromhex('210c000001a50000000020c1')
    return build_packet(category=9, control_id=struct.pack('<I', discriminator) + bytes([opaque]), payload=records)


class MirrorStateTests(unittest.TestCase):
    def test_identity_version_bound_and_opaque_role_flags(self):
        event = m.decode_state_packet(identity())
        self.assertEqual(event['name_hex'], b'Synthetic'.hex())
        self.assertEqual(event['flags_raw'], 2)
        self.assertFalse(event['role_flags_interpreted'])
        with self.assertRaises(m.MirrorStateError):
            m.decode_state_packet(identity(2242))
        with self.assertRaises(m.MirrorStateError):
            m.MirrorTrace(profile='quantum1-v2242.4')

    def test_batch_layout_preserves_both_uninitialized_bytes(self):
        event = m.decode_state_packet(batch(), control_types=TYPES)
        self.assertEqual(event['kind'], 'control_batch')
        self.assertEqual(event['header_byte11_opaque'], 0xB6)
        record = event['records'][0]
        self.assertEqual(record['id'], ID)
        self.assertEqual(record['byte5_opaque'], 0xA5)
        self.assertEqual(record['value_raw_hex'], '000020c1')
        self.assertEqual(record['typed_value'], -10.)
        self.assertFalse(record['semantic_application_supported'])
        self.assertFalse(event['completion_marker'])

    def test_meter_batch_remains_separate_from_control_store(self):
        trace = m.MirrorTrace(control_types=TYPES)
        events = trace.feed('peer_to_client', batch(discriminator=1))
        self.assertEqual(events[0]['kind'], 'meter_tally_batch')
        self.assertEqual(trace.snapshot()['observed_control_count'], 0)

    def test_unknown_type_and_unknown_flags_never_populate_control_store(self):
        trace = m.MirrorTrace()
        trace.feed('peer_to_client', batch())
        self.assertEqual(trace.snapshot()['observed_control_count'], 0)
        trace = m.MirrorTrace(control_types=TYPES)
        payload = bytes.fromhex('210c000001a50100000020c1')
        event = trace.feed('peer_to_client', batch(payload))[0]
        self.assertEqual(event['records'][0]['unknown_flags_raw'], 1)
        self.assertEqual(trace.snapshot()['unsupported_flag_record_count'], 1)
        self.assertEqual(trace.snapshot()['observed_control_count'], 0)

    def test_category8_needs_type_binding_and_retains_native_float_bits(self):
        frame = build_packet(category=8, control_id=bytes.fromhex(ID), payload=bytes.fromhex('000020c1'))
        unknown = m.decode_state_packet(frame)
        self.assertIsNone(unknown['typed_value'])
        self.assertIn('value_opaque_hex', unknown)
        typed = m.decode_state_packet(frame, control_types=TYPES)
        self.assertEqual(typed['typed_value'], -10.)
        self.assertEqual(typed['value_raw_hex'], '000020c1')
        malformed = build_packet(category=8, control_id=bytes.fromhex(ID), payload=b'\0' * 8)
        with self.assertRaises(m.MirrorStateError):
            m.decode_state_packet(malformed, control_types=TYPES)

    def test_string_values_validate_extent_and_preserve_encoding_bytes(self):
        key = 'ff01000001'
        value = '合成'.encode('cp950')
        frame = build_packet(category=8, control_id=bytes.fromhex(key), payload=struct.pack('<I', len(value)) + value)
        event = m.decode_state_packet(frame, control_types={key: 4})
        self.assertEqual(event['string_value_hex'], value.hex())

    def test_all_tcp_split_positions_and_coalesced_frames(self):
        stream = identity() + batch()
        for split in range(len(stream) + 1):
            trace = m.MirrorTrace(control_types=TYPES)
            events = trace.feed('peer_to_client', stream[:split])
            events += trace.feed('peer_to_client', stream[split:])
            trace.finish('peer_to_client')
            self.assertEqual([event['kind'] for event in events], ['identity', 'control_batch'])
            self.assertEqual(trace.snapshot()['observed_control_count'], 1)
            self.assertFalse(trace.snapshot()['initial_sync_complete'])

    def test_corruption_is_permanent_and_retains_prior_valid_event(self):
        damaged = bytearray(batch())
        damaged[-1] ^= 1
        trace = m.MirrorTrace(control_types=TYPES)
        with self.assertRaises(m.MirrorStateError) as caught:
            trace.feed('peer_to_client', identity() + damaged)
        self.assertEqual([event['kind'] for event in caught.exception.valid_events], ['identity'])
        self.assertTrue(trace.snapshot()['observations_stale'])
        with self.assertRaises(m.MirrorStateError):
            trace.feed('peer_to_client', batch())
        trace.begin_connection()
        trace.feed('peer_to_client', batch())

    def test_truncated_stream_and_local_byte_bound(self):
        trace = m.MirrorTrace()
        trace.feed('peer_to_client', identity()[:-1])
        with self.assertRaises(m.MirrorStateError):
            trace.finish('peer_to_client')
        trace = m.MirrorTrace(max_stream_bytes=20)
        with self.assertRaises(m.MirrorStateError):
            trace.feed('peer_to_client', identity())

    def test_compatibility_is_evaluated_before_current_surface_and_not_complete(self):
        result = m.evaluate_session_verify(verify_record(), baseline())
        self.assertTrue(result['eligible_under_recovered_predicates'])
        self.assertFalse(result['current_surface_values_compared'])
        self.assertFalse(result['initial_sync_complete'])
        trace = m.MirrorTrace(control_types=TYPES, baseline=baseline())
        request = build_session_verify(verify_record(direction=1))
        reply = build_session_verified(accepted=1, direction=1)
        trace.feed('peer_to_client', request)
        trace.feed('client_to_peer', reply)
        self.assertEqual(trace.snapshot()['expected_current_state_direction'], 'peer_to_client')
        trace.feed('peer_to_client', batch())
        self.assertEqual(trace.snapshot()['phase'], 'current_state_data_observed')
        self.assertFalse(trace.snapshot()['initial_sync_complete'])

    def test_direction_zero_current_state_comes_from_verify_receiver(self):
        trace = m.MirrorTrace(control_types=TYPES)
        trace.feed('client_to_peer', build_session_verify(verify_record(direction=0)))
        trace.feed('peer_to_client', build_session_verified(accepted=1, direction=0))
        self.assertEqual(trace.snapshot()['expected_current_state_direction'], 'peer_to_client')

    def test_every_meaningful_predicate_can_independently_reject(self):
        cases = [
            replace(verify_record(), state_checksum=0),
            replace(verify_record(), console_record=b'\xff' + verify_record().console_record[1:]),
            replace(verify_record(), console_record=verify_record().console_record[:111] + b'\xff' + verify_record().console_record[112:]),
            replace(verify_record(), strings=(b'c:\\different.ses', b'ignored', baseline().binary_signature)),
            replace(verify_record(), strings=(baseline().session_reference.lower(), b'ignored', bytes(100))),
        ]
        for record in cases:
            self.assertFalse(m.evaluate_session_verify(record, baseline())['eligible_under_recovered_predicates'])
        self.assertFalse(m.evaluate_session_verify(verify_record(), baseline(mode_flag_a=1, mode_flag_b=1))['eligible_under_recovered_predicates'])

    def test_signature_wire_lengths_are_pascal_bounded_not_fixed_to_source_100(self):
        for length in (0, 1, 99, 100, 102, 254, 255):
            wire = bytes((index * 73) % 256 for index in range(length))
            local = baseline(binary_signature=wire)
            record = replace(verify_record(), strings=(verify_record().strings[0], b'synthetic secondary', wire))
            result = m.evaluate_session_verify(record, local)
            self.assertTrue(result['eligible_under_recovered_predicates'], length)
            self.assertEqual(local.binary_signature, wire)
            self.assertEqual(result['signature_byte_lengths'], {'received': length, 'baseline': length})
            event = m.decode_state_packet(build_session_verify(record))
            self.assertEqual(event['verify_record']['strings_hex'][2], wire.hex())
        with self.assertRaisesRegex(m.MirrorStateError, 'no truncation'):
            baseline(binary_signature=bytes(256))

    def test_utf8_rtl_fixture_compares_102_wire_bytes_and_rejects_raw_source_or_padding(self):
        # Independent Windows RTL fixture: the first source pair e9/ff are
        # Unicode codepoints e-acute/y-diaeresis. CP_UTF8 wire bytes expand to
        # c3 a9 c3 bf; the remaining 98 explicit NUL codepoints stay lengthful.
        source_codepoints = bytes.fromhex('e9ff') + bytes(98)
        wire = bytes.fromhex('c3a9c3bf') + bytes(98)
        self.assertEqual((len(source_codepoints), len(wire)), (100, 102))
        record = replace(verify_record(), strings=(verify_record().strings[0], b'synthetic secondary', wire))
        self.assertTrue(m.evaluate_session_verify(record, baseline(binary_signature=wire))['checks']['binary_signature'])
        for wrong in (source_codepoints, wire[:100], wire + b'\0'):
            comparison = m.evaluate_session_verify(record, baseline(binary_signature=wrong))
            self.assertFalse(comparison['checks']['binary_signature'])
        payload = record.payload_bytes()
        prefix = 128 + 1 + len(record.strings[0]) + 1 + len(record.strings[1])
        self.assertEqual(payload[prefix], 102)
        self.assertEqual(payload[prefix + 1:], wire)
        damaged = bytearray(payload); damaged[prefix] = 103
        frame = build_packet(category=4, control_id=b'\x06\0\0\0\0', payload=damaged)
        with self.assertRaises(m.MirrorStateError):
            m.decode_state_packet(frame)

    def test_native_comparison_ignores_drive_letter_secondary_and_uncompared_console_bytes(self):
        record = verify_record()
        console = bytearray(record.console_record)
        console[50] ^= 0xFF
        changed = replace(record, console_record=bytes(console),
                          strings=(b'd' + record.strings[0][1:], b'another secondary', record.strings[2]))
        self.assertTrue(m.evaluate_session_verify(changed, baseline())['eligible_under_recovered_predicates'])
        # Only the local reference is lowercased by the recovered receiver.
        changed = replace(record, strings=(baseline().session_reference, record.strings[1], record.strings[2]))
        self.assertFalse(m.evaluate_session_verify(changed, baseline())['eligible_under_recovered_predicates'])

    def test_reply_without_matching_request_wrong_direction_or_unproven_acceptance_fails(self):
        with self.assertRaises(m.MirrorStateError):
            m.MirrorTrace().feed('peer_to_client', build_session_verified(accepted=1, direction=0))
        trace = m.MirrorTrace()
        trace.feed('client_to_peer', build_session_verify(verify_record(direction=0)))
        with self.assertRaises(m.MirrorStateError):
            trace.feed('peer_to_client', build_session_verified(accepted=1, direction=1))
        trace = m.MirrorTrace()
        trace.feed('peer_to_client', build_session_verify(verify_record(direction=1)))
        with self.assertRaises(m.MirrorStateError):
            trace.feed('client_to_peer', build_session_verified(accepted=1, direction=1))

    def test_header_opaque_byte11_is_preserved_but_initialized_fields_and_flags_are_strict(self):
        record = verify_record()
        frame = build_packet(category=4, control_id=b'\x06\0\0\0\xa5', payload=record.payload_bytes())
        self.assertEqual(m.decode_state_packet(frame)['header_byte11_opaque'], 0xA5)
        wrong = build_packet(category=4, control_id=b'\x06\0\x01\0\xa5', payload=record.payload_bytes())
        with self.assertRaises(m.MirrorStateError):
            m.decode_state_packet(wrong)
        wrong = build_packet(category=4, control_id=b'\x07\0\0\0\0', payload=b'\x01\0\x01\0')
        with self.assertRaises(m.MirrorStateError):
            m.decode_state_packet(wrong)

    def test_reconnect_invalidates_old_values_pending_verify_and_local_slot_identity(self):
        trace = m.MirrorTrace(control_types=TYPES)
        trace.feed('peer_to_client', batch())
        trace.feed('client_to_peer', build_session_verify(verify_record()))
        trace.disconnect()
        self.assertTrue(trace.snapshot()['observations_stale'])
        trace.begin_connection()
        self.assertEqual(trace.snapshot()['observed_control_count'], 0)
        self.assertEqual(trace.snapshot()['connection_epoch'], 2)
        with self.assertRaises(m.MirrorStateError):
            trace.feed('peer_to_client', build_session_verified(accepted=1, direction=0))

    def test_unknown_message_stays_opaque_and_batch_extent_is_checked(self):
        unknown = build_packet(category=42, control_id=bytes(5), payload=b'\0\0')
        with self.assertRaises(m.MirrorStateError):
            m.decode_state_packet(unknown)
        trace = m.MirrorTrace()
        self.assertEqual(trace.feed('peer_to_client', unknown)[0]['kind'], 'opaque')
        self.assertEqual(trace.snapshot()['opaque_message_count'], 1)
        for frame in (batch(bytes(13)), batch(discriminator=2)):
            with self.assertRaises(m.MirrorStateError):
                m.decode_state_packet(frame)


if __name__ == '__main__':
    unittest.main()

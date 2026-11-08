"""Synthetic independent-wire tests; never use a VM, socket or private capture."""
from dataclasses import replace
from pathlib import Path
import struct
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'digico-native-session' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import native_transfer as transfer
from mirror_codec import ChecksumError


def independent_frame(subcommand, payload, *, file_type=0, command_id=0, category=4,
                      header_byte9_raw=0):
    payload += bytes(max(0, 2-len(payload)))
    prefix = struct.pack('<IHBBBBHi', 18+len(payload), 255, category,
                         subcommand, file_type, header_byte9_raw, command_id, 0)
    body = prefix + payload
    return body + (sum(body) & 65535).to_bytes(2, 'little')


def file_record(**changes):
    return replace(transfer.FileRecord(
        n_block=0, n_blocks=2, block_size=3, file_type=3, command_id=0,
        unique_id=0x1234, error_code=0, timeout_count=0xFFFFFFF0,
        filename=b'C:\\Synthetic.ses', data=b'ABC',
    ), **changes)


class NativeTransferTests(unittest.TestCase):
    def test_request_empty_allocator_padding_and_explicit_raw_filename(self):
        self.assertEqual(transfer.build_file_request(),
                         independent_frame(1, b'\0'))
        value = b'C:\\Synthetic.ses'
        raw = transfer.build_file_request(value, file_type=3, command_id=0x19B)
        self.assertEqual(raw, independent_frame(1, bytes([len(value)])+value,
                                                file_type=3, command_id=0x19B))
        decoded = transfer.decode_transfer_packet(raw)
        self.assertEqual((decoded.subcommand, decoded.file_type, decoded.command_id,
                          decoded.string_bytes), (1, 3, 0x19B, value))
        self.assertFalse(decoded.as_dict()['runtime_acceptance_verified'])

    def test_block_layout_has_150_prefix_and_exact_data(self):
        record = file_record()
        raw = transfer.build_file_block(record)
        prefix = struct.pack('<iiHHHHHI', 0, 2, 3, 3, 0, 0x1234, 0, 0xFFFFFFF0)
        name_field = bytes([len(record.filename)])+record.filename+bytes(127-len(record.filename))
        self.assertEqual(raw, independent_frame(2, prefix+name_field+b'ABC', file_type=3))
        self.assertEqual(len(raw), 16+150+3+2)
        self.assertEqual(raw[16+22], len(record.filename))
        self.assertEqual(raw[16+150:-2], b'ABC')
        self.assertEqual(transfer.decode_transfer_packet(raw).file_record, record)

    def test_maximum_block_and_short_final_block(self):
        full = file_record(block_size=16384, data=bytes(16384))
        self.assertEqual(len(transfer.build_file_block(full)), 16552)
        final = file_record(n_block=1, block_size=1, data=b'Z')
        decoded = transfer.decode_transfer_packet(transfer.build_file_block(final))
        self.assertEqual((decoded.file_record.n_block, decoded.file_record.data), (1, b'Z'))

    def test_ack_preserves_original_block_index_and_size_without_data(self):
        final = file_record(n_block=1)
        raw = transfer.build_file_ack(final)
        self.assertEqual(len(raw), 168)
        decoded = transfer.decode_transfer_packet(raw)
        self.assertEqual(decoded.subcommand, 3)
        self.assertEqual((decoded.file_record.n_block, decoded.file_record.n_blocks,
                          decoded.file_record.block_size, decoded.file_record.data),
                         (1, 2, 3, b''))
        # ACK success increments peer state elsewhere, never this wire n_block.
        self.assertEqual(struct.unpack_from('<i', raw, 16)[0], 1)

    def test_error_ack_keeps_numeric_error_and_no_auto_action(self):
        record = file_record(n_block=2, error_code=8)
        decoded = transfer.decode_transfer_packet(transfer.build_file_ack(record))
        self.assertEqual((decoded.file_record.error_code, decoded.file_record.n_block), (8, 2))
        self.assertNotIn('retry', decoded.as_dict())

    def test_ack_copies_native_unspecified_header_byte9(self):
        # Runtime observed0x23, but disassembly proves BF3FEC/allocator do not
        # initialize byte9; BF3B30 copies it, so it is not a fixed '#' marker.
        record = file_record(n_block=1)
        for byte9 in (0, 0x23, 0xA5):
            with self.subTest(byte9=byte9):
                raw = transfer.build_file_block(record, header_byte9_raw=byte9)
                self.assertEqual(raw, independent_frame(2, record.prefix_bytes()+record.data,
                                                        file_type=3, header_byte9_raw=byte9))
                block = transfer.decode_transfer_packet(raw)
                ack = transfer.build_ack_for_block(block)
                self.assertEqual(ack, independent_frame(3, record.prefix_bytes(),
                                                        file_type=3, header_byte9_raw=byte9))
                self.assertEqual(ack[7:12], bytes([3, 3, byte9, 0, 0]))
                self.assertEqual(block.as_dict()['header_byte9_raw'], byte9)
                self.assertEqual(transfer.decode_transfer_packet(ack).file_record.n_block, 1)

    def test_ack_does_not_copy_mismatched_meaningful_control_fields(self):
        record = file_record()
        for control in (b'\x02\x02\x23\0\0', b'\x02\x03\x23\x01\0',
                        b'\x04\x03\x23\0\0', b'\x02\x03\x23\0'):
            with self.subTest(control=control), self.assertRaises(transfer.TransferCodecError):
                transfer.build_file_ack(record, source_control_id=control)

    def test_filename_padding_is_preserved_but_never_decoded_as_name(self):
        record = file_record(filename=b'A', filename_padding=bytes([0xA5])*126)
        decoded = transfer.decode_transfer_packet(transfer.build_file_block(record)).file_record
        self.assertEqual(decoded.filename, b'A')
        self.assertEqual(decoded.filename_padding, bytes([0xA5])*126)
        self.assertEqual(decoded.prefix_bytes(), record.prefix_bytes())

    def test_file_status_is_uint32_byte_length_not_shortstring_on_wire(self):
        raw = transfer.build_file_status(b'Synthetic', file_type=2, command_id=421)
        self.assertEqual(raw, independent_frame(4, b'\x09\0\0\0Synthetic',
                                                file_type=2, command_id=421))
        self.assertEqual(transfer.decode_transfer_packet(raw).string_bytes, b'Synthetic')

    def test_complete_has_only_allocator_two_byte_body(self):
        raw = transfer.build_file_complete()
        self.assertEqual(raw, independent_frame(5, b''))
        self.assertEqual(transfer.decode_transfer_packet(raw).subcommand, 5)

    def test_verify_offsets_and_three_raw_shortstrings(self):
        record = transfer.SessionVerifyRecord(1, bytes(range(123)), 0xDEADBEEF,
                                               (b'Synthetic.ses', b'Raw\xA4\x40', b'Feature'))
        raw = transfer.build_session_verify(record)
        body = raw[16:-2]
        self.assertEqual(body[0], 1)
        self.assertEqual(body[1:124], bytes(range(123)))
        self.assertEqual(body[124:128], b'\xef\xbe\xad\xde')
        self.assertEqual(body[128:], b'\x0dSynthetic.ses\x05Raw\xA4\x40\x07Feature')
        decoded = transfer.decode_transfer_packet(raw)
        self.assertEqual(decoded.verify_record, record)
        self.assertFalse(decoded.as_dict()['verify_record']['acceptance_conditions_evaluated'])

    def test_verify_keeps_unspecified_header_byte11_without_command_execution(self):
        record = transfer.SessionVerifyRecord(1, bytes(123), 0, (b'', b'', b''))
        for byte11 in (0, 0x23, 0xA5):
            raw = independent_frame(6, record.payload_bytes(), command_id=byte11 << 8)
            decoded = transfer.decode_transfer_packet(raw)
            self.assertEqual(decoded.verify_record, record)
            self.assertEqual(decoded.packet.control_id.raw[4], byte11)

    def test_verified_is_acceptance_then_direction_with_reserved_tail(self):
        raw = transfer.build_session_verified(accepted=1, direction=0)
        self.assertEqual(raw, independent_frame(7, b'\x01\0\0\0'))
        decoded = transfer.decode_transfer_packet(independent_frame(7, b'\x02\x03\xaa\xbb'))
        self.assertEqual((decoded.accepted_raw, decoded.direction_raw,
                          decoded.verified_reserved_hex), (2, 3, 'aabb'))

    def test_builders_reject_invalid_input_before_any_transport_exists(self):
        cases = [
            lambda: transfer.build_file_request(bytes(256)),
            lambda: file_record(filename=bytes(128)),
            lambda: file_record(block_size=16385),
            lambda: file_record(n_blocks=-1),
            lambda: file_record(unique_id=True),
            lambda: file_record(filename_padding=b'wrong'),
            lambda: transfer.build_file_block(file_record(data=b'AB')),
            lambda: transfer.build_file_status(bytes(256)),
            lambda: transfer.build_file_request(b'A', file_type=256),
            lambda: transfer.build_file_request(b'A', command_id=65536),
            lambda: transfer.build_file_block(file_record(), header_byte9_raw=256),
            lambda: transfer.SessionVerifyRecord(0, bytes(122), 0, (b'', b'', b'')),
            lambda: transfer.SessionVerifyRecord(0, bytes(123), 0, (b'', b'')),
            lambda: transfer.build_session_verified(accepted=2, direction=0),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(transfer.TransferCodecError):
                case()

    def test_parser_rejects_structural_conflicts_and_trailing_bytes(self):
        prefix = file_record().prefix_bytes()
        verify = transfer.SessionVerifyRecord(0, bytes(123), 0, (b'', b'', b''))
        cases = [
            independent_frame(1, b'\x03A'),
            independent_frame(1, b'\x01Aextra'),
            independent_frame(2, prefix+b'AB', file_type=3),
            independent_frame(2, prefix+b'ABC', file_type=2),
            independent_frame(2, prefix+b'ABC', file_type=3, command_id=1),
            independent_frame(3, prefix+b'X', file_type=3),
            independent_frame(3, prefix[:149], file_type=3),
            independent_frame(4, b'\x02\0\0\0A'),
            independent_frame(5, b'\0\1'),
            independent_frame(6, verify.payload_bytes()+b'X'),
            independent_frame(6, verify.payload_bytes()[:-1]),
            independent_frame(7, b'\x01\0'),
            independent_frame(0, b'\0\0'),
            independent_frame(1, b'\0\0', category=3),
        ]
        for raw in cases:
            with self.subTest(raw=raw[:18].hex()), self.assertRaises(transfer.TransferCodecError):
                transfer.decode_transfer_packet(raw)

    def test_checksum_corruption_is_not_accepted(self):
        raw = bytearray(transfer.build_file_request(b'Synthetic'))
        raw[17] ^= 1
        with self.assertRaises(ChecksumError):
            transfer.decode_transfer_packet(raw)

    def test_empty_file_record_decodes_without_claiming_sequence_success(self):
        # Original ceil(file_size / 16384) yields zero for an empty file. Preserve
        # this edge case structurally; do not invent a successful completion rule.
        record = file_record(n_blocks=0, block_size=0, data=b'')
        decoded = transfer.decode_transfer_packet(transfer.build_file_block(record))
        self.assertEqual(decoded.file_record.n_blocks, 0)
        self.assertFalse(decoded.as_dict()['runtime_acceptance_verified'])

    def test_default_summary_omits_file_bytes(self):
        summary = transfer.decode_transfer_packet(transfer.build_file_block(file_record())).as_dict()
        self.assertNotIn('data_hex', summary['file_record'])
        self.assertEqual(summary['file_record']['data_bytes'], 3)


if __name__ == '__main__':
    unittest.main()

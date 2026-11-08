"""Independent synthetic SES containers; no vendor blob, show, VM or editor."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/digico-native-session/scripts'
sys.path.insert(0, str(SCRIPTS))
import ses_file as ses
import native_session as native


FILE_IDENTITIES = {
    'quantum1-v2126': (b'Q1 ', b'N', 2126),
    'quantum1-v2242-4': (b'Q1 ', b'O', 2242),
    'quantum2-v2242-4': (b'Q2 ', b'O', 2242),
    'quantum3-v2242': (b'Q3 ', b'O', 2242),
    'quantum5-v2242': (b'Q5 ', b'O', 2242),
    'quantum7-v2242': (b'SDQ', b'O', 2242),
    'quantum8-v2242': (b'Q8 ', b'O', 2242),
}
OPAQUE = (b'\x7f\x00DATA\xff', b'\x00\x00opaque\x80\x00', bytes(range(19)))


def container(profile='quantum1-v2126', sections=OPAQUE, tags=None):
    """Construct the public 456-byte outer grammar without native payloads.

    The byte pattern exercises uninterpreted header/padding preservation. These
    opaque sections deliberately do not claim to be a native-loadable Session.
    All sizes/offsets below are independent literals from the file-format spec.
    """
    model, marker, release = FILE_IDENTITIES[profile]
    header = bytearray((index * 37 + 11) % 256 for index in range(456))
    header[:26] = b'DiGiCo     ' + model + b' .SES v' + marker + b'   \r'
    struct.pack_into('<HH', header, 32, 456, release)
    header[36:159] = bytes(123)
    struct.pack_into('<H', header, 36 + 2, 12)
    struct.pack_into('<ff', header, 36 + 0x53, 48000., 25.)
    offsets = [456]
    for block in sections:
        offsets.append(offsets[-1] + len(block))
    struct.pack_into('<40I', header, 256, *(offsets + [0] * (40 - len(offsets))))
    header[416:456] = bytes(40)
    # Unrecognized section tags stay opaque; a zero tag at the EOF slot and
    # zero unused slots accompany the exact absolute offsets above.
    # Entry zero is the special zero-tagged first section; the reader searches
    # for the terminal zero starting at entry one.
    for index in range(1, len(sections)):
        header[416 + index] = tags[index] if tags is not None else 0xa0 + index
    return bytes(header) + b''.join(sections)


def with_table(data, offsets):
    changed = bytearray(data)
    struct.pack_into('<40I', changed, 256, *(offsets + [0] * (40 - len(offsets))))
    return bytes(changed)


def record(data):
    return struct.pack('<H', len(data)) + data


def current_capture(channels=()):
    return (b'\xd3\0' + struct.pack('<i', len(channels)) + b''.join(channels)
            + record(b'Synthetic Current') + bytes(range(8))
            + record(b'') + record(bytes(2)) + struct.pack('<i', 0)
            + record(b'') + record(b'') + record(bytes(81))
            + record(b'') + record(b''))


def input_channel_capture():
    modules = []
    for module_type, controls in ((12, ((0x21, -20.), (0x22, 0.))), (1, ((1, 0.),))):
        header = bytearray(58)
        struct.pack_into('<i', header, 0, module_type)
        struct.pack_into('<i', header, 44, len(controls))
        values = b''.join(bytes((parameter, module_type, 0, 0)) + struct.pack('<f', value)
                          for parameter, value in controls)
        modules.append(b'\xda\0' + record(header) + values + struct.pack('<iii', -1, -1, 0))
    metadata = bytearray(39)
    struct.pack_into('<H', metadata, 0, 0x0100)
    metadata[2:14] = b'\x0bSynthetic A'
    struct.pack_into('<i', metadata, 34, -1)
    return b'\xd5\0' + record(b'\x01') + struct.pack('<i', 2) + b''.join(modules) + record(metadata)


def snapshot_container(profile='quantum1-v2126', current=None):
    """Zero-cue Store grammar and a no-channel current capture, all handmade."""
    options = bytearray(237)
    struct.pack_into('<4i', options, 0, -1, -1, -1, 0)
    empty = (b'\xd2\0\xd2\0' + struct.pack('<4i', 0, 0, 0, 0)
             + record(options) + record(b'S' * 288) + record(b'E' * 264))
    current = current_capture() if current is None else current
    sections = [OPAQUE[0], empty, current, OPAQUE[2], b'\xdb\0' + struct.pack('<i', 0)]
    tags = [0, 0xd2, 0xd3, 0xa3, 0xdb]
    if FILE_IDENTITIES[profile][2] == 2242:
        sections.append(b'\x73\0' + struct.pack('<ii', 0, -1))
        tags.append(0x73)
    return container(profile, tuple(sections), tags=tags)


def edit_request(raw, *operations, **changes):
    return {'schema_version': 1, 'expected_sha256': hashlib.sha256(raw).hexdigest(),
            'encoding': 'ascii', 'operations': list(operations), **changes}


class SesContainerTests(unittest.TestCase):
    def test_noop_preserves_every_header_table_tag_padding_and_payload_byte(self):
        raw = container()
        original = bytearray(raw)
        session = ses.SesFile.from_bytes(original)
        original[:] = b'\0' * len(original)
        self.assertEqual(session.to_bytes(), raw)
        self.assertEqual(session.sections, OPAQUE)
        self.assertEqual(session.to_bytes()[159:256], raw[159:256])
        self.assertEqual(session.to_bytes()[416:456], raw[416:456])
        self.assertEqual(ses.SesFile.from_bytes(memoryview(raw)).to_bytes(), raw)

    def test_known_file_families_are_distinct_from_creator_executable_identity(self):
        for profile in FILE_IDENTITIES:
            with self.subTest(profile=profile):
                raw = container(profile)
                session = ses.SesFile.from_bytes(raw)
                self.assertEqual(session.build_profile, profile)
                report = session.inspect()
                self.assertEqual(report['format_profile'], profile)
                self.assertFalse(report['executable_identity_verified'])
                self.assertFalse(report['native_acceptance_verified'])
                self.assertFalse(report['editor_used'])
                self.assertFalse(report['network_used'])
                self.assertEqual(session.to_bytes(), raw)

    def test_section_summary_is_exact_and_does_not_invent_payload_semantics(self):
        raw = container()
        report = ses.SesFile.from_bytes(raw).inspect()
        self.assertEqual(report['bytes'], len(raw))
        self.assertEqual(report['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(report['structure']['sample_frequency'], 48000.)
        self.assertEqual(report['structure']['video_frame_rate'], 25.)
        self.assertFalse(report['structure']['count_slot_labels_verified'])
        offset = 456
        for index, (actual, expected) in enumerate(zip(report['sections'], OPAQUE)):
            expected_fields = {'index': index, 'tag': 0 if index == 0 else 0xa0 + index,
                               'offset': offset, 'bytes': len(expected),
                               'sha256': hashlib.sha256(expected).hexdigest()}
            for key, value in expected_fields.items():
                self.assertEqual(actual[key], value)
            offset += len(expected)
        self.assertEqual(len(report['sections']), len(OPAQUE))
        self.assertIn('semantic_coverage', report)

    def test_rejects_wrong_model_format_release_and_selected_profile(self):
        raw = container()
        changes = [(11, b'XYZ'), (21, b'X'), (34, struct.pack('<H', 2242)),
                   (32, struct.pack('<H', 455))]
        for offset, value in changes:
            with self.subTest(offset=offset):
                invalid = bytearray(raw); invalid[offset:offset + len(value)] = value
                with self.assertRaises(ValueError):
                    ses.SesFile.from_bytes(invalid)
        with self.assertRaises(ValueError):
            ses.SesFile.from_bytes(raw, build_profile='quantum5-v2242')
        for profile in ('quantum638-unresolved', '', True, 2242):
            with self.subTest(profile=profile), self.assertRaises((ValueError, TypeError)):
                ses.SesFile.from_bytes(raw, build_profile=profile)

    def test_rejects_truncated_header_and_all_invalid_offset_boundaries(self):
        raw = container(); end = len(raw)
        for length in (0, 25, 35, 159, 255, 415, 455):
            with self.subTest(length=length), self.assertRaises(ValueError):
                ses.SesFile.from_bytes(raw[:length])
        cases = {
            'missing offsets': [],
            'missing EOF': [456],
            'first offset before payload': [455, end],
            'first offset skips bytes': [457, end],
            'empty section': [456, 456, end],
            'duplicate interior offset': [456, 463, 463, end],
            'descending offsets': [456, 474, 463, end],
            'offset beyond file': [456, end + 1, end],
            'EOF before trailing bytes': [456, end - 1],
            'EOF outside file': [456, end + 1],
            'nonzero after zero sentinel': [456, 0, end],
            'uint32 oversized offset': [456, 0xffffffff],
        }
        for label, table in cases.items():
            with self.subTest(case=label), self.assertRaises(ValueError):
                ses.SesFile.from_bytes(with_table(raw, table))

    def test_all_index_slots_are_bounded_without_requiring_unused_capacity(self):
        raw = container(sections=tuple(bytes([index]) for index in range(39)))
        self.assertEqual(len(ses.SesFile.from_bytes(raw).sections), 39)
        self.assertEqual(ses.SesFile.from_bytes(raw).to_bytes(), raw)

    def test_section_tags_and_terminal_eof_cannot_disagree(self):
        raw = container()
        for index, value in ((0, 0xa0), (1, 0), (4, 0xa4)):
            with self.subTest(tag_index=index), self.assertRaises(ValueError):
                bad = bytearray(raw); bad[416 + index] = value
                ses.SesFile.from_bytes(bad)
        bad = bytearray(raw); bad[417:456] = b'\x01' * 39
        with self.assertRaises(ValueError):
            ses.SesFile.from_bytes(bad)
        # An unused offset cannot hide behind the terminal zero tag.
        bad = bytearray(raw); struct.pack_into('<I', bad, 256 + 4 * 4, 1)
        with self.assertRaises(ValueError):
            ses.SesFile.from_bytes(bad)

    def test_readonly_roundtrip_preserves_a_different_finite_frame_rate(self):
        raw = bytearray(container()); struct.pack_into('<f', raw, 36 + 0x57, 30.)
        session = ses.SesFile.from_bytes(raw)
        self.assertEqual(session.inspect()['structure']['video_frame_rate'], 30.)
        self.assertEqual(session.to_bytes(), bytes(raw))

    def test_nonfinite_structure_fields_fail_instead_of_leaking_nan_json(self):
        raw = container()
        for offset in (36 + 0x53, 36 + 0x57):
            for value in (float('nan'), float('inf'), -float('inf')):
                with self.subTest(offset=offset, value=value):
                    bad = bytearray(raw); struct.pack_into('<f', bad, offset, value)
                    with self.assertRaises(ValueError):
                        ses.SesFile.from_bytes(bad)

    def test_file_size_limit_applies_to_bytes_and_bounded_file_reads(self):
        raw = container(sections=(b'x' * 80,))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic.ses'; path.write_bytes(raw)
            with mock.patch.object(ses, 'MAX_FILE_BYTES', 512):
                with self.assertRaisesRegex(ValueError, 'bound'):
                    ses.SesFile.from_bytes(raw)
                with self.assertRaisesRegex(ValueError, 'bound'):
                    ses.SesFile.read(path)

    def test_multibyte_buffer_cannot_bypass_the_byte_limit_with_element_count(self):
        raw = container(sections=(b'x' * (2048 - 456),))
        view = memoryview(raw).cast('I')
        self.assertEqual(len(view), 512)
        self.assertEqual(view.nbytes, 2048)
        with mock.patch.object(ses, 'MAX_FILE_BYTES', 600):
            with self.assertRaises(ValueError):
                ses.SesFile.from_bytes(view)

    def test_inspection_rejects_unknown_encoding_and_does_not_change_bytes(self):
        raw = container(); session = ses.SesFile.from_bytes(raw)
        for encoding in ('ascii', 'cp950', 'utf-8'):
            json.dumps(session.inspect(encoding), allow_nan=False)
        with self.assertRaises(ValueError):
            session.inspect('replace')
        self.assertEqual(session.to_bytes(), raw)


class SesFileFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='synthetic-ses-file-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'source.ses'
        self.raw = container()
        self.source.write_bytes(self.raw)

    def cli(self, *args):
        out, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(error):
            status = ses.main(list(args))
        return status, out.getvalue(), error.getvalue()

    def native_cli(self, *args):
        out, error = io.StringIO(), io.StringIO()
        with mock.patch.object(native, 'invoke_windows', side_effect=AssertionError('file operation must not invoke an editor')), \
             mock.patch.object(native, 'target_lock', side_effect=AssertionError('file operation must not acquire a target lease')), \
             mock.patch.object(native.subprocess, 'run', side_effect=AssertionError('file operation must not invoke a VM')), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(error):
            status = native.main(list(args))
        return status, out.getvalue(), error.getvalue()


class SesFileIoAndCliTests(SesFileFixture):
    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX named pipe fixture')
    def test_named_pipe_is_rejected_without_a_blocking_buffered_open(self):
        pipe = self.root / 'synthetic-pipe.ses'
        os.mkfifo(pipe)
        # A regular buffered open would block awaiting a writer. Fail immediately
        # instead: the test creates no worker/thread and cannot hang the suite.
        with mock.patch.object(Path, 'open', side_effect=AssertionError('named pipe must not reach a blocking buffered open')):
            with self.assertRaises(ValueError):
                ses.SesFile.read(pipe)

    def test_write_new_copy_is_exact_and_preserves_source(self):
        output = self.root / 'copy.ses'
        result = ses.SesFile.read(self.source).write(output)
        self.assertEqual(output.read_bytes(), self.raw)
        self.assertEqual(self.source.read_bytes(), self.raw)
        self.assertEqual(result['sha256'], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['copy.ses', 'source.ses'])

    def test_existing_destination_and_same_source_never_get_overwritten(self):
        output = self.root / 'existing.ses'; output.write_bytes(b'synthetic existing output')
        session = ses.SesFile.read(self.source)
        for path in (self.source, output):
            with self.subTest(path=path.name), self.assertRaises(FileExistsError):
                session.write(path)
        self.assertEqual(output.read_bytes(), b'synthetic existing output')
        self.assertEqual(self.source.read_bytes(), self.raw)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['existing.ses', 'source.ses'])

    def test_failed_sync_leaves_no_published_or_partial_destination(self):
        output = self.root / 'copy.ses'
        with mock.patch.object(ses.os, 'fsync', side_effect=OSError('synthetic sync failure')):
            with self.assertRaisesRegex(OSError, 'synthetic sync failure'):
                ses.SesFile.read(self.source).write(output)
        self.assertEqual(list(self.root.iterdir()), [self.source])
        self.assertEqual(self.source.read_bytes(), self.raw)

    def test_publication_race_never_replaces_another_writer(self):
        output = self.root / 'copy.ses'
        original_link = ses.os.link
        def competing_writer(source, destination):
            Path(destination).write_bytes(b'synthetic competing writer')
            return original_link(source, destination)
        with mock.patch.object(ses.os, 'link', side_effect=competing_writer):
            with self.assertRaises(FileExistsError):
                ses.SesFile.read(self.source).write(output)
        self.assertEqual(output.read_bytes(), b'synthetic competing writer')
        self.assertEqual(self.source.read_bytes(), self.raw)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['copy.ses', 'source.ses'])

    def test_wrong_suffix_and_invalid_object_leave_no_output(self):
        output = self.root / 'copy.txt'
        with self.assertRaises(ValueError):
            ses.SesFile.read(self.source).write(output)
        self.assertFalse(output.exists())
        session = ses.SesFile.read(self.source)
        invalid = ses.SesFile(session._header, (b'',), session.build_profile)
        with self.assertRaises(ValueError):
            invalid.write(self.root / 'invalid.ses')
        self.assertEqual(list(self.root.iterdir()), [self.source])

    def test_standalone_inspect_and_clone_need_no_editor_arguments(self):
        code, out, error = self.cli('inspect', '--source', str(self.source))
        self.assertEqual(code, 0, error)
        report = json.loads(out)
        self.assertEqual(report['format_profile'], 'quantum1-v2126')
        self.assertFalse(report['editor_used'])
        self.assertFalse(report['network_used'])
        output = self.root / 'clone.ses'
        code, out, error = self.cli('clone', '--source', str(self.source), '--output', str(output))
        self.assertEqual(code, 0, error)
        self.assertEqual(output.read_bytes(), self.raw)
        self.assertEqual(self.source.read_bytes(), self.raw)
        self.assertFalse(json.loads(out)['native_acceptance_verified'])

    def test_standalone_mismatched_profile_and_existing_output_fail_closed(self):
        code, out, error = self.cli('inspect', '--source', str(self.source),
                                    '--build-profile', 'quantum5-v2242')
        self.assertEqual(code, 2)
        self.assertEqual(out, '')
        self.assertFalse(json.loads(error)['editor_used'])
        code, out, error = self.cli('clone', '--source', str(self.source), '--output', str(self.source))
        self.assertEqual(code, 2)
        self.assertEqual(out, '')
        self.assertIn('error', json.loads(error))
        self.assertEqual(self.source.read_bytes(), self.raw)

    def test_main_file_inspect_and_clone_bypass_all_editor_target_arguments(self):
        code, out, error = self.native_cli('file-inspect', '--source', str(self.source))
        self.assertEqual(code, 0, error)
        self.assertFalse(json.loads(out)['editor_used'])
        output = self.root / 'delegated-copy.ses'
        code, out, error = self.native_cli('file-clone', '--source', str(self.source), '--output', str(output))
        self.assertEqual(code, 0, error)
        self.assertEqual(output.read_bytes(), self.raw)
        self.assertEqual(self.source.read_bytes(), self.raw)
        self.assertFalse(json.loads(out)['network_used'])

    def test_main_file_command_rejects_identity_mismatch_without_target_calls(self):
        code, out, error = self.native_cli('file-clone', '--source', str(self.source),
                                          '--build-profile', 'quantum5-v2242',
                                          '--output', str(self.root / 'forbidden.ses'))
        self.assertEqual(code, 2)
        self.assertEqual(out, '')
        self.assertFalse(json.loads(error)['editor_used'])
        self.assertEqual(list(self.root.iterdir()), [self.source])


class SesEditContractTests(unittest.TestCase):
    def test_empty_edit_and_hash_rejection_happen_without_snapshot_parsing(self):
        raw = container(); session = ses.SesFile.from_bytes(raw)
        self.assertEqual(session.edit(edit_request(raw)).to_bytes(), raw)
        request = edit_request(raw, {'op': 'rename', 'index': 0, 'name': 'Synthetic A'},
                               expected_sha256='0' * 64)
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            session.edit(request)
        self.assertEqual(session.to_bytes(), raw)

    def test_request_schema_hash_encoding_and_profile_are_strict(self):
        raw = container(); session = ses.SesFile.from_bytes(raw)
        invalid = []
        for field in ('schema_version', 'expected_sha256', 'encoding', 'operations'):
            value = edit_request(raw); value.pop(field); invalid.append(value)
        invalid += [edit_request(raw, extra=True), None, [], 1]
        for value in (True, 0, 1., '1', None):
            invalid.append(edit_request(raw, schema_version=value))
        for value in ('', 'a' * 63, 'A' * 64, 'x' * 64, True, None, 1):
            invalid.append(edit_request(raw, expected_sha256=value))
        for value in ('cp1252', '', None, True):
            invalid.append(edit_request(raw, encoding=value))
        for value in ('quantum5-v2242', True, None):
            invalid.append(edit_request(raw, build_profile=value))
        for value in (0, 30, True, 25., '25', None):
            invalid.append(edit_request(raw, fps=value))
        for value in ({}, 'rename', None, True):
            invalid.append(edit_request(raw, operations=value))
        invalid.append(edit_request(raw, operations=[{'op': 'mystery_operation'}] * 1001))
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                session.edit(request)
        self.assertEqual(session.to_bytes(), raw)

    def test_create_from_same_file_current_capture_and_cp950_rename_roundtrip(self):
        raw = snapshot_container('quantum5-v2242')
        session = ses.SesFile.from_bytes(raw)
        request = edit_request(raw,
            {'op': 'create', 'index': 'append', 'name': 'Synthetic A'},
            {'op': 'rename', 'index': 0, 'name': '測試 A'},
            {'op': 'timecode', 'index': 0, 'timecode': '00:01:23:12'},
            encoding='cp950', build_profile='quantum5-v2242', fps=25)
        result = session.edit(request)
        parsed = ses.SesFile.from_bytes(result.to_bytes())
        snapshot = parsed.inspect('cp950')['snapshot_section']['snapshots'][0]
        self.assertEqual(snapshot['name_raw_hex'], '測試 A'.encode('cp950').hex())
        self.assertEqual(snapshot['recall_at'], 8348)
        self.assertTrue(snapshot['recall_at_active'])
        self.assertEqual(session.to_bytes(), raw)
        for index in (0, 2, 3):
            self.assertEqual(result.sections[index], session.sections[index])
        self.assertFalse(parsed.inspect()['native_acceptance_verified'])

    def test_invalid_later_operation_leaves_original_object_byte_exact(self):
        raw = snapshot_container(); session = ses.SesFile.from_bytes(raw)
        requests = [
            edit_request(raw, {'op': 'create', 'index': 'append', 'name': 'Synthetic A'},
                         {'op': 'mystery_operation'}),
            edit_request(raw, {'op': 'create', 'index': 'append'},
                         {'op': 'rename', 'index': 0, 'name': 'x' * 32}),
            edit_request(raw, {'op': 'create', 'index': 'append'},
                         {'op': 'timecode', 'index': 0, 'timecode': '00:00:00:25'}),
        ]
        for request in requests:
            with self.subTest(request=request), self.assertRaises(ValueError):
                session.edit(request)
            self.assertEqual(session.to_bytes(), raw)

    def test_unknown_operation_and_non_boolean_active_reject(self):
        raw = snapshot_container(); session = ses.SesFile.from_bytes(raw)
        with self.assertRaisesRegex(ValueError, 'Unsupported direct-file operation'):
            session.edit(edit_request(raw, {'op': 'mystery_operation'}))
        for active in (0, 1, 'true', None):
            request = edit_request(raw, {'op': 'create', 'index': 'append',
                                        'timecode': '00:00:00:01', 'active': active})
            with self.subTest(active=active), self.assertRaises(ValueError):
                session.edit(request)
        self.assertEqual(session.to_bytes(), raw)

    def test_nonempty_setlist_refuses_all_snapshot_index_mutations(self):
        seed = ses.SesFile.from_bytes(snapshot_container('quantum5-v2242'))
        seed = seed.edit(edit_request(seed.to_bytes(), {'op': 'create', 'index': 'append',
                                                      'name': 'Synthetic A'}))
        sections = list(seed.sections)
        list_name = b'\x09Synthetic' + bytes(22)
        sections[seed.section_tags.index(0x73)] = (b'\x73\0' + struct.pack('<i', 1)
            + list_name + struct.pack('<iii', 1, 0, 0))
        raw = container('quantum5-v2242', tuple(sections), tags=seed.section_tags)
        session = ses.SesFile.from_bytes(raw)
        for operation in ({'op': 'create', 'index': 'append'},
                          {'op': 'duplicate', 'source_index': 0, 'index': 'append'},
                          {'op': 'delete', 'index': 0}):
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, 'SetList'):
                session.edit(edit_request(raw, operation))
            self.assertEqual(session.to_bytes(), raw)
        changed = session.edit(edit_request(raw,
            {'op': 'rename', 'index': 0, 'name': 'Synthetic B'},
            {'op': 'timecode', 'index': 0, 'timecode': '00:00:01:00'}))
        self.assertEqual(changed.sections[seed.section_tags.index(0x73)],
                         session.sections[seed.section_tags.index(0x73)])

    def test_current_input_edits_are_captured_by_later_create_in_the_same_file(self):
        from ses_snapshot import SnapshotSection
        raw = snapshot_container('quantum5-v2242', current_capture((input_channel_capture(),)))
        source = ses.SesFile.from_bytes(raw)
        result = source.edit(edit_request(raw,
            {'op': 'set_channel_name', 'channel_id': 256, 'name': 'Synthetic Input B'},
            {'op': 'set_control', 'id': '210c000001', 'value': -12},
            {'op': 'set_control', 'id': '0101000001', 'value': .5},
            {'op': 'set_control', 'id': '220c000001', 'value': 1},
            {'op': 'create', 'index': 'append', 'name': 'Synthetic Cue'}))
        channels = result.inspect()['current_console']['channels']
        self.assertEqual(channels[0]['name'], 'Synthetic Input B')
        expected = {'210c00': -12., '220c00': 1., '010100': .5}
        values = {row['id'][:6]: row['value'] for row in channels[0]['basic_controls']}
        self.assertEqual(values, expected)
        captured = SnapshotSection.from_bytes(result.sections[1], version=2242).snapshots[0]
        stored = {record[:3].hex(): struct.unpack('<f', record[4:8])[0]
                  for module in captured.channels[0].modules
                  for record in (module.controls[index:index+8]
                                 for index in range(0, len(module.controls), 8))}
        self.assertEqual(stored, expected)
        self.assertEqual(source.to_bytes(), raw)
        for index in (0, 3, 4, 5):
            self.assertEqual(result.sections[index], source.sections[index])

    def test_displayed_numbers_and_fresh_integer_number_policy_preserve_raw_scaling(self):
        raw = snapshot_container(); session = ses.SesFile.from_bytes(raw)
        seeded = session.edit(edit_request(raw, {'op': 'create', 'index': 'append'},
                                          {'op': 'duplicate', 'source_index': 0, 'index': 'append'}))
        snapshots = seeded.inspect()['snapshot_section']['snapshots']
        self.assertEqual([row['number_raw'] for row in snapshots], [100, 200])
        self.assertEqual([row['number'] for row in snapshots], [1., 2.])
        changed = seeded.edit(edit_request(seeded.to_bytes(), {'op': 'number', 'index': 0, 'number': 12}))
        numbers = changed.inspect()['snapshot_section']['snapshots']
        self.assertEqual([row['number_raw'] for row in numbers], [1200, 200])
        for value in (0, -1, True, 1.5, '3', 0x7fffffff // 100 + 1, 2):
            with self.subTest(number=value), self.assertRaises(ValueError):
                seeded.edit(edit_request(seeded.to_bytes(), {'op': 'number', 'index': 0, 'number': value}))
        self.assertEqual([row['number_raw'] for row in seeded.inspect()['snapshot_section']['snapshots']], [100, 200])

    def test_locked_snapshot_rejects_rename_timecode_number_and_delete(self):
        from ses_snapshot import SnapshotSection
        seed = ses.SesFile.from_bytes(snapshot_container())
        seed = seed.edit(edit_request(seed.to_bytes(), {'op': 'create', 'index': 'append'}))
        sections = list(seed.sections)
        snapshots = SnapshotSection.from_bytes(sections[1], version=2126)
        snapshots.snapshots[0].flags.data = struct.pack('<H', 0x2000)
        sections[1] = snapshots.to_bytes()
        raw = container(sections=tuple(sections), tags=seed.section_tags)
        source = ses.SesFile.from_bytes(raw)
        for operation in ({'op': 'rename', 'index': 0, 'name': 'Synthetic B'},
                          {'op': 'timecode', 'index': 0, 'timecode': '00:00:01:00'},
                          {'op': 'number', 'index': 0, 'number': 3},
                          {'op': 'delete', 'index': 0}):
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, '[Ll]ock'):
                source.edit(edit_request(raw, operation))
            self.assertEqual(source.to_bytes(), raw)

    def test_missing_external_sections_block_advanced_but_not_name_edit(self):
        seed = ses.SesFile.from_bytes(snapshot_container('quantum5-v2242'))
        seed = seed.edit(edit_request(seed.to_bytes(), {'op': 'create', 'index': 'append'}))
        for tag in (0xd3, 0xdb, 0x73):
            with self.subTest(missing_tag=tag):
                sections = [block for block, actual in zip(seed.sections, seed.section_tags) if actual != tag]
                tags = [actual for actual in seed.section_tags if actual != tag]
                raw = container('quantum5-v2242', tuple(sections), tags=tags)
                source = ses.SesFile.from_bytes(raw)
                with self.assertRaisesRegex(ValueError, 'external reference sections'):
                    source.edit(edit_request(raw, {'op': 'delete', 'index': 0}))
                renamed = source.edit(edit_request(raw, {'op': 'rename', 'index': 0, 'name': 'Synthetic B'}))
                self.assertEqual(renamed.inspect()['snapshot_section']['snapshots'][0]['name'], 'Synthetic B')


class SesEditCliTests(SesFileFixture):
    def test_cli_edit_is_pure_file_io_and_public_wrapper_matches(self):
        self.raw = snapshot_container(); self.source.write_bytes(self.raw)
        request = self.root / 'edit.json'
        request.write_text(json.dumps(edit_request(self.raw, {'op': 'create', 'index': 'append',
                                                            'name': 'Synthetic New'})), encoding='utf-8')
        for index, invoke in enumerate((self.cli, self.native_cli)):
            output = self.root / ('edited-%d.ses' % index)
            mode = 'edit' if index == 0 else 'file-edit'
            code, out, error = invoke(mode, '--source', str(self.source), '--request', str(request),
                                      '--output', str(output))
            self.assertEqual(code, 0, error)
            self.assertEqual(self.source.read_bytes(), self.raw)
            self.assertEqual(ses.SesFile.read(output).inspect()['snapshot_section']['snapshot_count'], 1)
            self.assertFalse(json.loads(out)['editor_used'])
            self.assertFalse(json.loads(out)['network_used'])

    def test_cli_late_edit_failure_leaves_no_output(self):
        self.raw = snapshot_container(); self.source.write_bytes(self.raw)
        request = self.root / 'edit.json'
        request.write_text(json.dumps(edit_request(self.raw,
            {'op': 'create', 'index': 'append', 'name': 'Synthetic A'},
            {'op': 'mystery_operation'})), encoding='utf-8')
        output = self.root / 'forbidden.ses'
        code, out, error = self.native_cli('file-edit', '--source', str(self.source),
            '--request', str(request), '--output', str(output))
        self.assertEqual(code, 2)
        self.assertEqual(out, '')
        self.assertIn('Unsupported direct-file operation', json.loads(error)['error'])
        self.assertFalse(output.exists())
        self.assertEqual(self.source.read_bytes(), self.raw)

    def test_duplicate_request_json_keys_reject_at_every_object_depth(self):
        for request_text in (
            '{"schema_version":1,"schema_version":1}',
            '{"operations":[{"op":"rename","index":0,"index":1}]}',
        ):
            with self.subTest(request=request_text):
                request = self.root / 'duplicates.json'; request.write_text(request_text, encoding='utf-8')
                output = self.root / 'forbidden.ses'
                code, out, error = self.cli('edit', '--source', str(self.source),
                    '--request', str(request), '--output', str(output))
                self.assertEqual(code, 2)
                self.assertEqual(out, '')
                self.assertIn('Duplicate JSON field', json.loads(error)['error'])
                self.assertFalse(output.exists())
                self.assertEqual(self.source.read_bytes(), self.raw)

    def test_malformed_known_section_cannot_publish_a_clone_before_report_failure(self):
        self.raw = container(sections=OPAQUE, tags=(0, 0xd2, 0xa2))
        self.source.write_bytes(self.raw)
        output = self.root / 'forbidden.ses'
        code, out, error = self.cli('clone', '--source', str(self.source), '--output', str(output))
        self.assertEqual(code, 2)
        self.assertEqual(out, '')
        self.assertIn('error', json.loads(error))
        self.assertFalse(output.exists())
        self.assertEqual(self.source.read_bytes(), self.raw)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX named pipe fixture')
    def test_edit_request_pipe_rejects_without_blocking_or_publishing(self):
        request = self.root / 'request.json'; os.mkfifo(request)
        output = self.root / 'forbidden.ses'
        with mock.patch.object(Path, 'open', side_effect=AssertionError('request pipe must not reach a blocking buffered open')):
            code, out, error = self.native_cli('file-edit', '--source', str(self.source),
                                              '--request', str(request), '--output', str(output))
        self.assertEqual(code, 2)
        self.assertEqual(out, '')
        self.assertIn('regular file', json.loads(error)['error'])
        self.assertFalse(output.exists())
        self.assertEqual(self.source.read_bytes(), self.raw)

    def test_edit_request_bytes_are_bounded_before_json_decode(self):
        request = self.root / 'oversized.json'; request.write_text('{}' + ' ' * 1024, encoding='utf-8')
        output = self.root / 'forbidden.ses'
        with mock.patch.object(ses, 'MAX_REQUEST_BYTES', 64):
            code, out, error = self.cli('edit', '--source', str(self.source),
                '--request', str(request), '--output', str(output))
        self.assertEqual(code, 2)
        self.assertEqual(out, '')
        self.assertIn('byte bound', json.loads(error)['error'])
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()

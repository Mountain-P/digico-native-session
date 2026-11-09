"""Synthetic Q5 files exercise the installed CLI comparison entry point."""
import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/digico-native-session/scripts'))

import native_session
from ses_compare import compare_sessions
from ses_factory import create_session
from ses_file import SesFile, SesFormatError


def _seed(source, timecode, profile='quantum5-v2242'):
    return create_session({
        'schema_version': 1, 'build_profile': profile, 'encoding': 'ascii',
        'structure': {'inputs': {'mono': 2, 'stereo': 0}},
        'operations': [
            {'op': 'set_input_route', 'channel_id': 256, 'source': source},
            {'op': 'create', 'index': 'append', 'name': 'Synthetic Cue', 'timecode': timecode},
        ],
    })


def _legacy_bytes(session):
    # The fixture writes the older record widths and release, independently
    # of legacy detection. The production factory remains V2242 only.
    topology = session._topology()
    topology.version = 2025
    for group in topology.groups:
        for channel in group.channels:
            channel.metadata = channel.metadata[:88]
    raw_header = bytearray(session._header)
    raw_header[21] = ord('M')
    struct.pack_into('<H', raw_header, 34, 2025)
    sections = list(session.sections)
    sections[session._section_index(0xc9)] = topology.to_bytes()
    return SesFile(bytes(raw_header), tuple(sections), 'quantum5-v2025').to_bytes()


class SesComparisonTests(unittest.TestCase):
    def test_native_cli_compares_versions_and_preserves_both_sources(self):
        older = _legacy_bytes(_seed(0x1c00, '00:00:02:00'))
        newer = _seed(0x1c01, '00:00:03:00').to_bytes()
        with tempfile.TemporaryDirectory() as directory:
            left = Path(directory) / 'synthetic-a.ses'
            right = Path(directory) / 'synthetic-b.ses'
            left.write_bytes(older)
            right.write_bytes(newer)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(native_session.main([
                    'file-compare', '--left', str(left), '--right', str(right),
                    '--left-channel-id', '0x100', '--right-channel-id', '0x100',
                ]), 0)
            self.assertEqual((left.read_bytes(), right.read_bytes()), (older, newer))
        report = json.loads(output.getvalue())
        self.assertEqual((report['left']['release'], report['right']['release']), (2025, 2242))
        self.assertFalse(report['editor_used'])
        self.assertFalse(report['effective_recall_verified'])
        changes = report['differences']
        self.assertEqual(changes['cues']['matched'][0]['changes']['recall_at'],
                         {'left': 200, 'right': 300})
        self.assertEqual(changes['current_signal_rows'][0]['left_id'], 0x100)
        self.assertEqual(changes['current_signal_rows'][0]['different_stored_fields'], ['main_alt'])

    def test_legacy_profile_reads_and_stays_read_only(self):
        raw = _legacy_bytes(_seed(0x1c00, '00:00:02:00'))
        session = SesFile.from_bytes(raw, 'quantum5-v2025')
        self.assertEqual(session.to_bytes(), raw)
        report = session.inspect(snapshot_index=0, include_topology=True, include_routing=True,
                                 include_presets=True)
        self.assertTrue(report['read_only_profile'])
        self.assertFalse(any(control['file_write_supported']
                             for row in report['current_console']['channels']
                             for control in row['basic_controls']))
        with self.assertRaisesRegex(SesFormatError, 'read-only'):
            session.edit({})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'should-not-exist.ses'
            with self.assertRaisesRegex(SesFormatError, 'read-only'):
                session.write(output)
            self.assertFalse(output.exists())
        with self.assertRaises(SesFormatError):
            SesFile.from_bytes(raw, 'quantum5-v2242')

    def test_comparison_rejects_other_console_models(self):
        q5 = _seed(0x1c00, '00:00:02:00')
        other = _seed(0x1c00, '00:00:02:00', profile='quantum1-v2242-4')
        with self.assertRaisesRegex(SesFormatError, 'Quantum5'):
            compare_sessions(q5, other)

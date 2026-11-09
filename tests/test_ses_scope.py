"""Synthetic end-to-end Q5 Recall Scope and Safe analysis."""
import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest

from test_ses_compare import _legacy_bytes, _seed

import native_session
from ses_file import SesFile, SesFormatError
from ses_scope import inspect_scope_file


def _scope_seed(*, safe=False, global_fader=True, cue_fader=True, ignore_scope=False):
    session = _seed(0x1c00, '00:00:02:00')
    snapshots, current, topology = session._snapshots(), session._console(), session._topology()
    cue = snapshots.snapshots[0]
    cue_mask = cue.controller_scope
    offset = 11 * 32 + 4
    cue_mask.patch(offset, bytes([cue_mask.data[offset] | 2 if cue_fader
                                  else cue_mask.data[offset] & ~2]), 992)
    saved = next(channel for channel in cue.channels if channel.channel_id == 0x100)
    modules = (saved.modules if saved.own_modules.effective(1)[0]
               else [snapshots.modules[index] for index in saved.modules if index >= 0])
    for module in modules:
        if module.header.effective(58)[:4] == b'\x0c\0\0\0':
            module.header.patch(53, bytes([ignore_scope]), 58)
    group = next(group for group in topology.groups if group.group_id == 1)
    group_metadata = bytearray(group.metadata)
    group_metadata[0x21 + 1] |= 1 << 4  # module 12
    group_metadata[0x29 + 4] &= ~6  # Fader and Mute
    if global_fader:
        group_metadata[0x29 + 4] |= 2
    group.metadata = bytes(group_metadata)

    channel = next(channel for channel in current.snapshot.channels if channel.channel_id == 0x100)
    fader = next(module for module in channel.modules if module.header.effective(58)[:4] == b'\x0c\0\0\0')
    for parameter, value in ((0xf0, False), (0xf6, safe)):
        fader.controls += struct.pack('<BBBBf', parameter, 12, 0, 0, float(value))
    fader.header.patch(44, struct.pack('<i', len(fader.controls) // 8), 58)
    sections = list(session.sections)
    for tag, part in ((0xd2, snapshots), (0xd3, current), (0xc9, topology)):
        sections[session._section_index(tag)] = part.to_bytes()
    return SesFile.from_bytes(SesFile(session._header, tuple(sections), session.build_profile).to_bytes())


def _parameter_group(report, key):
    row = report['cues'][0]['channels'][0]
    return next(group for group in report['parameter_sets'][row['parameter_set']] if key in group['parameters'])


class RecallScopeWorkflowTests(unittest.TestCase):
    def test_scope_and_safe_are_separate_gates(self):
        cases = [
            ({}, 'allowed', ()),
            ({'global_fader': False}, 'scope_blocked', ('c9_group',)),
            ({'cue_fader': False}, 'scope_blocked', ('cue',)),
            ({'safe': True}, 'safe_blocked', ('fade_mute_safe',)),
            ({'global_fader': False, 'safe': True}, 'scope_blocked', ('c9_group', 'fade_mute_safe')),
            ({'cue_fader': False, 'safe': True, 'ignore_scope': True}, 'allowed', ('ignore_scope',)),
        ]
        for release in (2025, 2242):
            for settings, status, reasons in cases:
                with self.subTest(release=release, settings=settings):
                    source = _scope_seed(**settings)
                    if release == 2025:
                        source = SesFile.from_bytes(_legacy_bytes(source))
                    report = inspect_scope_file(source, snapshot_index=0, channel_id=0x100)
                    fader = _parameter_group(report, '210c00')
                    self.assertEqual((fader['status'], fader['reasons']), (status, reasons))
                    self.assertEqual(_parameter_group(report, '220c00')['status'],
                                     'allowed' if settings.get('ignore_scope') else 'scope_blocked')

    def test_cli_reports_filtered_native_ids_and_keeps_source_bytes(self):
        raw = _legacy_bytes(_scope_seed(safe=True))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'synthetic.ses'
            source.write_bytes(raw)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(native_session.main([
                    'file-recall-scope', '--source', str(source), '--snapshot-index', '0',
                    '--channel-id', '0x100',
                ]), 0)
            self.assertEqual(source.read_bytes(), raw)
        result = json.loads(output.getvalue())
        self.assertEqual(result['version'], 2025)
        self.assertEqual(result['parameters']['210c00']['name'], 'Fader')
        self.assertEqual(result['cues'][0]['channels'][0]['channel_id'], 0x100)
        self.assertFalse(result['effective_recall_verified'])

    def test_scope_channel_filter_rejects_missing_channel(self):
        with self.assertRaisesRegex(SesFormatError, 'not present'):
            inspect_scope_file(_scope_seed(), snapshot_index=0, channel_id=0x10ff)

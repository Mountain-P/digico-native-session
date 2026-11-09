import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills' / 'digico-native-session' / 'scripts'))

import native_session
from ses_factory import create_session
from ses_file import SesFile
from ses_structure import CREATION_PROFILES


class NewSessionWorkflowTests(unittest.TestCase):
    def test_create_file_then_read_routing_and_preset_through_cli(self):
        request = ROOT / 'examples' / 'file-new-quantum5.json'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.ses'
            with contextlib.redirect_stdout(io.StringIO()):
                status = native_session.main(['file-new', '--request', str(request),
                                              '--output', str(path)])
            self.assertEqual(status, 0)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = native_session.main(['file-inspect', '--source', str(path), '--routing',
                                              '--topology', '--presets', '--snapshot-index', '0'])
            self.assertEqual(status, 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report['structure']['input_count_slots'][:2], [8, 2])
            self.assertEqual(report['presets']['preset_count'], 1)
            channels = {row['channel_id']: row for row in report['routing']['channels']}
            self.assertEqual(channels[0x100]['input_routes']['main'], 0x1c000000)
            self.assertEqual(channels[0x108]['input_routes']['main'], 0x1d020000)
            self.assertEqual(channels[0x601]['output_legs']['left'], [0x4e00])
            self.assertEqual(channels[0x601]['output_legs']['right'], [0x4e01])
            self.assertIn({'aux_channel_id': 0x500, 'level': -12, 'enabled': True},
                          channels[0x100]['aux_sends'])
            self.assertEqual(channels[0x500]['physical_outputs'], [0x4e02])
            capture = report['selected_snapshot']
            self.assertEqual(capture['metadata']['recall_at'], 200)
            stored = {row['channel_id']: row for row in capture['stored_mix']['channels']}
            self.assertEqual(stored[0x100]['name'], 'Synthetic Vocal')
            levels = {item['parameter']: item['value'] for item in stored[0x101]['basic_controls']}
            self.assertEqual((levels['trim'], levels['fader']), (3, -18))
            self.assertEqual(SesFile.read(path).to_bytes(), path.read_bytes())
            self.assertFalse(report['native_acceptance_verified'])

    def test_every_enabled_profile_reopens_without_a_source(self):
        for profile in CREATION_PROFILES:
            with self.subTest(profile=profile):
                session = create_session({'schema_version': 1, 'build_profile': profile,
                                          'encoding': 'ascii', 'structure': {}})
                reopened = SesFile.from_bytes(session.to_bytes(), profile)
                self.assertEqual(session.to_bytes(), reopened.to_bytes())
                report = reopened.inspect(include_routing=True, include_presets=True)
                self.assertEqual(report['snapshot_section']['snapshot_count'], 0)
                self.assertEqual(report['presets']['preset_count'], 0)
                self.assertTrue(report['routing']['inputs'])

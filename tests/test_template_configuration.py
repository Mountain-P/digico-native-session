"""The local importer checks admission headers, never native snapshot content."""
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/digico-native-session/scripts/configure_template.py'
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location('configure_template', SCRIPT)
template = importlib.util.module_from_spec(spec)
spec.loader.exec_module(template)


class TemplateConfigurationTests(unittest.TestCase):
    def test_header_only_configuration_is_explicit_and_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'SKILL.md').write_text('test')
            source = root / 'user-owned.ses'
            data = bytearray(36)
            data[:26] = template.HEADER
            data[34:36] = (2126).to_bytes(2, 'little')
            source.write_bytes(data)
            configured = template.configure(source, root)
            self.assertFalse(configured['snapshot_count_verified'])
            self.assertEqual(configured['snapshot_count'], 0)
            self.assertEqual((root / 'assets' / configured['filename']).read_bytes(), bytes(data))
            manifest = (root / 'assets/empty-template.json').read_text()
            self.assertNotIn(str(source), manifest)
            with self.assertRaises(FileExistsError):
                template.configure(source, root)

    def test_wrong_or_truncated_header_does_not_create_template(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'SKILL.md').write_text('test')
            source = root / 'wrong.ses'
            source.write_bytes(b'not a native session')
            with self.assertRaises(ValueError):
                template.configure(source, root)
            self.assertFalse((root / 'assets').exists())


if __name__ == '__main__':
    unittest.main()

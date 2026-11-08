#!/usr/bin/env python3
"""Import a user-owned template for an admitted build; never contact an editor.

Header and hash checks do not prove that a session has zero snapshots.
new_session must still native-load this file and read back count zero and 25fps.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

from build_profiles import build_value, profile_bound

HEADER = bytes.fromhex(build_value('session_file.header_hex'))
EXECUTABLE_SHA256 = build_value('executable.sha256')


@profile_bound(feature='template', error_type=ValueError)
def configure(source, skill_root):
    source = Path(source).resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != '.ses':
        raise ValueError('source must be a regular .ses file')
    with source.open('rb') as stream:
        header = stream.read(36)
    if (len(header) != 36 or header[:26] != bytes.fromhex(build_value('session_file.header_hex'))
            or int.from_bytes(header[34:36], 'little') != build_value('release')):
        raise ValueError('source model/version header differs from the selected build profile')
    root = Path(skill_root).resolve(strict=True)
    if not (root / 'SKILL.md').is_file():
        raise ValueError('skill-root must contain SKILL.md')
    assets = root / 'assets'
    assets.mkdir(exist_ok=True)
    target = assets / build_value('session_file.template_filename')
    manifest_path = assets / build_value('session_file.manifest_filename')
    if target.exists() or manifest_path.exists():
        raise FileExistsError('template already configured; existing files were not replaced')
    # Create exclusively, then publish the manifest after a full copy.
    # No source path is stored in the manifest.
    with source.open('rb') as src, target.open('xb') as dst:
        shutil.copyfileobj(src, dst)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = {
        'schema_version': 1, 'build_profile': build_value('id'), 'filename': target.name,
        'sha256': digest, 'bytes': target.stat().st_size,
        'snapshot_count': 0, 'snapshot_count_verified': False,
        'validation': 'Header/hash only. Native new_session must read back zero snapshots and 25fps.',
        'model': build_value('session_file.model_label'),
        'file_version': build_value('session_file.file_version'),
        'executable_sha256': build_value('executable.sha256'),
    }
    with manifest_path.open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--skill-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--build-profile', default=None, help='Exact verified build profile ID')
    args = parser.parse_args()
    try:
        manifest = configure(args.source, args.skill_root, build_profile=args.build_profile)
    except (OSError, ValueError) as error:
        print(json.dumps({'error': str(error), 'network_used': False}), file=sys.stderr)
        return 2
    print(json.dumps({'configured': True, 'network_used': False, **manifest}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

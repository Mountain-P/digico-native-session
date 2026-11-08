#!/usr/bin/env python3
"""Independent, lossless DiGiCo .ses container reader/writer.

No editor, VM, socket or process memory is used. A recognized header identifies
a file-format family, not the executable that created it. Unknown section bytes
are retained verbatim; a successful structural parse is not native acceptance.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile

from build_profiles import select_profile

HEADER_BYTES = 456
INDEX_OFFSET = 256
INDEX_SLOTS = 40
TAGS_OFFSET = 416
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
ENCODINGS = ('ascii', 'cp950', 'utf-8')
FORMAT_PROFILES = (
    'quantum1-v2126', 'quantum1-v2242-4', 'quantum2-v2242-4',
    'quantum3-v2242', 'quantum5-v2242', 'quantum7-v2242', 'quantum8-v2242',
)


class SesFormatError(ValueError):
    """Invalid, truncated or unsupported native session data."""


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _fields(value, required, optional=(), label='request'):
    if not isinstance(value, dict):
        raise SesFormatError(label + ' must be an object')
    if set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise SesFormatError('Missing or unknown fields in ' + label)


def _integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise SesFormatError(label + ' is outside its supported bounds')
    return value


def _name(value, encoding):
    if not isinstance(value, str) or '\0' in value:
        raise SesFormatError('Name must be a string without NUL')
    raw = value.encode(encoding, errors='strict')
    if not 1 <= len(raw) <= 31:
        raise SesFormatError('Name requires 1..31 encoded bytes without truncation')
    return raw


def _quarterframes(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{2}:[0-9]{2}:[0-9]{2}:[0-9]{2}', value):
        raise SesFormatError('Timecode must be HH:MM:SS:FF')
    hours, minutes, seconds, frames = map(int, value.split(':'))
    if hours > 23 or minutes > 59 or seconds > 59 or frames >= 25:
        raise SesFormatError('Timecode exceeds the supported 25-fps component range')
    return ((hours * 3600 + minutes * 60 + seconds) * 25 + frames) * 4


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SesFormatError('Duplicate JSON field: ' + key)
        result[key] = value
    return result


def _format_profile(data, requested):
    candidates = (requested,) if requested is not None else FORMAT_PROFILES
    matches = []
    for name in candidates:
        profile = select_profile(build_profile=name)
        if (data[:26] == bytes.fromhex(profile['session_file']['header_hex'])
                and struct.unpack_from('<H', data, 34)[0] == profile['release']):
            matches.append(profile)
    if len(matches) != 1:
        raise SesFormatError('Unsupported or mismatched .ses model/format/version header')
    return matches[0]


def _decode_structure(raw):
    sample_frequency, frame_rate = struct.unpack_from('<ff', raw, 0x53)
    if not math.isfinite(sample_frequency) or not math.isfinite(frame_rate):
        raise SesFormatError('Nonfinite sample frequency or video frame rate')
    return {
        'record_bytes': 123,
        'input_count_slots': list(struct.unpack_from('<8H', raw, 2)),
        'aux_count_slots': list(struct.unpack_from('<8H', raw, 0x12)),
        'group_count_slots': list(struct.unpack_from('<8H', raw, 0x22)),
        'count_slot_labels_verified': False,
        'timecode_source_raw': raw[0x3a],
        'frame_rate_index_raw': raw[0x3b],
        'sample_frequency': sample_frequency,
        'video_frame_rate': frame_rate,
        'bussing_mode_raw': raw[0x63],
        'soft_channels': raw[0x6e], 'matrix_outputs': raw[0x6f],
        'control_groups': raw[0x70], 'multi_groups': raw[0x71],
        'matrix_inputs': raw[0x72], 'graphic_eqs': raw[0x73],
    }


def _publish_new(path, data):
    """Publish complete bytes exclusively; existing paths are never replaced."""
    target = Path(path).absolute()
    if target.suffix.lower() != '.ses':
        raise SesFormatError('Output must end in .ses')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.ses-', dir=target.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # link is atomic and refuses an existing file, directory or symlink.
        # There is deliberately no replace/overwrite fallback.
        os.link(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {'bytes': len(data), 'sha256': _sha256(data)}


@dataclass(frozen=True)
class SesFile:
    _header: bytes = field(repr=False)
    sections: tuple[bytes, ...] = field(repr=False)
    build_profile: str

    @classmethod
    def from_bytes(cls, data, build_profile=None):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise SesFormatError('Session data must be bytes-like')
        size = data.nbytes if isinstance(data, memoryview) else len(data)
        if not HEADER_BYTES <= size <= MAX_FILE_BYTES:
            raise SesFormatError('Session file is truncated or exceeds the local file-size bound')
        data = bytes(data)
        profile = _format_profile(data, build_profile)
        if struct.unpack_from('<H', data, 32)[0] != HEADER_BYTES:
            raise SesFormatError('Unsupported native header size')
        table = struct.unpack_from('<40I', data, INDEX_OFFSET)
        tags = data[TAGS_OFFSET:TAGS_OFFSET + INDEX_SLOTS]
        try:
            terminal = tags.index(0, 1)
        except ValueError as error:
            raise SesFormatError('Missing terminal section-directory tag') from error
        offsets = table[:terminal + 1]
        if (tags[0] != 0 or any(tags[terminal:])
                or len(offsets) < 2 or offsets[0] != HEADER_BYTES
                or offsets[-1] != len(data) or any(table[terminal + 1:])
                or any(a >= b for a, b in zip(offsets, offsets[1:]))):
            raise SesFormatError('Invalid section offset table or end-of-file boundary')
        if any(offset > len(data) for offset in offsets):
            raise SesFormatError('Section offset exceeds the file')
        _decode_structure(data[36:159])
        return cls(data[:HEADER_BYTES], tuple(data[a:b] for a, b in
                   zip(offsets, offsets[1:])), profile['id'])

    @property
    def section_tags(self):
        return tuple(self._header[TAGS_OFFSET:TAGS_OFFSET + len(self.sections)])

    @classmethod
    def read(cls, path, build_profile=None):
        source = Path(path)
        if not stat.S_ISREG(source.stat().st_mode):
            raise SesFormatError('Source must be a regular file')
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)
        with os.fdopen(os.open(source, flags), 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise SesFormatError('Source must be a regular file')
            data = stream.read(MAX_FILE_BYTES + 1)
        return cls.from_bytes(data, build_profile)

    @property
    def structure(self):
        return _decode_structure(self._header[36:159])

    @property
    def release(self):
        return struct.unpack_from('<H', self._header, 34)[0]

    def _section_index(self, tag):
        matches = [i for i, value in enumerate(self.section_tags) if value == tag]
        if len(matches) != 1:
            raise SesFormatError('Required native section is missing or ambiguous: %02x' % tag)
        return matches[0]

    def _snapshots(self):
        from ses_snapshot import SnapshotSection
        return SnapshotSection.from_bytes(self.sections[self._section_index(0xd2)],
                                          version=self.release)

    def _console(self):
        from ses_console import CurrentConsole
        return CurrentConsole.from_bytes(self.sections[self._section_index(0xd3)],
                                         version=self.release, build_profile=self.build_profile)

    @staticmethod
    def _validate_current_references(console, snapshots):
        pool_count, snapshot_count = len(snapshots.modules), len(snapshots.snapshots)
        for channel in console.snapshot.channels:
            if not channel.own_modules.effective(1)[0]:
                for index in channel.modules:
                    _integer(index, 'Current-state module reference', -1, pool_count-1)
                continue
            for module in channel.modules:
                _integer(module.parent_module_index, 'Current-state parent module reference',
                         -1, pool_count-1)
                _integer(module.original_snapshot_index, 'Current-state Snapshot reference',
                         -1, snapshot_count-1)

    def _admit_snapshot_structure_edit(self, sections):
        from ses_snapshot import assert_no_external_snapshot_refs
        def section(tag):
            try:
                return sections[self._section_index(tag)]
            except SesFormatError as error:
                raise SesFormatError('Advanced Snapshot edits require external reference sections') from error
        return assert_no_external_snapshot_refs(
            version=self.release, current_section=section(0xd3),
            preset_section=section(0xdb),
            setlist_section=section(0x73) if self.release == 2242 else None)

    def to_bytes(self):
        if not 1 <= len(self.sections) < INDEX_SLOTS:
            raise SesFormatError('Section count exceeds the native index capacity')
        header = bytearray(self._header)
        offsets = [HEADER_BYTES]
        for section in self.sections:
            if not isinstance(section, bytes) or not section:
                raise SesFormatError('Invalid empty or non-byte section')
            offsets.append(offsets[-1] + len(section))
        if offsets[-1] > MAX_FILE_BYTES:
            raise SesFormatError('Output exceeds the local file-size bound')
        struct.pack_into('<40I', header, INDEX_OFFSET,
                         *(offsets + [0] * (INDEX_SLOTS - len(offsets))))
        return bytes(header) + b''.join(self.sections)

    def inspect(self, encoding='ascii', channel_id=None, include_raw_controls=False):
        if encoding not in ENCODINGS:
            raise SesFormatError('Unsupported text encoding')
        data = self.to_bytes()
        offset = HEADER_BYTES
        sections = []
        for index, section in enumerate(self.sections):
            sections.append({'index': index, 'tag': self.section_tags[index], 'offset': offset,
                             'bytes': len(section), 'sha256': _sha256(section)})
            offset += len(section)
        report = {'schema_version': 1, 'subsystem': 'independent-ses-file',
                'format_profile': self.build_profile,
                'format_marker': self._header[21:22].decode('ascii'),
                'release': struct.unpack_from('<H', self._header, 34)[0],
                'bytes': len(data), 'sha256': _sha256(data),
                'header_bytes': HEADER_BYTES, 'structure': self.structure,
                'sections': sections,
                'native_acceptance_verified': False,
                'editor_used': False, 'network_used': False,
                'executable_identity_verified': False,
                'semantic_coverage': 'header-and-container'}
        if 0xd2 in self.section_tags:
            snapshot_state = self._snapshots()
            snapshots = snapshot_state.inspect(encoding, include_channels=False)
            for index, snapshot in enumerate(snapshots['snapshots']):
                snapshot.pop('channels', None)
                snapshot['index'] = index
            report['snapshot_section'] = snapshots
            report['semantic_coverage'] = 'header-container-snapshots'
        if 0xd3 in self.section_tags:
            console = self._console()
            if 0xd2 in self.section_tags:
                self._validate_current_references(console, snapshot_state)
            report['current_console'] = console.inspect(
                encoding, channel_id, include_raw_controls=include_raw_controls)
            report['semantic_coverage'] = 'header-container-snapshots-current-basic-mix'
        return report

    def edit(self, request):
        _fields(request, ('schema_version', 'expected_sha256', 'encoding', 'operations'),
                ('build_profile', 'fps'))
        if type(request['schema_version']) is not int or request['schema_version'] != 1:
            raise SesFormatError('schema_version must be integer 1')
        expected = request['expected_sha256']
        if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
            raise SesFormatError('expected_sha256 must be a lowercase SHA-256')
        if expected != _sha256(self.to_bytes()):
            raise SesFormatError('Source SHA-256 differs from the reviewed request')
        if request.get('build_profile', self.build_profile) != self.build_profile:
            raise SesFormatError('Request build_profile differs from the source format')
        if type(request.get('fps', 25)) is not int or request.get('fps', 25) != 25:
            raise SesFormatError('Only 25-fps editing is supported')
        if self.structure['video_frame_rate'] != 25:
            raise SesFormatError('Source Session is not configured for 25 fps')
        encoding = request['encoding']
        if encoding not in ENCODINGS:
            raise SesFormatError('Unsupported text encoding')
        operations = request['operations']
        if not isinstance(operations, list) or len(operations) > 1000:
            raise SesFormatError('operations must be an array with at most1000 entries')
        if not operations:
            return self
        snapshots = self._snapshots()
        sections = list(self.sections)
        console = None
        for operation in operations:
            if not isinstance(operation, dict):
                raise SesFormatError('Each operation must be an object')
            op = operation.get('op')
            if op in ('set_channel_name', 'set_control'):
                if console is None:
                    console = self._console()
                if op == 'set_channel_name':
                    _fields(operation, ('op', 'channel_id', 'name'), label=op)
                    console.rename_channel(operation['channel_id'], _name(operation['name'], encoding))
                else:
                    _fields(operation, ('op', 'id', 'value'), label=op)
                    console.set_basic_control(operation['id'], operation['value'])
                # Later create operations capture the edited current state.
                sections[self._section_index(0xd3)] = console.to_bytes()
            elif op == 'rename':
                _fields(operation, ('op', 'index', 'name'), label='rename')
                snapshots.rename(operation['index'], _name(operation['name'], encoding))
            elif op == 'timecode':
                _fields(operation, ('op', 'index', 'timecode'), ('active',), label='timecode')
                snapshots.timecode(operation['index'], _quarterframes(operation['timecode']),
                                   operation.get('active', True))
            elif op == 'number':
                _fields(operation, ('op', 'index', 'number'), label='number')
                snapshots.set_number(operation['index'], operation['number'])
            elif op == 'delete':
                _fields(operation, ('op', 'index'), label='delete')
                self._admit_snapshot_structure_edit(sections)
                index = _integer(operation['index'], 'Snapshot index', 0, len(snapshots.snapshots)-1)
                if snapshots.snapshots[index].inspect(encoding)['locked']:
                    raise SesFormatError('Cannot delete a locked Snapshot')
                snapshots.delete(index)
            elif op in ('create', 'duplicate'):
                required = ('op', 'index') if op == 'create' else ('op', 'index', 'source_index')
                _fields(operation, required, ('name', 'timecode', 'active', 'number'), label=op)
                self._admit_snapshot_structure_edit(sections)
                index = operation['index']
                if index == 'append':
                    index = len(snapshots.snapshots)
                _integer(index, 'Snapshot insertion index', 0, len(snapshots.snapshots))
                if op == 'create':
                    snapshots.create_from_current_console(sections[self._section_index(0xd3)], index)
                else:
                    snapshots.duplicate(operation['source_index'], index)
                if 'number' in operation:
                    snapshots.set_number(index, operation['number'])
                if 'name' in operation:
                    snapshots.rename(index, _name(operation['name'], encoding))
                if 'timecode' in operation:
                    snapshots.timecode(index, _quarterframes(operation['timecode']),
                                       operation.get('active', True))
                elif 'active' in operation:
                    raise SesFormatError('active requires timecode')
            else:
                raise SesFormatError('Unsupported direct-file operation: ' + str(op))
        sections[self._section_index(0xd2)] = snapshots.to_bytes()
        result = SesFile(self._header, tuple(sections), self.build_profile)
        # Fully reparse the changed semantic section before returning it.
        result = SesFile.from_bytes(result.to_bytes(), self.build_profile)
        result._snapshots()
        if console is not None:
            self._validate_current_references(result._console(), result._snapshots())
        return result

    def write(self, path):
        data = self.to_bytes()
        # Reparse before publication so invalid manually constructed objects fail.
        verified = SesFile.from_bytes(data, self.build_profile)
        if 0xd2 in verified.section_tags:
            snapshots = verified._snapshots()
        if 0xd3 in verified.section_tags:
            console = verified._console()
            if 0xd2 in verified.section_tags:
                self._validate_current_references(console, snapshots)
        return _publish_new(path, data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('inspect', 'clone', 'edit'):
        sub = commands.add_parser(command)
        sub.add_argument('--source', type=Path, required=True)
        sub.add_argument('--build-profile', choices=FORMAT_PROFILES)
        sub.add_argument('--encoding', choices=ENCODINGS, default='ascii')
        sub.add_argument('--channel-id', type=lambda value: int(value, 0),
                         help='filter current-mix inspection to one raw uint16 channel ID')
        sub.add_argument('--include-raw-controls', action='store_true',
                         help='also list bounded raw controller records without guessing datatypes')
        if command != 'inspect':
            sub.add_argument('--output', type=Path, required=True)
        if command == 'edit':
            sub.add_argument('--request', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        session = SesFile.read(args.source, args.build_profile)
        if args.command == 'edit':
            request_stat = args.request.stat()
            if not stat.S_ISREG(request_stat.st_mode):
                raise SesFormatError('Request must be a regular file')
            if request_stat.st_size > MAX_REQUEST_BYTES:
                raise SesFormatError('Request exceeds the local JSON byte bound')
            flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)
            with os.fdopen(os.open(args.request, flags), 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise SesFormatError('Request must be a regular file')
                raw_request = stream.read(MAX_REQUEST_BYTES + 1)
            if len(raw_request) > MAX_REQUEST_BYTES:
                raise SesFormatError('Request exceeds the local JSON byte bound')
            session = session.edit(json.loads(raw_request.decode('utf-8-sig'),
                                              object_pairs_hook=_json_object))
        report = session.inspect(args.encoding, args.channel_id, args.include_raw_controls)
        if args.command != 'inspect':
            session.write(args.output)
        print(json.dumps(report, ensure_ascii=False,
                         indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, OverflowError, struct.error) as error:
        print(json.dumps({'error': str(error), 'editor_used': False,
                          'network_used': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

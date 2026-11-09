"""Pure current-mix D3 semantics over the bounded native Snapshot serializer.

Input and output-channel names, fader, trim and mute have bounded setters. File
records do not contain live controller definition/VMT/response-pointer proof;
these edits therefore never claim runtime acceptance or physical audio proof.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import math
import re
import struct

from build_profiles import BuildProfileError, select_profile
from ses_snapshot import ConsoleSnapshot, ModuleSnapshot, SnapshotFormatError


class ConsoleFormatError(SnapshotFormatError):
    pass


# Parameter, module, band are exactly the first three bytes copied by native
# ModuleSnapshot.Create. Both continuous dtype1 and Boolean dtype0 values below
# are native Single overlays, not int32 Boolean encodings.
_BASIC = {'210c00': ('fader', -150.0, 10.0, 'dB'),
          '010100': ('trim', -20.0, 60.0, 'dB'),
          '220c00': ('mute', 0.0, 1.0, None)}
_SUPPORTED_PROFILES = frozenset((
    'quantum1-v2126', 'quantum1-v2242-4', 'quantum2-v2242-4',
    'quantum3-v2242', 'quantum5-v2242', 'quantum7-v2242', 'quantum8-v2242'))
_TRIM_MINIMUM = {name: (-40.0 if name in ('quantum1-v2126', 'quantum1-v2242-4',
                                         'quantum8-v2242') else -20.0)
                 for name in _SUPPORTED_PROFILES}
_ENCODINGS = ('ascii', 'cp950', 'utf-8')
_LEVEL_CHANNEL_TYPES = (0x01, 0x05, 0x06, 0x10)
MAX_RAW_CONTROL_ROWS = 100000


def _writable_control(channel_id, prefix):
    kind = channel_id >> 8
    return kind == 0x01 if prefix == '010100' else kind in _LEVEL_CHANNEL_TYPES


def _channel_id(value):
    if type(value) is not int or not 0 <= value <= 65535:
        raise ConsoleFormatError('Channel ID must be a uint16 integer')
    return value


def _profile(value, version):
    if value is None:
        return None
    try:
        if isinstance(value, Mapping):
            profile = select_profile(build_profile=value.get('id'),
                                     executable_sha256=value.get('executable', {}).get('sha256'))
        elif isinstance(value, str):
            profile = select_profile(build_profile=value)
        else:
            raise ConsoleFormatError('Build profile must be a profile ID or mapping')
    except BuildProfileError as error:
        raise ConsoleFormatError(str(error)) from error
    if profile['id'] not in _SUPPORTED_PROFILES or profile['release'] != version:
        raise ConsoleFormatError('Current-mix layout has no matching reviewed file-format profile')
    return profile


class CurrentConsole:
    def __init__(self, snapshot, version, build_profile=None, module_pool=None):
        self.snapshot = snapshot
        self.version = version
        self.profile = _profile(build_profile, version)
        if module_pool is not None and (not isinstance(module_pool, (list, tuple))
                or any(not isinstance(module, ModuleSnapshot) for module in module_pool)):
            raise ConsoleFormatError('Module pool must contain parsed ModuleSnapshot objects')
        # Pool-backed views inspect stored Snapshot records only. They neither
        # compute scope/inheritance nor enable current-state setters.
        self.module_pool = tuple(module_pool) if module_pool is not None else None

    @classmethod
    def from_bytes(cls, data, version=2242, build_profile=None, module_pool=None):
        return cls(ConsoleSnapshot.from_bytes(data, version=version), version, build_profile, module_pool)

    def to_bytes(self):
        return self.snapshot.to_bytes()

    def _channel(self, channel_id):
        _channel_id(channel_id)
        rows = [row for row in self.snapshot.channels if row.channel_id == channel_id]
        if len(rows) != 1:
            raise ConsoleFormatError('Current-state channel is missing or ambiguous')
        raw = rows[0].metadata.effective(39)
        if raw[2] > 31:
            raise ConsoleFormatError('Channel name length exceeds its reviewed ShortString31 field')
        return rows[0]

    def _modules(self, channel):
        if channel.own_modules.effective(1)[0]:
            for module in channel.modules:
                if not isinstance(module, ModuleSnapshot):
                    raise ConsoleFormatError('Owned current-state module is not inline')
                yield module
        elif self.module_pool is not None:
            for index in channel.modules:
                if type(index) is not int or not -1 <= index < len(self.module_pool):
                    raise ConsoleFormatError('Shared module pool index is outside its bounds')
                if index != -1:
                    yield self.module_pool[index]

    def _controls(self, channel):
        """Yield stored numeric records, without applying scope/inheritance."""
        for module in self._modules(channel):
            module_type = struct.unpack_from('<i', module.header.effective(58))[0]
            for offset in range(0, len(module.controls), 8):
                record = module.controls[offset:offset+8]
                prefix = record[:3].hex()
                if prefix in _BASIC:
                    if module_type != record[1]:
                        raise ConsoleFormatError('Controller ID disagrees with its enclosing module TypeID')
                    yield module, offset, prefix, record

    def _bounds(self, prefix):
        label, low, high, unit = _BASIC[prefix]
        if prefix == '010100' and self.profile is not None:
            # Exact-profile static seed facts; no caller-supplied range is used.
            low = _TRIM_MINIMUM[self.profile['id']]
        return label, low, high, unit

    def inspect(self, encoding='ascii', channel_id=None, include_raw_controls=False):
        if encoding not in _ENCODINGS:
            raise ConsoleFormatError('Name encoding must be ascii, cp950, or utf-8')
        if type(include_raw_controls) is not bool:
            raise ConsoleFormatError('include_raw_controls must be Boolean')
        if channel_id is None:
            channels = self.snapshot.channels
        else:
            channels = [self._channel(channel_id)]
        if include_raw_controls:
            raw_count = sum(len(module.controls) // 8 for channel in channels
                            for module in self._modules(channel))
            if raw_count > MAX_RAW_CONTROL_ROWS:
                raise ConsoleFormatError('Raw control inspection exceeds its row bound; filter by channel_id')
        rows = []
        channel_counts = Counter(item.channel_id for item in self.snapshot.channels)
        for channel in channels:
            raw = channel.metadata.effective(39)
            if raw[2] > 31:
                raise ConsoleFormatError('Channel name exceeds its ShortString31 byte bound')
            namebytes = raw[3:3+raw[2]]
            try:
                name = namebytes.decode(encoding, errors='strict')
            except UnicodeError:
                name = None
            controls = []
            records = list(self._controls(channel))
            prefix_counts = Counter(record[2] for record in records)
            for module, offset, prefix, record in records:
                label, low, high, unit = self._bounds(prefix)
                value = struct.unpack('<f', record[4:8])[0]
                finite = math.isfinite(value)
                controls.append({'id': (record[:3] + struct.pack('<H', channel.channel_id)).hex(),
                                 'parameter': label, 'value': value if finite else None,
                                 'value_raw_hex': record[4:8].hex(), 'finite': finite,
                                 'flags': record[3], 'native_unit': unit,
                                 'minimum': low, 'maximum': high,
                                 'file_write_supported': (self.module_pool is None
                                     and bool(channel.own_modules.effective(1)[0])
                                     and _writable_control(channel.channel_id, prefix)
                                     and record[3] == 0 and module.parent_module_index == -1
                                     and channel_counts[channel.channel_id] == 1
                                     and prefix_counts[prefix] == 1)})
            summary = {'channel_id': channel.channel_id, 'name': name,
                         'name_decoded': name is not None, 'name_raw_hex': namebytes.hex(),
                         'own_modules': bool(channel.own_modules.effective(1)[0]),
                         'basic_controls': controls}
            if not channel.own_modules.effective(1)[0]:
                summary['module_pool_indices'] = list(channel.modules)
                summary['shared_module_references_resolved'] = self.module_pool is not None
            if include_raw_controls:
                summary['raw_controls'] = []
                for module in self._modules(channel):
                    module_type = struct.unpack_from('<i', module.header.effective(58))[0]
                    for p, m, band, flags, value in struct.iter_unpack('<BBBB4s', module.controls):
                        summary['raw_controls'].append({
                            'id': (bytes((p, m, band)) + struct.pack('<H', channel.channel_id)).hex(),
                            'flags': flags, 'value_raw_hex': value.hex(),
                            'value_type': 'opaque_native_4bytes', 'module_type': module_type,
                            'parent_module_index': module.parent_module_index,
                            'original_snapshot_index': module.original_snapshot_index})
                if not channel.own_modules.effective(1)[0] and self.module_pool is None:
                    summary['raw_controls_unavailable_reason'] = 'shared_module_references'
            rows.append(summary)
        return {'version': self.version,
                'file_format_profile': self.profile['id'] if self.profile is not None else None,
                'channel_count': len(self.snapshot.channels), 'channels': rows,
                'record_semantics': 'stored_controller_records',
                'scope_inheritance_applied': False, 'effective_recall_verified': False,
                'bounds_source': 'reviewed_static_definition_seed',
                'live_definition_verified': False, 'native_load_verified': False,
                'physical_audio_verified': False}

    def rename_channel(self, channel_id, namebytes):
        if self.module_pool is not None:
            raise ConsoleFormatError('Pool-backed Snapshot inspection is read-only')
        channel = self._channel(channel_id)
        if channel_id >> 8 not in _LEVEL_CHANNEL_TYPES:
            raise ConsoleFormatError('Names are writable on Input, Aux, Group and Matrix outputs')
        if (not isinstance(namebytes, bytes) or not 1 <= len(namebytes) <= 31
                or b'\0' in namebytes):
            raise ConsoleFormatError('Channel name requires 1..31 encoded bytes without NUL')
        # A native ShortString copy overwrites length+body; preserve stale tail,
        # other metadata, and unknown record extensions byte-for-byte.
        channel.metadata.patch(2, bytes((len(namebytes),)) + namebytes, 39)
        return self

    def set_basic_control(self, id_hex, value):
        if not isinstance(id_hex, str) or not re.fullmatch('[0-9a-fA-F]{10}', id_hex):
            raise ConsoleFormatError('Control ID must contain exactly five hexadecimal bytes')
        raw_id = bytes.fromhex(id_hex)
        prefix = raw_id[:3].hex()
        if prefix not in _BASIC:
            raise ConsoleFormatError('File control write is limited to reviewed band0 fader, trim and mute')
        channel_id = struct.unpack('<H', raw_id[3:])[0]
        if not _writable_control(channel_id, prefix):
            raise ConsoleFormatError('Channel type is outside the reviewed control write domain')
        channel = self._channel(channel_id)
        if not channel.own_modules.effective(1)[0]:
            raise ConsoleFormatError('Current-state control uses shared module references')
        if self.module_pool is not None:
            raise ConsoleFormatError('Pool-backed Snapshot inspection is read-only')
        matches = [(module, offset, record) for module, offset, key, record in self._controls(channel)
                   if key == prefix]
        if len(matches) != 1:
            raise ConsoleFormatError('Current-state control is missing or ambiguous')
        module, offset, record = matches[0]
        if record[3] != 0 or module.parent_module_index != -1:
            raise ConsoleFormatError('Scoped or inherited current-state controls are read-only')
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConsoleFormatError('Control value must be a finite native number')
        try:
            numeric = float(value)
            encoded = struct.pack('<f', numeric)
        except (ValueError, OverflowError, struct.error) as error:
            raise ConsoleFormatError('Control value cannot be represented as native float32') from error
        canonical = struct.unpack('<f', encoded)[0]
        label, low, high, unit = self._bounds(prefix)
        if not math.isfinite(canonical) or not low <= canonical <= high:
            raise ConsoleFormatError(label + ' exceeds its reviewed native value bounds')
        if prefix == '220c00' and numeric not in (0, 1):
            raise ConsoleFormatError('Mute requires numeric 0 or 1, encoded as native float32')
        module.controls = module.controls[:offset+4] + encoded + module.controls[offset+8:]
        return self

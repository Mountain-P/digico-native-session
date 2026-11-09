"""Editor-free DB/D7 processing presets and their positional references."""
from __future__ import annotations

import copy
import struct

from ses_snapshot import (
    ChannelSnapshot, ConsoleSnapshot, MAX_STREAM_BYTES, ModuleSnapshot,
    PresetSection, SnapshotFormatError, SnapshotSection, _Block, _Preset,
)


class PresetFileError(SnapshotFormatError):
    pass


# Reviewed mono Input records observed in native processing captures and D3.
# The Gate/Mustard key sockets, physical input gain, inserts, sends, assignments,
# and D3-only bookkeeping are outside these processing scopes.
_EQ_BAND = '03 04 05 06 07 0b 0e 10 11 12 7c 7d'
_DYNAMICS_BAND = '03 05 09 0e 0f 10 11 12 3b'
_MODULE_RULES = {
    1: ('trim', ('01',)),
    4: ('eq', ('02 ' + _EQ_BAND, _EQ_BAND, _EQ_BAND, _EQ_BAND)),
    5: ('dynamics', ('02 03 05 09 0e 0f 10 11 12 14 37 3a 3b', _DYNAMICS_BAND,
                     _DYNAMICS_BAND, '02 05 09 0a 0b 0e 0f 10 11 12 13 3b')),
    7: ('eq', ('03 05', '03 05')),
    9: ('delay', ('07 08 17',)),
    12: ('fader', ('21 22',)),
    26: ('tidal_preamp', ('02 05 1a 1b 37 38 39 3a 3b 3c 45',)),
    27: ('tidal_eq', ('03 05', '03 05')),
    28: ('tidal_eq', ('02 04 05 06 07', '04 05 06 07', '04 05 06 07', '04 05 06 07')),
    29: ('tidal_comp', ('02 03 05 09 0b 0e 0f 10 11 12 37 3f 41 46 47 48 49 4a 4c 4d', '05')),
    30: ('tidal_gate', ('02 03 05 0a 0b 0e 11 12 13 37 4c', '05')),
}
_RULES = {module: (scope, tuple(bytes.fromhex(parameters) for parameters in bands))
          for module, (scope, bands) in _MODULE_RULES.items()}
PRESET_SCOPES = tuple(dict.fromkeys(scope for scope, _ in _RULES.values()))
_ENCODINGS = ('ascii', 'cp950', 'utf-8')
_MAX_PRESETS = 10000
_MAX_RAW_CONTROLS = 100000


def _text(value, label, maximum, minimum=0):
    if (not isinstance(value, bytes) or not minimum <= len(value) <= maximum
            or b'\0' in value):
        raise PresetFileError(f'{label} requires {minimum}..{maximum} encoded bytes without NUL')
    return _Block(value)


def _decode(value, encoding):
    try:
        return value.decode(encoding)
    except UnicodeError:
        return None


def _scopes(scopes):
    if scopes is None:
        return None
    if (not isinstance(scopes, (list, tuple)) or not scopes
            or len(scopes) > len(PRESET_SCOPES)
            or any(type(scope) is not str or scope not in PRESET_SCOPES for scope in scopes)
            or len(set(scopes)) != len(scopes)):
        raise PresetFileError('Preset scopes must be a nonempty list of unique supported names')
    return set(scopes)


def _input_channel(current, channel_id):
    if not isinstance(current, ConsoleSnapshot):
        raise PresetFileError('Expected the same session’s decoded current-state ConsoleSnapshot')
    if type(channel_id) is not int or not 0x100 <= channel_id <= 0x1ff:
        raise PresetFileError('Preset processing requires an Input channel ID in 0x0100..0x01ff')
    rows = [channel for channel in current.channels if channel.channel_id == channel_id]
    if len(rows) != 1:
        raise PresetFileError('Input channel is missing or ambiguous')
    return rows[0]


def _stored_time(current):
    if not isinstance(current.timestamp, bytes) or len(current.timestamp) != 8:
        raise PresetFileError('Current-state timestamp is outside its reviewed eight-byte layout')
    return current.timestamp


def _processing_records(channel, scopes):
    if (not 0x100 <= channel.channel_id <= 0x1ff or len(channel.metadata.data) != 39
            or channel.metadata.data[38] != 1 or channel.own_modules.effective(1) != b'\x01'):
        raise PresetFileError('Preset processing requires a reviewed owned mono Input record')
    selected = []
    seen_modules, found_scopes = set(), set()
    for module in channel.modules:
        if not isinstance(module, ModuleSnapshot):
            raise PresetFileError('Expected inline processing modules')
        module_type = struct.unpack_from('<i', module.header.effective(58))[0]
        rule = _RULES.get(module_type)
        if rule is None or (scopes is not None and rule[0] not in scopes):
            continue
        scope, bands = rule
        if module_type in seen_modules:
            raise PresetFileError('Preset module TypeID is ambiguous within the Input')
        seen_modules.add(module_type)
        if (len(module.header.data) != 58 or len(module.controls) % 8
                or module.parent_module_index != -1 or module.original_snapshot_index != -1
                or module.player_index_plus_one != 0 or module.header.data[48:53] != bytes(5)):
            raise PresetFileError('Preset processing module contains unreviewed state or references')
        records = {}
        for offset in range(0, len(module.controls), 8):
            parameter, owner, band, flags = module.controls[offset:offset+4]
            if band >= len(bands) or parameter not in bands[band]:
                continue
            prefix = module.controls[offset:offset+3]
            if owner != module_type or flags or prefix in records:
                raise PresetFileError('Preset controller ID or flags are ambiguous')
            records[prefix] = offset
        if records:
            selected.append((module, records))
            found_scopes.add(scope)
    if not selected or (scopes is not None and scopes - found_scopes):
        raise PresetFileError('Input lacks the requested supported processing scopes')
    return selected


def _transfer_processing(source, destination, scopes, *, require_source):
    source_records = _processing_records(source, scopes)
    destination_records = _processing_records(destination, scopes)
    values = {prefix: module.controls[offset+4:offset+8]
              for module, records in source_records for prefix, offset in records.items()}
    destination_ids = {prefix for _, records in destination_records for prefix in records}
    required = values.keys() if require_source else destination_ids
    available = destination_ids if require_source else values.keys()
    if required - available:
        raise PresetFileError('Source and destination processing controller layouts differ')

    updates = []
    for module, records in destination_records:
        payload = bytearray(module.controls)
        for prefix, offset in records.items():
            if prefix in values:
                payload[offset+4:offset+8] = values[prefix]
        updates.append((module, bytes(payload)))
    for module, payload in updates:
        module.controls = payload
    return len(required)


class SessionPresets(PresetSection):
    """Lossless PresetSection codec with bounded mono Input processing edits."""

    def _preset(self, index, *, editable=False):
        if type(index) is not int or not 0 <= index < len(self.presets):
            raise PresetFileError('Preset index is outside the library')
        preset = self.presets[index]
        if editable and preset.metadata.effective(14)[8] != 0:
            raise PresetFileError('Preset is locked or factory protected')
        return preset

    @staticmethod
    def _ordinary_input(preset):
        metadata = preset.metadata.effective(14)
        if len(preset.channels) != 1 or struct.unpack_from('<HHB', metadata, 9) != (0, 1, 0):
            raise PresetFileError('Processing apply/update requires a one-channel ordinary Input preset')
        return preset.channels[0]

    def inspect(self, encoding='ascii', preset_index=None, include_raw_controls=False):
        if encoding not in _ENCODINGS or type(include_raw_controls) is not bool:
            raise PresetFileError('Unrecognized Preset inspection encoding or Boolean option')
        rows = (enumerate(self.presets) if preset_index is None
                else ((preset_index, self._preset(preset_index)),))
        presets = []
        raw_count = 0
        for index, preset in rows:
            metadata = preset.metadata.effective(14)
            lock, fx_resource, channel_count, mix_preset = struct.unpack_from('<BHHB', metadata, 8)
            channels = []
            for channel in preset.channels:
                row = channel.inspect()
                row['channel_name'] = _decode(bytes.fromhex(row['channel_name_raw_hex']), encoding)
                if include_raw_controls and row['own_modules']:
                    raw_count += sum(len(module.controls) // 8 for module in channel.modules)
                    if raw_count > _MAX_RAW_CONTROLS:
                        raise PresetFileError('Preset raw control output exceeds its bound; select one preset')
                    row['modules'] = [module.inspect() for module in channel.modules]
                channels.append(row)
            names = {key: getattr(preset, attr).data for key, attr in
                     (('name', 'name'), ('group_name', 'group'), ('notes', 'notes'))}
            presets.append({'index': index, 'lock_flag': lock, 'fx_resource': fx_resource,
                            'channel_count': channel_count, 'mix_preset': mix_preset,
                            'time_stored_hex': metadata[:8].hex(), 'channels': channels,
                            **{key: _decode(value, encoding) for key, value in names.items()},
                            **{f'{key}_hex': value.hex() for key, value in names.items()}})
        return {'preset_count': len(self.presets), 'presets': presets,
                'supported_processing_scopes': list(PRESET_SCOPES)}

    def capture(self, current, channel_id, *, name, group, notes=b'', scopes=None):
        if len(self.presets) >= _MAX_PRESETS:
            raise PresetFileError('Preset count exceeds its bound')
        name = _text(name, 'Preset name', 31, minimum=1)
        group = _text(group, 'Preset group', 31)
        notes = _text(notes, 'Preset notes', 255)
        source = _input_channel(current, channel_id)
        selected = _processing_records(source, _scopes(scopes))
        timestamp = _stored_time(current)
        modules = []
        for module, records in selected:
            header = bytearray(module.header.data)
            header[36:44] = timestamp
            struct.pack_into('<i', header, 44, len(records))
            header[48:58] = bytes(10)
            payload = b''.join(module.controls[offset:offset+8] for offset in records.values())
            modules.append(ModuleSnapshot(_Block(bytes(header)), payload, -1, -1, 0))
        channel_metadata = _Block(source.metadata.data)
        channel_metadata.patch(34, struct.pack('<i', -1), 39)
        channel = ChannelSnapshot(_Block(b'\x01'), modules, channel_metadata)
        metadata = _Block(timestamp + struct.pack('<BHHB', 0, 0, 1, 0))
        preset = _Preset([channel], name, group, notes, metadata)
        if self.byte_size() + preset.byte_size() > MAX_STREAM_BYTES:
            raise PresetFileError('Preset insertion exceeds its byte bound')
        index = len(self.presets)
        self.presets.append(preset)
        return index

    def rename(self, index, name, *, group=None, notes=None):
        preset = self._preset(index, editable=True)
        name = _text(name, 'Preset name', 31, minimum=1)
        group = preset.group if group is None else _text(group, 'Preset group', 31)
        notes = preset.notes if notes is None else _text(notes, 'Preset notes', 255)
        new_size = sum(block.byte_size() for block in (name, group, notes))
        old_size = sum(block.byte_size() for block in (preset.name, preset.group, preset.notes))
        if self.byte_size() + new_size - old_size > MAX_STREAM_BYTES:
            raise PresetFileError('Preset strings exceed the section byte bound')
        preset.name, preset.group, preset.notes = name, group, notes

    def set_lock(self, index, locked):
        if type(locked) is not bool:
            raise PresetFileError('Preset lock state must be Boolean')
        preset = self._preset(index)
        if preset.metadata.effective(14)[8] not in (0, 1):
            raise PresetFileError('Preset lock state is factory protected or unreviewed')
        metadata = _Block(preset.metadata.data)
        metadata.patch(8, bytes([locked]), 14)
        if self.byte_size() + metadata.byte_size() - preset.metadata.byte_size() > MAX_STREAM_BYTES:
            raise PresetFileError('Preset lock metadata exceeds the section byte bound')
        preset.metadata = metadata

    def update(self, index, current, channel_id, *, scopes=None):
        preset = self._preset(index, editable=True)
        source = _input_channel(current, channel_id)
        timestamp = _stored_time(current)
        destination = self._ordinary_input(preset)
        count = _transfer_processing(source, destination, _scopes(scopes), require_source=False)
        preset.metadata.patch(0, timestamp, 14)
        return count

    def apply(self, index, current, channel_id, *, scopes):
        """Copy scoped values to current state without setting a preset link."""
        scope_set = _scopes(scopes)
        if scope_set is None:
            raise PresetFileError('Preset apply requires explicit processing scopes')
        source = self._ordinary_input(self._preset(index))
        destination = _input_channel(current, channel_id)
        return _transfer_processing(source, destination, scope_set, require_source=True)

    def delete(self, index, *, current, snapshots, routing):
        """Delete an unlocked preset and remap the reviewed D5/C9 indices."""
        self._preset(index, editable=True)
        if not isinstance(current, ConsoleSnapshot) or not isinstance(snapshots, SnapshotSection):
            raise PresetFileError('Preset deletion requires parsed D3 and D2 sections')
        if snapshots.version != self.version:
            raise PresetFileError('Preset and Snapshot sections have different versions')
        from ses_routing import C9Console
        if not isinstance(routing, C9Console) or routing.version != self.version:
            raise PresetFileError('Preset deletion requires the same session’s parsed C9 section')

        channels = list(current.channels) + list(snapshots.unused_channels)
        channels.extend(channel for snapshot in snapshots.snapshots for channel in snapshot.channels)
        channels.extend(channel for preset in self.presets for channel in preset.channels)
        count = len(self.presets)

        def remap(reference):
            if not -1 <= reference < count:
                raise PresetFileError('Channel contains an invalid Preset positional reference')
            if reference == index:
                return -1
            return reference - 1 if reference > index else reference

        snapshot_updates, routing_updates = [], []
        for channel in channels:
            if len(channel.metadata.data) != 39:
                raise PresetFileError('Preset deletion requires reviewed D5 channel metadata')
            original = struct.unpack_from('<i', channel.metadata.data, 34)[0]
            replacement = remap(original)
            if replacement != original:
                metadata = _Block(channel.metadata.data)
                metadata.patch(34, struct.pack('<i', replacement), 39)
                snapshot_updates.append((channel, metadata))
        for group in routing.groups:
            for channel in group.channels:
                original = channel.preset_index
                replacement = remap(original)
                if replacement != original:
                    candidate = copy.copy(channel)
                    candidate.set_preset_index(replacement)
                    routing_updates.append((channel, candidate.metadata))

        del self.presets[index]
        for channel, metadata in snapshot_updates + routing_updates:
            channel.metadata = metadata

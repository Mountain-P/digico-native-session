"""Bounded, lossless Snapshot streams for the reviewed vN/vO Store layouts.

This module reads bytes only. It neither loads an editor nor proves that an
edited file is accepted by an editor. Unknown record bytes and shared pools are
preserved; a complete .ses container is the caller's separate responsibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
import copy
import struct


class SnapshotFormatError(ValueError):
    pass


MAX_CHANNELS = 4096
MAX_SNAPSHOTS = 10000
MAX_STREAM_BYTES = 128 * 1024 * 1024
# Every counted child/reference/controller consumes at least four serialized
# bytes. Bound aggregate objects by the actual input extent, rather than a fixed
# 200k cap which rejects a few legitimate owned full-console captures.
MAX_ITEMS = MAX_STREAM_BYTES // 4
SUPPORTED_VERSIONS = (2126, 2242)


def _integer(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise SnapshotFormatError(label + ' is outside its supported bounds')
    return value


class _Reader:
    def __init__(self, data, version):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError('Snapshot stream must be bytes-like')
        extent = data.nbytes if isinstance(data, memoryview) else len(data)
        if extent > MAX_STREAM_BYTES:
            raise SnapshotFormatError('Snapshot stream exceeds its byte bound')
        _integer(version, 'version', 0, 65535)
        if version not in SUPPORTED_VERSIONS:
            raise SnapshotFormatError('Unreviewed Snapshot Store version')
        self.data = bytes(data)
        self.position = 0
        self.version = version
        self.total_items = 0
        self.item_limit = max(1, len(self.data) // 4)

    def read(self, length):
        if length < 0 or self.position + length > len(self.data):
            raise SnapshotFormatError('Truncated Snapshot stream at byte ' + str(self.position))
        start = self.position
        self.position += length
        return self.data[start:self.position]

    def u16(self):
        return struct.unpack('<H', self.read(2))[0]

    def i32(self):
        return struct.unpack('<i', self.read(4))[0]

    def token(self, expected):
        if self.u16() != expected:
            raise SnapshotFormatError('Unexpected Snapshot record token')

    def count(self, maximum=MAX_ITEMS):
        count = _integer(self.i32(), 'record count', 0, maximum)
        self.total_items += count
        if self.total_items > self.item_limit:
            raise SnapshotFormatError('Snapshot object count exceeds its aggregate bound')
        return count

    def block(self):
        return _Block(self.read(self.u16()))

    def finish(self):
        if self.position != len(self.data):
            raise SnapshotFormatError('Unexpected trailing Snapshot section bytes')


@dataclass
class _Block:
    """Native u16-length record; Load zero-fills short records and skips excess."""
    data: bytes

    def byte_size(self):
        return 2 + len(self.data)

    def to_bytes(self):
        if len(self.data) > 65535:
            raise SnapshotFormatError('Native record exceeds uint16 length')
        return struct.pack('<H', len(self.data)) + self.data

    def effective(self, capacity):
        return self.data[:capacity].ljust(capacity, b'\0')

    def patch(self, offset, value, capacity):
        if offset < 0 or offset + len(value) > capacity:
            raise SnapshotFormatError('Field exceeds its reviewed record window')
        # Preserve excess/unknown bytes. A previously absent field requires only
        # enough zero padding to expose that field, matching native Load zeros.
        raw = bytearray(self.data.ljust(offset + len(value), b'\0'))
        raw[offset:offset + len(value)] = value
        self.data = bytes(raw)


@dataclass
class ModuleSnapshot:
    header: _Block
    controls: bytes
    parent_module_index: int
    original_snapshot_index: int
    player_index_plus_one: int

    def byte_size(self):
        return 2 + self.header.byte_size() + len(self.controls) + 12

    @classmethod
    def _read(cls, reader):
        reader.token(0xda)
        header = reader.block()
        count = _integer(struct.unpack_from('<i', header.effective(58), 44)[0],
                         'module controller count', 0, MAX_ITEMS)
        reader.total_items += count
        if reader.total_items > reader.item_limit:
            raise SnapshotFormatError('Controller count exceeds aggregate bound')
        controls = reader.read(count * 8)
        return cls(header, controls, reader.i32(), reader.i32(), reader.i32())

    def to_bytes(self):
        count = struct.unpack_from('<i', self.header.effective(58), 44)[0]
        if count < 0 or len(self.controls) != count * 8:
            raise SnapshotFormatError('Module controller count/extent disagree')
        return (b'\xda\0' + self.header.to_bytes() + self.controls
                + struct.pack('<iii', self.parent_module_index,
                              self.original_snapshot_index, self.player_index_plus_one))

    def inspect(self):
        header = self.header.effective(58)
        return {'type_id': struct.unpack_from('<i', header)[0],
                'controller_count': len(self.controls) // 8,
                'parent_module_index': self.parent_module_index,
                'original_snapshot_index': self.original_snapshot_index,
                'player_index_plus_one': self.player_index_plus_one,
                'header_raw_hex': self.header.data.hex(),
                'controllers': [{'parameter': p, 'module': m, 'band': b, 'flags': f,
                                 'value_raw_hex': raw.hex()}
                                for p, m, b, f, raw in struct.iter_unpack('<BBBB4s', self.controls)]}


@dataclass
class ChannelSnapshot:
    own_modules: _Block
    modules: list
    metadata: _Block

    def byte_size(self):
        modules = sum(item.byte_size() for item in self.modules) if self.own_modules.effective(1)[0] else 4 * len(self.modules)
        return 2 + self.own_modules.byte_size() + 4 + modules + self.metadata.byte_size()

    @classmethod
    def _read(cls, reader):
        reader.token(0xd5)
        own = reader.block()
        flag = own.effective(1)[0]
        if flag not in (0, 1):
            raise SnapshotFormatError('OwnModuleSnapshots is not a Boolean')
        count = reader.count()
        modules = [ModuleSnapshot._read(reader) if flag else reader.i32()
                   for _ in range(count)]
        return cls(own, modules, reader.block())

    def to_bytes(self):
        own = self.own_modules.effective(1)[0]
        modules = b''.join(item.to_bytes() if own else struct.pack('<i', item)
                           for item in self.modules)
        return (b'\xd5\0' + self.own_modules.to_bytes()
                + struct.pack('<i', len(self.modules)) + modules + self.metadata.to_bytes())

    @property
    def channel_id(self):
        return struct.unpack_from('<H', self.metadata.effective(39))[0]

    def inspect(self):
        raw = self.metadata.effective(39)
        return {'channel_id': self.channel_id, 'own_modules': bool(self.own_modules.effective(1)[0]),
                'module_count': len(self.modules),
                'module_pool_indices': list(self.modules) if not self.own_modules.effective(1)[0] else None,
                'channel_name_raw_hex': raw[3:3 + min(raw[2], 31)].hex(),
                'preset_index': struct.unpack_from('<i', raw, 34)[0],
                'stereo_mode_raw': raw[38], 'metadata_raw_hex': self.metadata.data.hex()}


@dataclass
class ConsoleSnapshot:
    channels: list[ChannelSnapshot]
    name: _Block
    timestamp: bytes
    controller_scope: _Block
    flags: _Block
    midi: bytes
    relays: _Block
    layout: _Block
    timing: _Block
    notes: _Block
    allocations: _Block

    def byte_size(self):
        return (2 + 4 + sum(channel.byte_size() for channel in self.channels)
                + self.name.byte_size() + len(self.timestamp) + self.controller_scope.byte_size()
                + self.flags.byte_size() + 4 + len(self.midi) + self.relays.byte_size()
                + self.layout.byte_size() + self.timing.byte_size() + self.notes.byte_size()
                + self.allocations.byte_size())

    @classmethod
    def _read(cls, reader):
        reader.token(0xd3)
        channels = [ChannelSnapshot._read(reader) for _ in range(reader.count(MAX_CHANNELS))]
        name = reader.block()
        if len(name.data) > 255:
            raise SnapshotFormatError('Snapshot ShortString input exceeds native byte bound')
        timestamp = reader.read(8)
        scope, flags = reader.block(), reader.block()
        midi_length = _integer(reader.i32(), 'MIDI byte extent', 0, MAX_STREAM_BYTES)
        midi = reader.read(midi_length)
        relays, layout, timing = reader.block(), reader.block(), reader.block()
        notes, allocations = reader.block(), reader.block()
        if len(allocations.data) % 4:
            raise SnapshotFormatError('Nodal allocation bytes are not whole uint32 entries')
        return cls(channels, name, timestamp, scope, flags, midi, relays,
                   layout, timing, notes, allocations)

    @classmethod
    def from_bytes(cls, data, version=2242):
        reader = _Reader(data, version)
        result = cls._read(reader)
        reader.finish()
        return result

    def to_bytes(self):
        if self.byte_size() > MAX_STREAM_BYTES:
            raise SnapshotFormatError('Console Snapshot exceeds its byte bound')
        return (b'\xd3\0' + struct.pack('<i', len(self.channels))
                + b''.join(channel.to_bytes() for channel in self.channels)
                + self.name.to_bytes() + self.timestamp + self.controller_scope.to_bytes()
                + self.flags.to_bytes() + struct.pack('<i', len(self.midi)) + self.midi
                + self.relays.to_bytes() + self.layout.to_bytes() + self.timing.to_bytes()
                + self.notes.to_bytes() + self.allocations.to_bytes())

    def inspect(self, encoding='ascii', include_channels=True):
        if type(include_channels) is not bool:
            raise SnapshotFormatError('include_channels must be Boolean')
        raw = self.timing.effective(81)
        try:
            name = self.name.data[:31].decode(encoding, errors='strict')
        except UnicodeError:
            name = None
        flags = struct.unpack('<H', self.flags.effective(2))[0]
        number_raw = struct.unpack_from('<i', raw)[0]
        result = {'name': name, 'name_raw_hex': self.name.data.hex(),
                'number_raw': number_raw, 'number': number_raw / 100, 'group': raw[6],
                'skip_previous_next': bool(raw[7]),
                'recall_at': struct.unpack_from('<i', raw, 10)[0],
                'recall_at_active': bool(flags & 0x80),
                'duration': struct.unpack_from('<i', raw, 14)[0],
                'flags': flags, 'locked': bool(flags & 0x2000),
                'channel_count': len(self.channels), 'midi_bytes': len(self.midi),
                'notes_bytes': len(self.notes.data), 'allocation_bytes': len(self.allocations.data)}
        if include_channels:
            result['channels'] = [channel.inspect() for channel in self.channels]
        return result

    def rename(self, namebytes):
        if not isinstance(namebytes, bytes) or len(namebytes) > 31 or b'\0' in namebytes:
            raise SnapshotFormatError('Snapshot name must be at most31 encoded bytes without NUL')
        self.name.data = namebytes

    def timecode(self, quarterframes, active=True):
        _integer(quarterframes, 'RecallAt quarterframes', 0, 0x7fffffff)
        if type(active) is not bool:
            raise SnapshotFormatError('RecallAt active must be Boolean')
        self.timing.patch(10, struct.pack('<i', quarterframes), 81)
        flags = struct.unpack('<H', self.flags.effective(2))[0]
        flags = (flags | 0x80) if active else (flags & ~0x80)
        self.flags.patch(0, struct.pack('<H', flags), 2)


@dataclass
class SnapshotSection:
    version: int
    modules: list[ModuleSnapshot]
    unused_channels: list[ChannelSnapshot]
    groups: list[_Block]
    snapshots: list[ConsoleSnapshot]
    options: _Block
    section_scope: _Block
    section_exclusions: _Block
    outer_tag: bool = True

    @classmethod
    def empty(cls, version):
        """Build the empty automation lists and observed factory options."""
        if version not in SUPPORTED_VERSIONS:
            raise SnapshotFormatError('Unreviewed Snapshot Store version')
        options = bytearray(237)
        struct.pack_into('<iii', options, 0, -1, 0, -1)
        options[30] = 1
        options[31] = 2 if version == 2242 else 0
        options[62] = 0x22
        for offset in range(110, 158, 4):
            struct.pack_into('<I', options, offset, 0x1fffffff)
        struct.pack_into('<i', options, 165, -1)
        struct.pack_into('<h', options, 171, -1)
        struct.pack_into('<ii', options, 211, -1, -1)
        struct.pack_into('<BH', options, 231, 1, 419)
        scope = (struct.pack('<II', 0x7c4873be, 0x7c4873be) + bytes([255]) * 64) * 4
        exclusions = bytearray(264)
        for offset in range(23, 264, 66):
            exclusions[offset] = 0x22
        return cls(version, [], [], [], [], _Block(bytes(options)),
                   _Block(scope), _Block(bytes(exclusions)))

    def byte_size(self):
        return ((4 if self.outer_tag else 2) + 16
                + sum(item.byte_size() for item in self.modules)
                + sum(item.byte_size() for item in self.unused_channels)
                + sum(item.byte_size() for item in self.groups)
                + sum(item.byte_size() for item in self.snapshots)
                + self.options.byte_size() + self.section_scope.byte_size()
                + self.section_exclusions.byte_size())

    @classmethod
    def from_bytes(cls, data, version=2242, *, outer_tag=True):
        reader = _Reader(data, version)
        reader.token(0xd2)
        if type(outer_tag) is not bool:
            raise SnapshotFormatError('outer_tag must be explicit Boolean')
        if outer_tag:
            reader.token(0xd2)
        modules = [ModuleSnapshot._read(reader) for _ in range(reader.count())]
        channels = [ChannelSnapshot._read(reader) for _ in range(reader.count(MAX_CHANNELS))]
        groups = [reader.block() for _ in range(reader.count(255))]
        snapshots = [ConsoleSnapshot._read(reader) for _ in range(reader.count(MAX_SNAPSHOTS))]
        result = cls(version, modules, channels, groups, snapshots,
                     reader.block(), reader.block(), reader.block(), outer_tag)
        reader.finish()
        result.validate_references()
        return result

    def to_bytes(self):
        if len(self.snapshots) > MAX_SNAPSHOTS or self.byte_size() > MAX_STREAM_BYTES:
            raise SnapshotFormatError('Snapshot section exceeds its count/byte bound')
        self.validate_references()
        return ((b'\xd2\0' if self.outer_tag else b'') + b'\xd2\0'
                + struct.pack('<i', len(self.modules)) + b''.join(x.to_bytes() for x in self.modules)
                + struct.pack('<i', len(self.unused_channels))
                + b''.join(x.to_bytes() for x in self.unused_channels)
                + struct.pack('<i', len(self.groups)) + b''.join(x.to_bytes() for x in self.groups)
                + struct.pack('<i', len(self.snapshots))
                + b''.join(x.to_bytes() for x in self.snapshots)
                + self.options.to_bytes() + self.section_scope.to_bytes()
                + self.section_exclusions.to_bytes())

    def _all_modules(self):
        yield from self.modules
        for channel in self.unused_channels:
            if channel.own_modules.effective(1)[0]:
                yield from channel.modules
        for snapshot in self.snapshots:
            for channel in snapshot.channels:
                if channel.own_modules.effective(1)[0]:
                    yield from channel.modules

    def _shared_user_updates(self, channels, delta):
        """Mirror native AddSnapshot/RemoveSnapshot per shared channel edge."""
        references = Counter(index for channel in channels
                             if not channel.own_modules.effective(1)[0]
                             for index in channel.modules if index != -1)
        updates = []
        for index, occurrences in references.items():
            _integer(index, 'shared module reference', 0, len(self.modules)-1)
            module = self.modules[index]
            old = struct.unpack_from('<i', module.header.effective(58), 54)[0]
            new = old + delta * occurrences
            _integer(old, 'stored shared module UserCount', 0, 0x7fffffff)
            _integer(new, 'updated shared module UserCount', 0, 0x7fffffff)
            updates.append((module, new))
        return updates

    def validate_references(self):
        # CTList.Load appends a module only after its LoadItem returns. A shared
        # parent must therefore have been loaded earlier, not be self/future.
        for index, module in enumerate(self.modules):
            _integer(module.parent_module_index, 'earlier shared parent module reference', -1, index-1)
        channels = list(self.unused_channels)
        channels += [channel for snapshot in self.snapshots for channel in snapshot.channels]
        for channel in channels:
            if not channel.own_modules.effective(1)[0]:
                for index in channel.modules:
                    _integer(index, 'shared module reference', -1, len(self.modules)-1)
        for module in self._all_modules():
            _integer(module.parent_module_index, 'parent module reference', -1, len(self.modules)-1)
            _integer(module.original_snapshot_index, 'original Snapshot reference', -1, len(self.snapshots)-1)
            _integer(module.player_index_plus_one, 'Player reference', 0, 0x7fffffff)
        return True

    def inspect(self, encoding='ascii', include_channels=True):
        if type(include_channels) is not bool:
            raise SnapshotFormatError('include_channels must be Boolean')
        options = self.options.effective(237)
        return {'version': self.version, 'snapshot_count': len(self.snapshots),
                'module_pool_count': len(self.modules), 'unused_channel_count': len(self.unused_channels),
                'group_count': len(self.groups),
                'current_snapshot_index': struct.unpack_from('<i', options)[0],
                'undo_snapshot_index': struct.unpack_from('<i', options, 4)[0],
                'surface_snapshot_index': struct.unpack_from('<i', options, 8)[0],
                'scroll_index': struct.unpack_from('<i', options, 12)[0],
                'snapshots': [snapshot.inspect(encoding, include_channels=include_channels) for snapshot in self.snapshots],
                'opaque_records_preserved': True, 'native_load_verified': False}

    def _snapshot(self, index):
        return self.snapshots[_integer(index, 'Snapshot index', 0, len(self.snapshots)-1)]

    def _editable_snapshot(self, index):
        snapshot = self._snapshot(index)
        if struct.unpack('<H', snapshot.flags.effective(2))[0] & 0x2000:
            raise SnapshotFormatError('Snapshot is locked; editing/deletion is refused')
        return snapshot

    def rename(self, index, namebytes):
        self._editable_snapshot(index).rename(namebytes)
        return self

    def timecode(self, index, quarterframes, active=True):
        self._editable_snapshot(index).timecode(quarterframes, active)
        return self

    def set_number(self, index, integer):
        """Set a positive whole displayed Cue number (native stores ×100)."""
        number = _integer(integer, 'displayed whole Cue number', 1, 0x7fffffff // 100) * 100
        snapshot = self._editable_snapshot(index)
        if any(item is not snapshot and struct.unpack_from('<i', item.timing.effective(81))[0] == number
               for item in self.snapshots):
            raise SnapshotFormatError('Displayed Cue number is already in use')
        snapshot.timing.patch(0, struct.pack('<i', number), 81)
        return self

    def delete(self, index):
        snapshot = self._editable_snapshot(index)
        updates = self._shared_user_updates(snapshot.channels, -1)
        # Native Delete first clears module origins of the removed object; later
        # Store regenerates surviving object indices. Module pool order stays.
        for module in self._all_modules():
            old = module.original_snapshot_index
            if old == index:
                module.original_snapshot_index = -1
            elif old > index:
                module.original_snapshot_index -= 1
        del self.snapshots[index]
        options = self.options.effective(237)
        current, surface = struct.unpack_from('<i', options)[0], struct.unpack_from('<i', options, 8)[0]
        if current == index:
            current = -1
        elif current > index:
            current -= 1
        if surface >= index:
            surface -= 1
        self.options.patch(0, struct.pack('<i', current), 237)
        self.options.patch(8, struct.pack('<i', surface), 237)
        for module, count in updates:
            module.header.patch(54, struct.pack('<i', count), 58)
        return self

    def duplicate(self, source_index, index):
        source = copy.deepcopy(self._snapshot(source_index))
        return self._insert(source, index)

    def _insert(self, snapshot, index):
        _integer(index, 'Snapshot insertion index', 0, len(self.snapshots))
        if len(self.snapshots) >= MAX_SNAPSHOTS or self.byte_size() + snapshot.byte_size() > MAX_STREAM_BYTES:
            raise SnapshotFormatError('Snapshot insertion exceeds its count/byte bound')
        candidate = copy.deepcopy(self)
        updates = candidate._shared_user_updates(snapshot.channels, 1)
        for module in candidate._all_modules():
            if module.original_snapshot_index >= index:
                module.original_snapshot_index += 1
        for channel in snapshot.channels:
            if channel.own_modules.effective(1)[0]:
                for module in channel.modules:
                    if module.original_snapshot_index >= index:
                        module.original_snapshot_index += 1
        # Keep selections pointing at the same old objects; do not implicitly
        # select/recall the newly inserted Snapshot in a file-editing operation.
        options = candidate.options.effective(237)
        for offset in (0, 8):
            old = struct.unpack_from('<i', options, offset)[0]
            if old >= index:
                candidate.options.patch(offset, struct.pack('<i', old+1), 237)
        # The native current-state D3 has Number0. Choose a fresh positive whole
        # display number without renumbering any existing cue. This is an
        # explicit file-authoring policy, not a claim of reproducing native
        # midpoint numbering or MIDI-control-number allocation.
        maximum = max((struct.unpack_from('<i', item.timing.effective(81))[0]
                       for item in candidate.snapshots), default=0)
        displayed = max(0, maximum) // 100 + 1
        _integer(displayed, 'new displayed Cue number', 1, 0x7fffffff // 100)
        snapshot.timing.patch(0, struct.pack('<i', displayed * 100), 81)
        candidate.snapshots.insert(index, snapshot)
        for module, count in updates:
            module.header.patch(54, struct.pack('<i', count), 58)
        candidate.validate_references()
        if candidate.byte_size() > MAX_STREAM_BYTES:
            raise SnapshotFormatError('Snapshot insertion exceeds its byte bound after metadata expansion')
        self.__dict__.update(candidate.__dict__)
        return self

    def create_from_current_console(self, section_d3, index=None):
        """Clone the same file's native current-state Snapshot representation.

        The caller must supply the corresponding D3 section from this same file.
        No current console state is synthesized from controller names or values.
        """
        snapshot = ConsoleSnapshot.from_bytes(section_d3, version=self.version)
        if not all(channel.own_modules.effective(1)[0] for channel in snapshot.channels):
            raise SnapshotFormatError('Current-state source contains non-owned module references')
        return self._insert(snapshot, len(self.snapshots) if index is None else index)


@dataclass
class _Preset:
    channels: list[ChannelSnapshot]
    name: _Block
    group: _Block
    notes: _Block
    metadata: _Block

    def to_bytes(self):
        return (b'\xd7\0' + struct.pack('<i', len(self.channels))
                + b''.join(channel.to_bytes() for channel in self.channels)
                + self.name.to_bytes() + self.group.to_bytes()
                + self.notes.to_bytes() + self.metadata.to_bytes())

    def byte_size(self):
        return (6 + sum(channel.byte_size() for channel in self.channels)
                + sum(block.byte_size() for block in (self.name, self.group, self.notes, self.metadata)))


@dataclass
class PresetSection:
    """Minimal DB/D7 Store structure, used to detect external Snapshot origins.

    Old Q1 tPreset.Store B776F8: inherited channel list, three ShortStrings,
    then length-record14 from object+164. No Preset semantic editing is exposed.
    """
    version: int
    presets: list[_Preset]

    @classmethod
    def from_bytes(cls, data, version=2242):
        reader = _Reader(data, version)
        reader.token(0xdb)
        presets = []
        for _ in range(reader.count(10000)):
            reader.token(0xd7)
            channels = [ChannelSnapshot._read(reader) for _ in range(reader.count(MAX_CHANNELS))]
            presets.append(_Preset(channels, reader.block(), reader.block(), reader.block(), reader.block()))
        reader.finish()
        return cls(version, presets)

    def byte_size(self):
        return 6 + sum(preset.byte_size() for preset in self.presets)

    def to_bytes(self):
        if self.byte_size() > MAX_STREAM_BYTES:
            raise SnapshotFormatError('Preset section exceeds its byte bound')
        return b'\xdb\0' + struct.pack('<i', len(self.presets)) + b''.join(preset.to_bytes() for preset in self.presets)

    def assert_no_snapshot_origin_refs(self):
        count = 0
        for preset in self.presets:
            for channel in preset.channels:
                if not channel.own_modules.effective(1)[0]:
                    raise SnapshotFormatError('Advanced Snapshot edits require independently owned Preset modules')
                for module in channel.modules:
                    count += 1
                    if module.original_snapshot_index != -1:
                        raise SnapshotFormatError('Preset DB contains external Snapshot origin references')
        return count


@dataclass
class _Setlist:
    name_raw: bytes
    snapshot_indices: list[int]


@dataclass
class SnapshotSetlists:
    """vO tag73: raw32-byte name, child count/i32 Snapshot indices, top current.

    Store9A5520/9A55B8 and EntryStore9A40C8 independently establish that disk
    entries are main-list ordinals, not the heap pointers retained at RAM+4.
    """
    lists: list[_Setlist]
    current_setlist_index: int

    @classmethod
    def from_bytes(cls, data, version=2242):
        if version != 2242:
            raise SnapshotFormatError('SetList disk layout is reviewed only for vO')
        reader = _Reader(data, version)
        reader.token(0x73)
        lists = []
        for _ in range(reader.count(10000)):
            name = reader.read(32)
            if name[0] > 31:
                raise SnapshotFormatError('SetList name exceeds native ShortString31')
            indices = [reader.i32() for _ in range(reader.count())]
            if any(index < -1 for index in indices):
                raise SnapshotFormatError('SetList has invalid negative Snapshot index')
            lists.append(_Setlist(name, indices))
        current = reader.i32()
        _integer(current, 'current SetList index', -1, len(lists)-1)
        reader.finish()
        return cls(lists, current)

    def byte_size(self):
        return 10 + sum(36 + 4 * len(item.snapshot_indices) for item in self.lists)

    def to_bytes(self):
        if self.byte_size() > MAX_STREAM_BYTES:
            raise SnapshotFormatError('SetList section exceeds its byte bound')
        return (b'\x73\0' + struct.pack('<i', len(self.lists))
                + b''.join(item.name_raw + struct.pack('<i', len(item.snapshot_indices))
                           + b''.join(struct.pack('<i', index) for index in item.snapshot_indices)
                           for item in self.lists)
                + struct.pack('<i', self.current_setlist_index))

    def validate_snapshot_count(self, count):
        _integer(count, 'main Snapshot count', 0, MAX_SNAPSHOTS)
        for item in self.lists:
            for index in item.snapshot_indices:
                _integer(index, 'SetList Snapshot reference', 0, count-1)
        return True

    def remap(self, index_map):
        if not isinstance(index_map, dict):
            raise SnapshotFormatError('Snapshot ordinal remap must be an explicit dictionary')
        for old, new in index_map.items():
            _integer(old, 'old Snapshot index', 0, MAX_SNAPSHOTS-1)
            _integer(new, 'new Snapshot index', -1, MAX_SNAPSHOTS-1)
        updated = []
        for item in self.lists:
            values = []
            for old in item.snapshot_indices:
                if old not in index_map:
                    raise SnapshotFormatError('SetList Snapshot reference lacks explicit remapping')
                new = index_map[old]
                if new != -1:
                    values.append(new)
            updated.append(values)
        for item, values in zip(self.lists, updated):
            item.snapshot_indices = values
        return self

    def insert_snapshot(self, index, old_count):
        _integer(index, 'insertion index', 0, old_count)
        self.validate_snapshot_count(old_count)
        return self.remap({old: old + (old >= index) for old in range(old_count)})

    def delete_snapshot(self, index, old_count):
        _integer(index, 'deletion index', 0, old_count-1)
        self.validate_snapshot_count(old_count)
        return self.remap({old: -1 if old == index else old - (old > index) for old in range(old_count)})

    def reorder_snapshots(self, order):
        if (not isinstance(order, list) or any(type(index) is not int for index in order)
                or sorted(order) != list(range(len(order)))):
            raise SnapshotFormatError('Snapshot reorder must be a complete permutation')
        self.validate_snapshot_count(len(order))
        return self.remap({old: new for new, old in enumerate(order)})


def assert_no_external_snapshot_refs(*, version, current_section, preset_section,
                                     setlist_section=None):
    """Conservative whole-file admission before any D2 count/order change.

    These external streams can contain main-Snapshot indices. This first
    implementation preserves them byte-for-byte and admits only independence;
    it does not guess a rewrite for a nonempty SetList or an external origin.
    Rename/timecode/number edits do not change main-list ordinals and do not
    require this structural-operation guard.
    """
    current = ConsoleSnapshot.from_bytes(current_section, version=version)
    current_modules = 0
    for channel in current.channels:
        if not channel.own_modules.effective(1)[0]:
            raise SnapshotFormatError('Advanced Snapshot edits require owned current-state modules')
        for module in channel.modules:
            current_modules += 1
            if module.original_snapshot_index != -1:
                raise SnapshotFormatError('Current-state D3 contains external Snapshot origin references')
    presets = PresetSection.from_bytes(preset_section, version=version)
    preset_count = len(presets.presets)
    preset_modules = presets.assert_no_snapshot_origin_refs()
    if setlist_section is None:
        if version != 2126:
            raise SnapshotFormatError('vO advanced edits require the reviewed SetList section')
    else:
        lists = SnapshotSetlists.from_bytes(setlist_section, version=version)
        if lists.lists:
            raise SnapshotFormatError('Nonempty SetList contains external Snapshot indices; advanced edits refused')
    return {'external_snapshot_references_absent': True,
            'current_module_count': current_modules, 'preset_count': preset_count,
            'preset_module_count': preset_modules, 'setlist_count': 0,
            'external_sections_rewritten': False}

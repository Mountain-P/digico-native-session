"""Independent preset edits, processing copies, and serialized reference shifts."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/digico-native-session/scripts'))
from ses_presets import PresetFileError, SessionPresets
from ses_routing import C9Channel, C9Console, C9Group, SocketGroups
from ses_snapshot import ChannelSnapshot, ConsoleSnapshot, ModuleSnapshot, SnapshotSection, _Block


def controller(prefix, value, flags=0):
    return bytes.fromhex(prefix) + bytes([flags]) + struct.pack('<f', value)


def module(type_id, *records, origin=-1):
    header = bytearray(58)
    struct.pack_into('<i', header, 0, type_id)
    struct.pack_into('<i', header, 44, len(records))
    header[53] = 1
    return ModuleSnapshot(_Block(bytes(header)), b''.join(records), -1, origin, 0)


def channel(channel_id, reference=-1, *, trim=0, fader=-12, mute=0, route=-1):
    metadata = bytearray(39)
    struct.pack_into('<H', metadata, 0, channel_id)
    metadata[2:8] = b'\x05Input'
    struct.pack_into('<iB', metadata, 34, reference, 1)
    modules = [
        module(1, controller('010100', trim), controller('000100', route)),
        module(5, controller('0e0500', -24), controller('160500', route)),
        module(12, controller('210c00', fader), controller('220c00', mute)),
    ]
    return ChannelSnapshot(_Block(b'\x01'), modules, _Block(bytes(metadata)))


def console(*channels):
    empty = lambda: _Block(b'')
    return ConsoleSnapshot(list(channels), empty(), struct.pack('<d', 46000),
                           empty(), empty(), b'', empty(), empty(), empty(), empty(), empty())


def topology(version, *channels):
    records = []
    for source in channels:
        metadata = bytearray(120 if version == 2126 else 121)
        struct.pack_into('<HH', metadata, 0, source.channel_id, source.channel_id - 0x100)
        metadata[4:10] = b'\x05Input'
        metadata[0x25] = 1
        struct.pack_into('<i', metadata, 0x26, source.inspect()['preset_index'])
        records.append(C9Channel(bytes(metadata)))
    group_metadata = b'\x01\x06Inputs' + bytes(100)
    empty = SocketGroups(b'', ())
    state = C9Console(version, empty, empty, [C9Group(0xca, group_metadata, records)])
    return C9Console.from_bytes(state.to_bytes(), version)


def values(channel):
    return {record[:3].hex(): struct.unpack('<f', record[4:])[0]
            for module in channel.modules
            for offset in range(0, len(module.controls), 8)
            for record in (module.controls[offset:offset+8],)}


class PresetFileTests(unittest.TestCase):
    def test_capture_apply_reparse_preserves_source_and_excludes_routes(self):
        for version in (2126, 2242):
            with self.subTest(version=version):
                first = channel(0x100, trim=3, fader=-18, mute=1, route=11)
                second = channel(0x101, fader=-9, route=22)
                current = console(first, second)
                before = current.to_bytes()
                library = SessionPresets.from_bytes(b'\xdb\0' + bytes(4), version)
                index = library.capture(current, 0x100, name='人聲'.encode('cp950'), group=b'Synthetic')
                self.assertEqual(current.to_bytes(), before)
                library = SessionPresets.from_bytes(library.to_bytes(), version)
                row = library.inspect('cp950', preset_index=index, include_raw_controls=True)['presets'][0]
                self.assertEqual(row['name'], '人聲')
                self.assertEqual(row['lock_flag'], 0)
                self.assertEqual(row['channel_count'], 1)
                self.assertEqual(row['channels'][0]['preset_index'], -1)
                self.assertEqual(library.apply(index, current, 0x101,
                                               scopes=['trim', 'fader', 'dynamics']), 4)
                persisted = ConsoleSnapshot.from_bytes(current.to_bytes(), version)
                result = values(persisted.channels[1])
                self.assertEqual((result['010100'], result['210c00'], result['220c00']), (3, -18, 1))
                self.assertEqual((result['000100'], result['160500']), (22, 22))

    def test_update_rename_and_lock_preserve_unedited_content(self):
        original = console(channel(0x100, trim=3, fader=-18))
        library = SessionPresets(2242, [])
        index = library.capture(original, 0x100, name=b'Original', group=b'Group', notes=b'Notes')
        updated = console(channel(0x100, trim=6, fader=-21))
        library.update(index, updated, 0x100, scopes=['fader'])
        library.rename(index, b'Renamed')
        library.set_lock(index, True)
        locked = library.to_bytes()
        with self.assertRaisesRegex(PresetFileError, 'locked'):
            library.rename(index, b'No change')
        self.assertEqual(library.to_bytes(), locked)
        library = SessionPresets.from_bytes(locked, 2242)
        library.set_lock(index, False)
        preset = library.presets[index]
        self.assertEqual((preset.name.data, preset.group.data, preset.notes.data),
                         (b'Renamed', b'Group', b'Notes'))
        self.assertEqual(values(preset.channels[0])['010100'], 3)
        self.assertEqual(values(preset.channels[0])['210c00'], -21)
        preset.metadata.patch(8, b'\x02', 14)
        with self.assertRaisesRegex(PresetFileError, 'factory protected'):
            library.set_lock(index, False)

    def test_apply_incompatible_layout_fails_before_writing_any_values(self):
        current = console(channel(0x100, trim=3, fader=-18), channel(0x101))
        library = SessionPresets(2242, [])
        index = library.capture(current, 0x100, name=b'Test', group=b'', scopes=['trim', 'fader'])
        destination = current.channels[1].modules[2]
        destination.controls = destination.controls[:8]  # No Mute record.
        destination.header.patch(44, struct.pack('<i', 1), 58)
        before = current.to_bytes()
        with self.assertRaisesRegex(PresetFileError, 'layouts differ'):
            library.apply(index, current, 0x101, scopes=['trim', 'fader'])
        self.assertEqual(current.to_bytes(), before)

    def test_delete_remaps_c9_d3_d2_and_nested_db_indices(self):
        for version in (2126, 2242):
            with self.subTest(version=version):
                current = console(channel(0x100, 0), channel(0x101, 2))
                routing = topology(version, *current.channels)
                snapshots = SnapshotSection.empty(version)
                snapshots.snapshots = [console(channel(0x100, 1), channel(0x101, 2))]
                snapshots.unused_channels = [channel(0x100, 1)]
                library = SessionPresets(version, [])
                for name in (b'Keep 0', b'Delete 1', b'Keep 2'):
                    library.capture(current, 0x100, name=name, group=b'', scopes=['trim'])
                library.presets[2].channels[0].metadata.patch(34, struct.pack('<i', 2), 39)
                library.delete(1, current=current, snapshots=snapshots, routing=routing)
                routing = C9Console.from_bytes(routing.to_bytes(), version)
                current = ConsoleSnapshot.from_bytes(current.to_bytes(), version)
                snapshots = SnapshotSection.from_bytes(snapshots.to_bytes(), version)
                library = SessionPresets.from_bytes(library.to_bytes(), version)
                self.assertEqual([item.name.data for item in library.presets], [b'Keep 0', b'Keep 2'])
                self.assertEqual([item.preset_index for item in routing.groups[0].channels], [0, 1])
                self.assertEqual([item.inspect()['preset_index'] for item in current.channels], [0, 1])
                self.assertEqual([item.inspect()['preset_index'] for item in snapshots.snapshots[0].channels], [-1, 1])
                self.assertEqual(snapshots.unused_channels[0].inspect()['preset_index'], -1)
                self.assertEqual(library.presets[1].channels[0].inspect()['preset_index'], 1)

    def test_delete_bad_reference_is_atomic(self):
        current = console(channel(0x100, 1), channel(0x101, 0))
        routing = topology(2242, *current.channels)
        routing.groups[0].channels[1].set_preset_index(2)
        snapshots = SnapshotSection.empty(2242)
        library = SessionPresets(2242, [])
        for name in (b'First', b'Second'):
            library.capture(current, 0x100, name=name, group=b'')
        before = [item.to_bytes() for item in (current, routing, snapshots, library)]
        with self.assertRaisesRegex(PresetFileError, 'invalid Preset positional reference'):
            library.delete(0, current=current, snapshots=snapshots, routing=routing)
        self.assertEqual([item.to_bytes() for item in (current, routing, snapshots, library)], before)


if __name__ == '__main__':
    unittest.main()

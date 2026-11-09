"""Inspect saved Q5 Macro and clock configuration without running commands."""
from __future__ import annotations

from collections import Counter
import struct

from ses_routing import C9Console
from ses_snapshot import SnapshotFormatError, _Reader


class SesAutomationError(SnapshotFormatError):
    pass


_REVIEWED_VERSIONS = (2025, 2242)
_MAX_MACRO_BYTES = 2 * 1024 * 1024
_MAX_MACRO_ITEMS = 16384
_MACRO_TAG = 0xf8
_AUDIO_SYNC_TAG = 0xe3
_AUDIO_SYNC_WIDTH = {2025: 15, 2242: 17}
_TIMECODE_SOURCES = {1: 'master', 2: 'smpte', 3: 'midi', 4: '9pin_direct', 5: '9pin_eavesdrop'}
_AUDIO_SYNC_SOURCES = {0: 'master', 10: 'aes_ebu', 12: 'word_clock', 13: 'video', 14: 'optocore'}


def _clock(sections, structure, version):
    timecode_source = structure['timecode_source_raw']
    audio_sync = None
    if _AUDIO_SYNC_TAG in sections:
        reader = _Reader(sections[_AUDIO_SYNC_TAG], version)
        record = reader.block().data
        reader.finish()
        if len(record) != _AUDIO_SYNC_WIDTH[version]:
            raise SesAutomationError('Unreviewed Q5 audio-sync record width')
        primary, secondary, select_secondary = struct.unpack_from('<HHB', record)
        selected = secondary if select_secondary else primary
        audio_sync = {'source': _AUDIO_SYNC_SOURCES.get(selected), 'source_raw': selected,
                      'primary_source_raw': primary, 'secondary_source_raw': secondary,
                      'select_secondary_raw': select_secondary}
    return {
        'timecode': {'source': _TIMECODE_SOURCES.get(timecode_source),
                     'source_raw': timecode_source, 'frames_per_second': structure['video_frame_rate']},
        'audio_sync': audio_sync,
        'sample_frequency': structure['sample_frequency'],
    }


def _channel_lookups(topology, encoding):
    inputs = [channel for group in topology.groups if group.group_id == 1 for channel in group.channels]
    auxes = [channel for group in topology.groups if group.group_id == 5 for channel in group.channels]
    input_counts = Counter(channel.logical_channel for channel in inputs)
    return (
        {channel.logical_channel: {'channel_id': channel.channel_id,
                                   'name': channel.name_bytes.decode(encoding, errors='replace')}
         for channel in inputs if input_counts[channel.logical_channel] == 1},
        {channel.channel_id & 255: {'channel_id': channel.channel_id,
                                    'name': channel.name_bytes.decode(encoding, errors='replace')}
         for channel in auxes},
    )


def _raw_item(reader):
    # Both native list item encodings include the length field itself.
    width = reader.u16()
    if width < 2:
        raise SesAutomationError('Invalid Macro list item width')
    return struct.pack('<H', width) + reader.read(width - 2)


def _command(raw, inputs, auxes):
    result = {'raw_hex': raw.hex(), 'effect': None}
    if len(raw) != 20:
        return result
    category, parameter, module, band, first, channel_type, last, value_type, value = (
        struct.unpack_from('<HBBBBBiBf', raw, 2))
    # Macro ranges use the native logical channel number, independent of the
    # low byte of a D3 channel ID. Category 1 targets Input controls.
    if (category != 1 or channel_type != 1 or value_type != 1
            or value not in (0.0, 1.0) or not 1 <= first <= last <= 255):
        return result
    if not all(number in inputs for number in range(first, last + 1)):
        return result
    target_range = {'first': first, 'last': last}
    if (parameter, module, band) == (0x0e, 1, 0):
        effect = {'operation': 'input_select', 'source': 'alt' if value else 'main'}
    elif (parameter, module) == (2, 8) and band in (0, 1):
        effect = {'operation': 'insert_in', 'insert': 'A' if band == 0 else 'B', 'enabled': bool(value)}
    elif (parameter, module) == (2, 2) and band in auxes:
        effect = {'operation': 'aux_send_on', 'aux_band': band, 'enabled': bool(value)}
    else:
        return result
    result['effect'] = {**effect, 'input_logical_range': target_range}
    return result


def _macros(raw, topology, encoding):
    if len(raw) > _MAX_MACRO_BYTES:
        raise SesAutomationError('Macro section exceeds its local byte bound')
    reader = _Reader(raw, topology.version)
    reader.item_limit = min(reader.item_limit, _MAX_MACRO_ITEMS)
    reader.token(_MACRO_TAG)
    inputs, auxes = _channel_lookups(topology, encoding)
    records = []
    for _ in range(reader.count()):
        header = reader.read(40)
        name_size = header[2]
        if name_size > 31:
            raise SesAutomationError('Stored Macro name exceeds ShortString31')
        name_bytes = header[3:3 + name_size]
        trigger_type, trigger_slot = header[34:36]
        trigger = {'type_raw': trigger_type, 'slot_raw': trigger_slot}
        if trigger_type in (3, 4):
            trigger.update(kind='smart_key', action='on' if trigger_type == 3 else 'off',
                           bank=trigger_slot // 10 + 1, key=trigger_slot % 10 + 1)
        elif trigger_type == 0x51:
            trigger['kind'] = 'snapshot'
        commands = [_command(_raw_item(reader), inputs, auxes) for _ in range(reader.count())]
        trigger_records = [_raw_item(reader).hex() for _ in range(reader.count())]
        records.append({'id': struct.unpack_from('<H', header)[0],
                        'name': name_bytes.decode(encoding, errors='replace'),
                        'name_raw_hex': name_bytes.hex(), 'header_raw_hex': header.hex(),
                        'trigger': trigger, 'commands': commands, 'trigger_records': trigger_records})
    trailer = reader.block().data
    reader.finish()
    if len(trailer) != 2:
        raise SesAutomationError('Unreviewed Macro trailer width')
    return {'count': len(records), 'records': records, 'trailer_raw_hex': trailer.hex(),
            'input_channels': inputs, 'aux_bands': auxes}


def inspect_automation(sections, topology, structure, encoding='cp950'):
    """Decode saved Q5 controls and preserve unreviewed Macro commands as bytes."""
    if not isinstance(topology, C9Console) or topology.version not in _REVIEWED_VERSIONS:
        raise SesAutomationError('Automation inspection requires a reviewed Q5 layout')
    if encoding not in ('ascii', 'cp950', 'utf-8'):
        raise SesAutomationError('Unsupported Macro name encoding')
    return {
        'version': topology.version,
        'clock': _clock(sections, structure, topology.version),
        'macros': (_macros(sections[_MACRO_TAG], topology, encoding) if _MACRO_TAG in sections else None),
    }

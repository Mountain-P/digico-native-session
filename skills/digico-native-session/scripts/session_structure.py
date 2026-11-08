"""Pure read-only tConsoleRec decoder for exact Quantum1 V2126.

123 bytes come from independently version-gated RPM or a decoded SessionVerify,
not arbitrary offsets in a .ses. This module does not create/restructure Sessions
or turn this record into a Category2 setter; no such command is established.
"""

from __future__ import annotations

from build_profiles import build_value, profile_bound

import math
import re
import struct


RECORD_BYTES = 123
TARGET_SHA256 = build_value('executable.sha256')
RECORD_RVA = build_value('addresses.hex_127a17c')
ALIAS_SLOT_RVA = build_value('addresses.hex_8e9980')


@profile_bound(feature='structure', documents=(), error_type=ValueError)
def decode_console_record(raw):
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TypeError('console record must be bytes-like')
    raw = bytes(raw)
    if len(raw) != RECORD_BYTES:
        raise ValueError('exact V2126 tConsoleRec requires123 bytes')
    sample_frequency, video_frame_rate = struct.unpack_from('<ff', raw, 0x53)
    if not math.isfinite(sample_frequency) or not math.isfinite(video_frame_rate):
        raise ValueError('console record contains nonfinite frequency/rate')
    return {
        'schema_version': 1, 'subsystem': 'native-session-structure-readonly',
        'executable_sha256': build_value('executable.sha256'),
        'record_rva': build_value('addresses.hex_127a17c'), 'record_bytes': RECORD_BYTES, 'raw_hex': raw.hex(),
        'stereo_mode_raw': raw[0],
        # Eight consecutive little-endian Word slots occupy each16-byte field.
        # This is raw structure inventory, without guessed mono/surround labels
        # or weighted DSP/resource totals for every slot.
        'input_count_slots': list(struct.unpack_from('<8H', raw, 0x02)),
        'aux_count_slots': list(struct.unpack_from('<8H', raw, 0x12)),
        'group_count_slots': list(struct.unpack_from('<8H', raw, 0x22)),
        'native_count_slot_indices': list(range(1, 9)),
        'native_count_slot_indices_verified': True,
        'count_slot_labels_verified': False,
        'timecode_source_raw': raw[0x3A], 'frame_rate_index_raw': raw[0x3B],
        'sample_frequency': sample_frequency, 'video_frame_rate': video_frame_rate,
        'bussing_mode_raw': raw[0x63],
        'soft_channels': raw[0x6E], 'matrix_outputs': raw[0x6F],
        'control_groups': raw[0x70], 'multi_groups': raw[0x71],
        'matrix_inputs': raw[0x72], 'graphic_eqs': raw[0x73],
        'headless_structure_setter_established': False,
    }


@profile_bound(feature='structure', documents=('readback',), error_type=ValueError)
def decode_structure_readback(readback):
    """Decode a validated native_structure.ps1 result, preserving RPM provenance.

    This checks the caller's copied evidence, not a live process. The host must
    still use a fresh successful runner result bound to its compiled request.
    """
    if not isinstance(readback, dict):
        raise TypeError('structure readback must be an object')
    if (type(readback.get('schema_version')) is not int or readback['schema_version'] != 1
            or readback.get('subsystem') != 'native-session-structure-readonly'
            or readback.get('executable_sha256') != build_value('executable.sha256')
            or type(readback.get('alias_slot_rva')) is not int or readback['alias_slot_rva'] != build_value('addresses.hex_8e9980')
            or type(readback.get('record_rva')) is not int or readback['record_rva'] != build_value('addresses.hex_127a17c')
            or type(readback.get('record_bytes')) is not int or readback['record_bytes'] != RECORD_BYTES):
        raise ValueError('structure readback differs from the exact V2126 profile')
    if any(readback.get(key) is not True for key in
           ('executable_hash_verified_by_caller','alias_verified','consistent_double_read','memory_read_only')):
        raise ValueError('structure readback lacks required hash/alias/read consistency evidence')
    if any(readback.get(key) is not False for key in ('ui_used','network_used','headless_structure_setter_established')):
        raise ValueError('structure readback is not the supported read-only operation')
    raw_hex=readback.get('raw_hex')
    if not isinstance(raw_hex,str) or not re.fullmatch('[0-9a-fA-F]{246}',raw_hex):
        raise ValueError('structure readback requires exactly123 copied raw bytes')
    return {**readback, **decode_console_record(bytes.fromhex(raw_hex))}

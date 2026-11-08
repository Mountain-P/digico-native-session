# Exact Quantum1 V2126 structure readback. Dot-source functions only.
# Caller must first hash-gate the executable and bind read-only $reader/$base.
# No target execution, sockets, UI, files or process-memory writes.
function ConvertTo-NativeStructureHex([byte[]]$Bytes) {
    return ([BitConverter]::ToString($Bytes)).Replace('-','').ToLowerInvariant()
}
function Read-NativeSessionStructure {
    [CmdletBinding()]
    param()
    Assert-NativeBuildFeature 'structure'
    $aliasRva=$script:NativeBuildProfile.addresses.hex_8e9980;$recordRva=$script:NativeBuildProfile.addresses.hex_127a17c;$recordBytes=123
    $expected=[long]$base+$recordRva
    $alias=[long]$reader.U32(([long]$base+$aliasRva))
    if($alias-ne $expected){throw 'Native Session structure alias does not point to the exact V2126 record.'}
    $first=[byte[]]$reader.Read($alias,$recordBytes)
    $second=[byte[]]$reader.Read($alias,$recordBytes)
    if($first.Length-ne $recordBytes-or $second.Length-ne $recordBytes){throw 'Native Session structure read did not return exactly123 bytes.'}
    if($reader.U32(([long]$base+$aliasRva))-ne $alias){throw 'Native Session structure alias changed during readback.'}
    $raw=ConvertTo-NativeStructureHex $first
    if((ConvertTo-NativeStructureHex $second)-cne $raw){throw 'Native Session structure changed during readback.'}
    $layout=@(
        [pscustomobject]@{name='stereo_mode_raw';offset=0;bytes=1;native_type='Byte';labels_verified=$false}
        [pscustomobject]@{name='input_count_slots';offset=0x02;bytes=16;native_type='8xWord LE';native_indices=@(1,2,3,4,5,6,7,8);labels_verified=$false}
        [pscustomobject]@{name='aux_count_slots';offset=0x12;bytes=16;native_type='8xWord LE';native_indices=@(1,2,3,4,5,6,7,8);labels_verified=$false}
        [pscustomobject]@{name='group_count_slots';offset=0x22;bytes=16;native_type='8xWord LE';native_indices=@(1,2,3,4,5,6,7,8);labels_verified=$false}
        [pscustomobject]@{name='timecode_source_raw';offset=0x3A;bytes=1;native_type='Byte';labels_verified=$false}
        [pscustomobject]@{name='frame_rate_index_raw';offset=0x3B;bytes=1;native_type='Byte';labels_verified=$false}
        [pscustomobject]@{name='sample_frequency';offset=0x53;bytes=4;native_type='Single LE'}
        [pscustomobject]@{name='video_frame_rate';offset=0x57;bytes=4;native_type='Single LE'}
        [pscustomobject]@{name='bussing_mode_raw';offset=0x63;bytes=1;native_type='Byte';labels_verified=$false}
        [pscustomobject]@{name='soft_channels';offset=0x6E;bytes=1;native_type='Byte'}
        [pscustomobject]@{name='matrix_outputs';offset=0x6F;bytes=1;native_type='Byte'}
        [pscustomobject]@{name='control_groups';offset=0x70;bytes=1;native_type='Byte'}
        [pscustomobject]@{name='multi_groups';offset=0x71;bytes=1;native_type='Byte'}
        [pscustomobject]@{name='matrix_inputs';offset=0x72;bytes=1;native_type='Byte'}
        [pscustomobject]@{name='graphic_eqs';offset=0x73;bytes=1;native_type='Byte'}
    )
    return [pscustomobject][ordered]@{
        schema_version=1;subsystem='native-session-structure-readonly';profile=$script:NativeBuildProfile.id
        build_profile=$script:NativeBuildProfile.id
        executable_sha256=$script:NativeBuildProfile.executable.sha256
        executable_hash_verified_by_caller=$true;alias_slot_rva=$aliasRva;record_rva=$recordRva
        record_pointer=$alias;record_bytes=$recordBytes;raw_hex=$raw
        alias_verified=$true;consistent_double_read=$true;values_atomic=$false
        memory_read_only=$true;ui_used=$false;network_used=$false;headless_structure_setter_established=$false
        offset_layout=$layout
        layout_provenance=[pscustomobject]@{
            kind='static Delphi extended RTTI plus native constructor reads'
            record_type='tConsoleRec';record_typeinfo_rva=$script:NativeBuildProfile.addresses.hex_8aae5c
            count_slot_constructor_va=@(('0x{0:X}' -f $script:NativeBuildProfile.addresses.hex_c6a1bf),('0x{0:X}' -f $script:NativeBuildProfile.addresses.hex_c6a290),('0x{0:X}' -f $script:NativeBuildProfile.addresses.hex_c6a362))
            count_slots='native indices1..8; human mode labels and DSP weighting unresolved'
            unknown_bytes='retained in raw_hex; no labels inferred for other offsets'
        }
    }
}

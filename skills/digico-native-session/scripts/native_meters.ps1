# Exact Quantum1 V2126 read-only cached-meter export. Dot-source after the runner
# verifies its PE hash and supplies $reader/$base. No process selection, sockets,
# UI, target function calls, memory writes, meter subscription or Cat9 injection.

function ConvertTo-NativeMeterHex([byte[]]$Bytes) {
    return ([BitConverter]::ToString($Bytes)).Replace('-','').ToLowerInvariant()
}
function Get-NativeMeterBytes($Context,[long]$Address,[int]$Length) {
    if($Address -le 0 -or $Address -gt 0xffffffffL -or $Length -lt 0 -or $Length -gt 1048576 -or ($Address+$Length) -gt 0x100000000L){throw 'Invalid meter read bounds.'}
    return ,([byte[]]$Context.Reader.Read($Address,$Length))
}
function Get-NativeMeterSlice([byte[]]$Bytes,[int]$Offset,[int]$Length) {
    $copy=New-Object byte[] $Length
    if($Length -gt 0){[Array]::Copy($Bytes,$Offset,$copy,0,$Length)}
    return ,$copy
}
function Get-NativeMeterScalar([byte[]]$Bytes,[int]$Offset) {
    $value=[BitConverter]::ToSingle($Bytes,$Offset)
    $finite=-not ([single]::IsNaN($value) -or [single]::IsInfinity($value))
    $number=$null;$code=$null
    if($finite){$number=$value;if($value -ge 0 -and $value -le 65535 -and [math]::Truncate([double]$value) -eq $value){$code=[int]$value}}
    return [pscustomobject][ordered]@{raw_hex=(ConvertTo-NativeMeterHex (Get-NativeMeterSlice $Bytes $Offset 4));float32=$number;finite=$finite;native_code=$code}
}
function Get-NativeMeterList($Context,[long]$Address,[int]$Maximum,[string]$Role) {
    $header=Get-NativeMeterBytes $Context $Address 16
    $items=[long][BitConverter]::ToUInt32($header,4);$count=[BitConverter]::ToInt32($header,8);$capacity=[BitConverter]::ToInt32($header,12)
    if($count -lt 0 -or $count -gt $Maximum -or $capacity -lt $count -or $capacity -gt 1048576 -or ($count -gt 0 -and $items -eq 0)){throw "Invalid $Role list bounds."}
    $raw=[byte[]]@();if($count -gt 0){$raw=Get-NativeMeterBytes $Context $items (4*$count)}
    $values=New-Object uint32[] $count
    for($i=0;$i -lt $count;$i++){$values[$i]=[BitConverter]::ToUInt32($raw,4*$i)}
    return [pscustomobject]@{address=$Address;items=$items;count=$count;capacity=$capacity;header_hex=(ConvertTo-NativeMeterHex $header);items_hex=(ConvertTo-NativeMeterHex $raw);values=$values}
}
function Assert-NativeMeterListStable($Context,$List) {
    if((ConvertTo-NativeMeterHex (Get-NativeMeterBytes $Context $List.address 16)) -cne $List.header_hex){throw 'Meter list structure changed during read.'}
    if($List.count -gt 0 -and (ConvertTo-NativeMeterHex (Get-NativeMeterBytes $Context $List.items (4*$List.count))) -cne $List.items_hex){throw 'Meter list membership changed during read.'}
}

function Read-NativeMeters {
    [CmdletBinding()]
    param([int]$ChannelId=-1,[int]$Limit=256)
    Assert-NativeBuildFeature 'meters'
    if($null -eq $reader -or [long]$base -le 0){throw 'Meters require the verified runner reader/base context.'}
    if($ChannelId -lt -1 -or $ChannelId -gt 65535 -or $Limit -lt 1 -or $Limit -gt 1000){throw 'Invalid raw channel ID or meter limit.'}
    $ctx=@{Reader=$reader;Base=[long]$base}
    $started=[DateTime]::UtcNow.ToString('o')
    $manager=[long]$reader.U32($ctx.Base+$script:NativeBuildProfile.addresses.hex_126e368)
    if($manager -eq 0 -or [long]$reader.U32($manager) -ne ($ctx.Base+$script:NativeBuildProfile.addresses.hex_7544a4)){throw 'Meter manager is not the exact tDMAList profile.'}
    if([long]$reader.U32($ctx.Base+$script:NativeBuildProfile.addresses.hex_7544a4-0x34) -ne $script:NativeBuildProfile.layouts.meter_manager.size){throw 'Unexpected tDMAList instance size.'}
    $registry=Get-NativeMeterList $ctx $manager 200000 'meter registry'
    $indexPointer=[long]$reader.U32($manager+$script:NativeBuildProfile.layouts.meter_manager.index)
    if($indexPointer -eq 0){throw 'MeterIndex is not initialized.'}
    $index=Get-NativeMeterList $ctx $indexPointer 8192 'stream meter index'
    # MeterIndex entries are INTEGER registry indices, not object pointers.
    # BuildMeterIndex(B5702C) stores the first of each nonzero AudioID group;
    # UpdateMeters(B57800) updates every adjacent alias sharing that AudioID.
    $byAudio=@{};$previous=-1
    for($i=0;$i -lt $index.count;$i++){
        $first=[long]$index.values[$i]
        if($first -lt 0 -or $first -ge $registry.count -or $first -le $previous){throw 'Invalid or unsorted stream-to-registry index.'}
        $previous=$first;$control=[long]$registry.values[$first]
        if($control -eq 0){throw 'Null meter registry entry.'}
        $audio=ConvertTo-NativeMeterHex (Get-NativeMeterBytes $ctx ($control+$script:NativeBuildProfile.layouts.controller.audio_id) 8)
        if($audio -ceq '0000000000000000' -or $byAudio.ContainsKey($audio)){throw 'Invalid or repeated stream AudioID anchor.'}
        $byAudio[$audio]=$i
    }
    $meters=[Collections.Generic.List[object]]::new();$witnesses=[Collections.Generic.List[object]]::new();$matching=0;$seen=@{}
    $strictAscii=[Text.Encoding]::GetEncoding(20127,[Text.EncoderExceptionFallback]::new(),[Text.DecoderExceptionFallback]::new())
    for($i=0;$i -lt $registry.count;$i++){
        $control=[long]$registry.values[$i]
        if($control -eq 0 -or $seen.ContainsKey([string]$control)){throw 'Null or duplicate meter registry pointer.'}
        $seen[[string]$control]=$true
        $identity=Get-NativeMeterBytes $ctx $control 13
        if([long][BitConverter]::ToUInt32($identity,0) -ne ($ctx.Base+$script:NativeBuildProfile.addresses.hex_8680fc)){throw 'Unsupported meter controller VMT.'}
        $rawChannel=[int][BitConverter]::ToUInt16($identity,7)
        if($ChannelId -ge 0 -and $rawChannel -ne $ChannelId){continue}
        $matching++
        if($meters.Count -ge $Limit){continue}
        $raw=Get-NativeMeterBytes $ctx $control ($script:NativeBuildProfile.layouts.controller.restore+4)
        if((ConvertTo-NativeMeterHex (Get-NativeMeterSlice $raw 0 13)) -cne (ConvertTo-NativeMeterHex $identity)){throw 'Meter identity changed before sample.'}
        $definitionPointer=[long][BitConverter]::ToUInt32($raw,$script:NativeBuildProfile.layouts.controller.definition)
        $definition=Get-NativeMeterBytes $ctx $definitionPointer $script:NativeBuildProfile.layouts.definition.size
        if($definition[$script:NativeBuildProfile.layouts.definition.data_type] -ne 3 -or $definition[$script:NativeBuildProfile.layouts.definition.parameter] -ne $raw[4] -or (($definition[$script:NativeBuildProfile.layouts.definition.band] -ne 0 -or $raw[5] -eq 5) -and $definition[$script:NativeBuildProfile.layouts.definition.band] -ne $raw[6])){throw 'Meter controller/definition identity mismatch.'}
        $nameLength=[int]$definition[7];if($nameLength -gt 31){throw 'Invalid meter definition name length.'}
        $name=$strictAscii.GetString((Get-NativeMeterSlice $definition 8 $nameLength))
        $audio=ConvertTo-NativeMeterHex (Get-NativeMeterSlice $raw $script:NativeBuildProfile.layouts.controller.audio_id 8)
        $streamIndex=$null;if($byAudio.ContainsKey($audio)){$streamIndex=[int]$byAudio[$audio]}
        $type=Get-NativeMeterScalar $definition 1
        $meters.Add([pscustomobject][ordered]@{
            registry_index=$i;stream_index=$streamIndex;controller_pointer=$control;raw_id_hex=(ConvertTo-NativeMeterHex (Get-NativeMeterSlice $raw 4 5));channel_id=$rawChannel
            parameter=[int]$raw[4];module=[int]$raw[5];band=[int]$raw[6];channel=[int]$raw[7];channel_type=[int]$raw[8]
            definition_pointer=$definitionPointer;definition_raw_hex=(ConvertTo-NativeMeterHex $definition);name=$name;raw_type=3;meter_type=$type
            audio_id_raw_hex=$audio;output=(Get-NativeMeterScalar $raw $script:NativeBuildProfile.layouts.controller.output);secondary=(Get-NativeMeterScalar $raw $script:NativeBuildProfile.layouts.controller.restore);ordinary_current=(Get-NativeMeterScalar $raw $script:NativeBuildProfile.layouts.controller.value)
            flags_u16=[int][BitConverter]::ToUInt16($raw,$script:NativeBuildProfile.layouts.controller.flags);unit='native-meter-code';db_value=$null;freshness='not_observable';physical_audio_verified=$false
        })
        $witnesses.Add([pscustomobject]@{pointer=$control;identity_hex=(ConvertTo-NativeMeterHex (Get-NativeMeterSlice $raw 0 13));definition_pointer=$definitionPointer;definition_hex=(ConvertTo-NativeMeterHex $definition)})
    }
    $configuration=Get-NativeMeterBytes $ctx ($manager+$script:NativeBuildProfile.layouts.meter_manager.configuration) 13
    $standard=[int]$reader.I32($ctx.Base+$script:NativeBuildProfile.addresses.hex_a2443c)
    $reference=[int]$reader.I32($ctx.Base+$script:NativeBuildProfile.addresses.hex_a24440)
    $minimum=[int]$reader.I32($manager+$script:NativeBuildProfile.layouts.meter_manager.minimum)
    $cycle=([int]$reader.U8($manager+$script:NativeBuildProfile.layouts.meter_manager.synthetic_cycle) -ne 0)
    if([long]$reader.U32($ctx.Base+$script:NativeBuildProfile.addresses.hex_126e368) -ne $manager -or [long]$reader.U32($manager+$script:NativeBuildProfile.layouts.meter_manager.index) -ne $indexPointer){throw 'Meter manager changed during read.'}
    Assert-NativeMeterListStable $ctx $registry;Assert-NativeMeterListStable $ctx $index
    foreach($w in $witnesses){
        if((ConvertTo-NativeMeterHex (Get-NativeMeterBytes $ctx $w.pointer 13)) -cne $w.identity_hex -or (ConvertTo-NativeMeterHex (Get-NativeMeterBytes $ctx $w.definition_pointer $script:NativeBuildProfile.layouts.definition.size)) -cne $w.definition_hex){throw 'Meter identity changed during read.'}
    }
    return [pscustomobject][ordered]@{
        schema_version=1;build_profile=$script:NativeBuildProfile.id;profile=$script:NativeBuildProfile.id;executable_sha256=$script:NativeBuildProfile.executable.sha256;memory_read_only=$true;network_used=$false;ui_used=$false;physical_audio_verified=$false
        sampled_start_utc=$started;sampled_end_utc=[DateTime]::UtcNow.ToString('o');values_atomic=$false;structure_verified=$true;freshness='not_observable'
        manager=[pscustomobject][ordered]@{pointer=$manager;vmt_rva=$script:NativeBuildProfile.addresses.hex_7544a4;registered_count=$registry.count;stream_count=$index.count;active=($configuration[0] -ne 0);overflowed=($configuration[1] -ne 0);need_starting=($configuration[2] -ne 0);suspend_restart=($configuration[3] -ne 0);stop_acknowledged=($configuration[4] -ne 0);minimum_native_code=$minimum;synthetic_cycle_active=$cycle;meter_standard=$standard;reference_level=$reference}
        selection=[pscustomobject]@{channel_id=$ChannelId;limit=$Limit};matching_count=$matching;returned_count=$meters.Count;truncated=($matching -gt $meters.Count);meters=$meters.ToArray()
        interpretation='Cached native meter codes. Output+2C and secondary+50 are updated by tDMAList; current+18 is a separate scalar. No hardware signal or update age was observed.'
    }
}

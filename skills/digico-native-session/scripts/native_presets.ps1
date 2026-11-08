# Exact Quantum1 V2126 preset RPM/readback helpers. Dot-source functions only.
# Requires the runner's hash-gated read-only $reader/$base. No memory writes/UI.

function ConvertTo-DiGiCoPresetHex([byte[]]$Bytes) {
    return ([BitConverter]::ToString($Bytes)).Replace('-','').ToLowerInvariant()
}
function Get-DiGiCoPresetSHA([byte[]]$Bytes) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try {return ConvertTo-DiGiCoPresetHex $sha.ComputeHash($Bytes)} finally {$sha.Dispose()}
}
function Has-DiGiCoPresetProperty($Value,[string]$Name) {
    if ($Value -is [Collections.IDictionary]) {return $Value.Contains($Name)}
    return $null-ne $Value.PSObject.Properties[$Name]
}
function Read-DiGiCoPresetText([byte[]]$Raw,[int]$Offset,[int]$Maximum,[string]$Encoding) {
    $length=[int]$Raw[$Offset]
    if ($length-gt $Maximum-or $Offset+1+$length-gt $Raw.Length) {throw 'Invalid native preset ShortString extent.'}
    $bytes=New-Object byte[] $length
    if ($length-gt 0) {[Array]::Copy($Raw,$Offset+1,$bytes,0,$length)}
    $code=switch($Encoding){'ascii'{20127};'cp950'{950};'utf-8'{65001};default{throw 'Unsupported preset text encoding.'}}
    $decoder=[Text.Encoding]::GetEncoding($code,[Text.EncoderExceptionFallback]::new(),[Text.DecoderExceptionFallback]::new())
    $text=$null;try{$text=$decoder.GetString($bytes)}catch{}
    return [pscustomobject]@{hex=(ConvertTo-DiGiCoPresetHex $bytes);text=$text}
}
function Read-DiGiCoPresetListBounds([long]$Address,[int]$Maximum) {
    $items=[long]$reader.U32($Address+4);$count=$reader.I32($Address+8);$capacity=$reader.I32($Address+12)
    if ($count-lt 0-or $count-gt $Maximum-or $capacity-lt $count-or ($count-gt 0-and $items-eq 0)) {throw 'Invalid native preset child-list bounds.'}
    return [pscustomobject]@{pointer=$Address;items=$items;count=$count;capacity=$capacity}
}
function Assert-DiGiCoPresetListUnchanged($Bounds) {
    if ($reader.U32($Bounds.pointer+4)-ne $Bounds.items-or $reader.I32($Bounds.pointer+8)-ne $Bounds.count) {throw 'Native preset child list changed during readback.'}
}
function Assert-DiGiCoPresetClassAncestor([long]$Address,[long]$ExpectedVMT) {
    $vmt=[long]$reader.U32($Address);$seen=[Collections.Generic.HashSet[long]]::new()
    for($depth=0;$depth-lt 32-and $vmt-ne 0;$depth++) {
        if($vmt-lt ($base+0x58)-or $vmt-ge ($base+0x4440000)-or -not $seen.Add($vmt)){throw 'Invalid/cyclic native Preset dependency VMT ancestry.'}
        if($vmt-eq $ExpectedVMT){return}
        $slot=[long]$reader.U32($vmt-0x30)
        if($slot-eq 0){break}
        if($slot-lt $base-or $slot-gt ($base+0x4440000-4)){throw 'Invalid native Preset dependency parent VMT slot.'}
        $vmt=[long]$reader.U32($slot)
    }
    throw 'Native Preset dependency does not derive from the expected class.'
}

function Read-DiGiCoPresetContent([long]$Preset,[string]$TextEncoding='ascii') {
    $bounds=Read-DiGiCoPresetListBounds $Preset 1024
    $channels=[Collections.Generic.List[object]]::new()
    $canonical=[IO.MemoryStream]::new();$writer=[IO.BinaryWriter]::new($canonical)
    $totalControls=0
    try {
        $writer.Write([int32]$bounds.count)
        for($c=0;$c-lt $bounds.count;$c++) {
            $channel=[long]$reader.U32($bounds.items+4*$c);$data=$reader.Read($channel,0x58)
            if([BitConverter]::ToUInt32($data,0)-ne ($base+0x7F90A0)){throw 'Unexpected tChannelSnapshot inside preset.'}
            $channelId=[int][BitConverter]::ToUInt16($data,0x24)
            $title=Read-DiGiCoPresetText $data 0x26 31 $TextEncoding
            $stereoMode=[int]$data[0x4A]
            $modulesBounds=Read-DiGiCoPresetListBounds $channel 256
            $modules=[Collections.Generic.List[object]]::new()
            $writer.Write([uint16]$channelId);$writer.Write([byte]$stereoMode)
            $titleBytes=New-Object byte[] ($title.hex.Length/2)
            for($i=0;$i-lt $titleBytes.Length;$i++){$titleBytes[$i]=[Convert]::ToByte($title.hex.Substring($i*2,2),16)}
            $writer.Write([byte]$titleBytes.Length);$writer.Write($titleBytes);$writer.Write([int32]$modulesBounds.count)
            for($m=0;$m-lt $modulesBounds.count;$m++) {
                $module=[long]$reader.U32($modulesBounds.items+4*$m);$mod=$reader.Read($module,0x5C)
                if([BitConverter]::ToUInt32($mod,0)-ne ($base+0x7F7938)){throw 'Unexpected tModuleSnapshot inside preset.'}
                $n=[BitConverter]::ToInt32($mod,0x30);$valuesPointer=[long][BitConverter]::ToUInt32($mod,0x3E)
                if($n-lt 0-or $n-gt 4096-or ($n-gt 0-and $valuesPointer-eq 0)){throw 'Invalid preset controller-array bounds.'}
                $totalControls+=$n;if($totalControls-gt 250000){throw 'Preset exceeds local controller readback bound.'}
                $values=[byte[]]@();if($n-gt 0){$values=$reader.Read($valuesPointer,8*$n)}
                $typeId=[BitConverter]::ToUInt32($mod,4)
                $crossfade=New-Object byte[] 4;[Array]::Copy($mod,0x35,$crossfade,0,4)
                $writer.Write([uint32]$typeId);$writer.Write([byte]$mod[0x34]);$writer.Write($crossfade);$writer.Write([byte]$mod[0x39])
                $writer.Write([int32]$n);$writer.Write($values)
                $controls=[Collections.Generic.List[object]]::new()
                for($i=0;$i-lt $n;$i++) {
                    $at=8*$i
                    $id=[byte[]]($values[$at],$values[$at+1],$values[$at+2],[byte]($channelId-band 255),[byte](($channelId-shr 8)-band 255))
                    $value=New-Object byte[] 4;[Array]::Copy($values,$at+4,$value,0,4)
                    $controls.Add([pscustomobject]@{control_id_hex=(ConvertTo-DiGiCoPresetHex $id);flags=[int]$values[$at+3];value_raw_hex=(ConvertTo-DiGiCoPresetHex $value)})
                }
                if($reader.I32($module+0x30)-ne $n-or $reader.U32($module+0x3E)-ne $valuesPointer){throw 'Preset controller array changed during readback.'}
                $modules.Add([pscustomobject]@{index=$m;pointer=$module;type_id_raw=$typeId;scope_flags=[int]$mod[0x34];crossfade_raw_hex=(ConvertTo-DiGiCoPresetHex $crossfade);ignore_scope=[int]$mod[0x39];controller_count=$n;controllers=@($controls.ToArray())})
            }
            Assert-DiGiCoPresetListUnchanged $modulesBounds
            $channels.Add([pscustomobject]@{index=$c;pointer=$channel;channel_id=$channelId;channel_title=$title.text;channel_title_hex=$title.hex;stereo_mode_raw=$stereoMode;modules=@($modules.ToArray())})
        }
        Assert-DiGiCoPresetListUnchanged $bounds
        $writer.Flush()
        return [pscustomobject]@{channel_snapshot_count=$bounds.count;controller_count=$totalControls;controller_content_sha256=(Get-DiGiCoPresetSHA $canonical.ToArray());channels=@($channels.ToArray());fingerprint_scope='channel IDs/titles/stereo, module type/scope/crossfade/ignore, controller records; excludes pointers/timestamps/refcounts'}
    } finally {$writer.Dispose();$canonical.Dispose()}
}

function Get-DiGiCoPresetMetadataSignature($Preset,[string[]]$Except=@()) {
    $parts=[Collections.Generic.List[string]]::new()
    foreach($key in @('name_hex','group_name_hex','notes_hex','time_stored_hex','lock_flag','fx_resource','channel_count','mix_preset')) {
        if($Except-notcontains $key){$parts.Add([string]$Preset.$key)}
    }
    return ($parts.ToArray()-join ':')
}
function Assert-DiGiCoPresetStepContract($Step) {
    $types=@{
        store_input_preset=@(3,0xC9);rename_preset=@(3,0xCE);lock_preset=@(1,0xD2)
        update_input_preset=@(1,0xCC);recall_input_preset=@(1,0xC8)
        set_preset_recall_scope=@(2,0xD3);restore_preset_recall_scope=@(2,0xD3)
        delete_preset=@(1,0xD1)
    }
    if(-not $types.ContainsKey([string]$Step.op)){throw 'Unknown native preset operation.'}
    $pair=$types[[string]$Step.op]
    $id=('{0:x2}0000b601'-f ([int]$pair[1]))
    if($Step.category-ne $pair[0]-or $Step.control_id_hex-ne $id){throw 'Preset category/ControlID differs from the exact native callback contract.'}
    $index=$Step.list_index
    if($index-is [bool]-or $null-eq $index-or $index-isnot [ValueType]-or [decimal]$index-ne [math]::Truncate([decimal]$index)-or [decimal]$index-lt 0-or [decimal]$index-gt 131071){throw 'Invalid native preset ListIndex.'}
    $body=[string]$Step.body_hex
    if($body-notmatch '^(?:[0-9a-fA-F]{2})+$'){throw 'Invalid preset body bytes.'}
    switch([string]$Step.op) {
        'store_input_preset' {$text=[string]$Step.group_name_hex}
        'rename_preset' {$text=[string]$Step.name_hex}
        'lock_preset' {if($Step.expected_lock_flag-notin @(0,1)-or $body-ne (ConvertTo-DiGiCoPresetHex ([BitConverter]::GetBytes([int32]$Step.expected_lock_flag)))){throw 'Invalid preset lock body.'};return}
        'update_input_preset' {if($body-notmatch '^[0-9a-fA-F]{4}0000$'){throw 'Preset update must select one channel with zero highword.'};return}
        'recall_input_preset' {if($body-ne (ConvertTo-DiGiCoPresetHex ([BitConverter]::GetBytes([int32]$Step.target_channel_id)))){throw 'Preset recall must use its explicit target channel and zero high flags.'};return}
        'delete_preset' {if($body-ne '00000000'){throw 'Invalid normalized preset delete body.'};return}
        default {if($Step.expected_scope_hex-notmatch '^[0-9a-fA-F]{64}$'-or $body-ne ('20000000'+$Step.expected_scope_hex)){throw 'Preset scope requires exact32-byte native payload.'};return}
    }
    if($text-notmatch '^(?:[0-9a-fA-F]{2}){0,31}$'){throw 'Preset text exceeds the31-byte native field.'}
    $wanted=(ConvertTo-DiGiCoPresetHex ([BitConverter]::GetBytes([uint32]($text.Length/2))))+$text
    if($body-ne $wanted){throw 'Preset string length/body disagrees with its exact raw text bytes.'}
}
function Read-DiGiCoNativePresets {
    [CmdletBinding()]
    param([int[]]$ContentIndices=@(),[ValidateSet('ascii','cp950','utf-8')][string]$TextEncoding='ascii')
    $list=[long]$reader.U32($base+0x126E3E8)
    if($list-eq 0-or $reader.U32($list)-ne ($base+0x7735B8)){throw 'Unexpected/uninitialized native presets list.'}
    $bounds=Read-DiGiCoPresetListBounds $list 10000
    foreach($index in $ContentIndices){if($index-lt 0-or $index-ge $bounds.count){throw 'Preset content index outside current list.'}}
    $presets=[Collections.Generic.List[object]]::new();$fingerprints=[Collections.Generic.List[string]]::new()
    for($i=0;$i-lt $bounds.count;$i++) {
        $address=[long]$reader.U32($bounds.items+4*$i);$raw=$reader.Read($address,0x178)
        if([BitConverter]::ToUInt32($raw,0)-ne ($base+0x77315C)){throw 'Unexpected native tPreset type.'}
        $name=Read-DiGiCoPresetText $raw 0x24 31 $TextEncoding
        $group=Read-DiGiCoPresetText $raw 0x44 31 $TextEncoding
        $notes=Read-DiGiCoPresetText $raw 0x64 255 $TextEncoding
        $stored=New-Object byte[] 8;[Array]::Copy($raw,0x164,$stored,0,8)
        $entry=[pscustomobject][ordered]@{index=$i;pointer=$address;name=$name.text;name_hex=$name.hex;group_name=$group.text;group_name_hex=$group.hex;notes=$notes.text;notes_hex=$notes.hex;time_stored_hex=(ConvertTo-DiGiCoPresetHex $stored);lock_flag=[int]$raw[0x16C];fx_resource=[int][BitConverter]::ToUInt16($raw,0x16D);channel_count=[int][BitConverter]::ToUInt16($raw,0x16F);mix_preset=[int]$raw[0x171];content=$null}
        if($ContentIndices-contains $i){$entry.content=Read-DiGiCoPresetContent $address $TextEncoding}
        $presets.Add($entry);$fingerprints.Add((Get-DiGiCoPresetMetadataSignature $entry))
    }
    Assert-DiGiCoPresetListUnchanged $bounds
    if($reader.U32($base+0x126E3E8)-ne $list){throw 'Native preset-list owner changed during readback.'}
    $scope=$reader.Read($base+0x126E3EC,32)
    $pathRaw=$reader.Read($base+0x127B4FC,256);$sessionPath=Read-DiGiCoPresetText $pathRaw 0 255 $TextEncoding
    $metadata=[Text.Encoding]::UTF8.GetBytes(($fingerprints.ToArray()-join '|'))
    return [pscustomobject][ordered]@{schema_version=1;subsystem='native-presets-readonly';preset_count=$bounds.count;list_pointer=$list;scope_hex=(ConvertTo-DiGiCoPresetHex $scope);presets=@($presets.ToArray());metadata_sha256=(Get-DiGiCoPresetSHA $metadata);current_session_path=$sessionPath.text;current_session_path_hex=$sessionPath.hex;library_file_path=$null;library_file_path_established=$false;text_encoding=$TextEncoding;memory_read_only=$true}
}

function Read-DiGiCoPresetInputChannel([int]$ChannelId) {
    if($ChannelId-lt 0x100-or $ChannelId-gt 0x1FF){throw 'This preset helper supports ordinary input channels only.'}
    $console=[long]$reader.U32($base+0x1279CFC)
    if($console-eq 0-or $reader.U32($console)-ne ($base+0x864808)){throw 'Unexpected native console object.'}
    $groups=Read-DiGiCoPresetListBounds $console 256
    for($g=0;$g-lt $groups.count;$g++) {
        $group=[long]$reader.U32($groups.items+4*$g)
        Assert-DiGiCoPresetClassAncestor $group ($base+0x86600C)
        if($reader.U8($group+0x30)-ne 1){continue}
        $channels=Read-DiGiCoPresetListBounds $group 4096
        for($i=0;$i-lt $channels.count;$i++) {
            $channel=[long]$reader.U32($channels.items+4*$i)
            if($reader.U32($channel)-ne ($base+0x86660C)){throw 'Unexpected native channel type.'}
            if($reader.U16($channel+0x30)-eq $ChannelId) {
                $raw=$reader.Read($channel+0x34,32);$name=Read-DiGiCoPresetText $raw 0 31 'ascii'
                Assert-DiGiCoPresetListUnchanged $channels
                Assert-DiGiCoPresetListUnchanged $groups
                if($reader.U32($base+0x1279CFC)-ne $console){throw 'Native console changed during readback.'}
                return [pscustomobject]@{pointer=$channel;channel_id=$ChannelId;name_hex=$name.hex;current_preset_pointer=[long]$reader.U32($channel+0x56)}
            }
        }
    }
    throw 'Input channel ID was not found in the running console.'
}

function Get-DiGiCoPresetCueReferenceCount([long]$PresetPointer) {
    # Read-only dependency check: deletion's native callback clears these refs.
    $list=[long]$reader.U32($base+0x127305C)
    if($list-eq 0-or $reader.U32($list)-ne ($base+0x7F9BB0)){throw 'Cannot verify preset Cue dependencies.'}
    $bounds=Read-DiGiCoPresetListBounds $list 2000;$count=0
    for($i=0;$i-lt $bounds.count;$i++) {
        $snapshot=[long]$reader.U32($bounds.items+4*$i)
        if($reader.U32($snapshot)-ne ($base+0x7F8188)){throw 'Unexpected Snapshot type during preset dependency read.'}
        $channels=Read-DiGiCoPresetListBounds $snapshot 4096
        for($c=0;$c-lt $channels.count;$c++) {
            $channel=[long]$reader.U32($channels.items+4*$c)
            if($reader.U32($channel)-ne ($base+0x7F90A0)){throw 'Unexpected channel Snapshot during preset dependency read.'}
            if($reader.U32($channel+0x46)-eq $PresetPointer){$count++}
        }
        Assert-DiGiCoPresetListUnchanged $channels
    }
    Assert-DiGiCoPresetListUnchanged $bounds
    if($reader.U32($base+0x127305C)-ne $list){throw 'Native Snapshot-list owner changed during readback.'}
    return $count
}

function Assert-DiGiCoPresetEdit {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)]$Before,[Parameter(Mandatory=$true)]$Step,[scriptblock]$ProcessingCheck=$null)
    Assert-DiGiCoPresetStepContract $Step
    if(Has-DiGiCoPresetProperty $Step 'expected_controller_values') {
        $values=@($Step.expected_controller_values);$ids=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        if($values.Count-eq 0){throw 'Stored processing verification requires a nonempty explicit controller subset.'}
        foreach($value in $values){if([string]$value.control_id_hex-notmatch '^[0-9a-fA-F]{10}$'-or [string]$value.value_raw_hex-notmatch '^[0-9a-fA-F]{8}$'-or -not $ids.Add([string]$value.control_id_hex)){throw 'Invalid or duplicated stored-controller verification bytes.'}}
    }
    $fresh=Read-DiGiCoNativePresets -TextEncoding $Before.text_encoding
    if($fresh.list_pointer-ne $Before.list_pointer-or $fresh.preset_count-ne $Before.preset_count-or $fresh.metadata_sha256-ne $Before.metadata_sha256-or $fresh.scope_hex-ne $Before.scope_hex){throw 'Preset inventory/scope changed since the supplied preflight readback.'}
    $op=[string]$Step.op;$index=[int]$Step.list_index
    $expected=[pscustomobject]@{op=$op;index=$index;channel_id=0;name_hex=$null;group_name_hex=$null;lock_flag=0;count_after=[int]$Before.preset_count}
    if($op-in @('set_preset_recall_scope','restore_preset_recall_scope')) {
        if([string]$Step.expected_scope_hex-notmatch '^[0-9a-fA-F]{64}$'){throw 'Preset scope requires an exact32-byte expected value.'}
        return $expected
    }
    if($op-eq 'store_input_preset') {
        if($Before.preset_count-ge 10000){throw 'Store would exceed the local preset readback bound.'}
        $channelId=$index-band 65535;$channel=Read-DiGiCoPresetInputChannel $channelId
        if(($index-shr 16)-notin @(0,1)){throw 'Only ordinary input presets and optional ForceMono are supported.'}
        if([string]$Step.group_name_hex-notmatch '^(?:[0-9a-fA-F]{2}){0,31}$'){throw 'Invalid preset group raw bytes.'}
        $expected.index=[int]$Before.preset_count;$expected.count_after++
        $expected.channel_id=$channelId;$expected.name_hex=$channel.name_hex;$expected.group_name_hex=[string]$Step.group_name_hex
        return $expected
    }
    if($index-lt 0-or $index-ge $Before.preset_count){throw 'Preset index is outside the fresh inventory.'}
    $entry=$Before.presets[$index]
    if($op-ne 'recall_input_preset'-and $entry.lock_flag-eq 2){throw 'Refusing to edit an original factory preset (LockFlag2).'}
    if($op-in @('update_input_preset','recall_input_preset')) {
        $stored=Read-DiGiCoPresetContent $entry.pointer $Before.text_encoding
        if($entry.mix_preset-ne 0-or $entry.channel_count-ne 1-or $stored.channel_snapshot_count-ne 1-or $stored.channels[0].channel_id-lt 0x100-or $stored.channels[0].channel_id-gt 0x1FF){throw 'This helper supports single ordinary-input processing presets only.'}
    }
    switch($op) {
        'rename_preset' {if([string]$Step.name_hex-notmatch '^(?:[0-9a-fA-F]{2}){0,31}$'){throw 'Invalid preset name raw bytes.'};$expected.name_hex=[string]$Step.name_hex}
        'lock_preset' {if($Step.expected_lock_flag-notin @(0,1)){throw 'Preset lock value must be0 or1.'};$expected.lock_flag=[int]$Step.expected_lock_flag}
        'update_input_preset' {
            if($entry.lock_flag-ne 0){throw 'Native preset update rejects a locked preset.'}
            $payload=[Convert]::ToInt32(([string]$Step.body_hex).Substring(0,2),16)+256*[Convert]::ToInt32(([string]$Step.body_hex).Substring(2,2),16)
            $null=Read-DiGiCoPresetInputChannel $payload;$expected.channel_id=$payload
        }
        'recall_input_preset' {
            if($null-eq $ProcessingCheck){throw 'Preset recall requires a processing-value verification callback, not only metadata/pointer verification.'}
            $expected.channel_id=[int]$Step.target_channel_id;$null=Read-DiGiCoPresetInputChannel $expected.channel_id
        }
        'delete_preset' {
            if($entry.lock_flag-ne 0){throw 'Refusing to delete a locked preset.'}
            if(-not (Has-DiGiCoPresetProperty $Step 'expected_group_name_hex')-or [string]$Step.expected_group_name_hex-notmatch '^(?:[0-9a-fA-F]{2}){1,31}$'-or $Step.expected_group_name_hex-ne $entry.group_name_hex){throw 'Preset delete requires the expected nonempty lab group name bytes.'}
            if((Get-DiGiCoPresetCueReferenceCount $entry.pointer)-ne 0){throw 'Preset is referenced by Cues; this non-Cue delete was not sent.'}
            $expected.count_after--
        }
        default {throw 'Unknown native preset operation.'}
    }
    return $expected
}

function Test-DiGiCoPresetControllerSubset($Content,$ExpectedValues) {
    foreach($wanted in $ExpectedValues) {
        $found=@($Content.channels|ForEach-Object {$_.modules}|ForEach-Object {$_.controllers}|Where-Object {$_.control_id_hex-eq $wanted.control_id_hex})
        if($found.Count-ne 1-or $found[0].value_raw_hex-ne $wanted.value_raw_hex){return $false}
    }
    return $true
}
function Test-DiGiCoPresetEdit($Before,$Now,$Step,$Expected) {
    if($Now.preset_count-ne $Expected.count_after){return $false}
    if($Expected.op-in @('set_preset_recall_scope','restore_preset_recall_scope')) {
        return $Now.metadata_sha256-eq $Before.metadata_sha256-and $Now.scope_hex-eq $Step.expected_scope_hex
    }
    if($Now.scope_hex-ne $Before.scope_hex){return $false}
    if($Expected.op-eq 'store_input_preset') {
        for($i=0;$i-lt $Before.preset_count;$i++){if((Get-DiGiCoPresetMetadataSignature $Before.presets[$i])-ne (Get-DiGiCoPresetMetadataSignature $Now.presets[$i])){return $false}}
        $entry=$Now.presets[$Expected.index]
        if($entry.name_hex-ne $Expected.name_hex-or $entry.group_name_hex-ne $Expected.group_name_hex-or $entry.lock_flag-ne 0-or $entry.mix_preset-ne 0-or $null-eq $entry.content-or $entry.content.channel_snapshot_count-ne 1-or $entry.content.channels[0].channel_id-ne $Expected.channel_id-or $entry.content.controller_count-le 0){return $false}
    } elseif($Expected.op-eq 'delete_preset') {
        for($i=0;$i-lt $Now.preset_count;$i++){$old=$i;if($i-ge $Expected.index){$old++};if((Get-DiGiCoPresetMetadataSignature $Before.presets[$old])-ne (Get-DiGiCoPresetMetadataSignature $Now.presets[$i])){return $false}}
        return $true
    } else {
        for($i=0;$i-lt $Now.preset_count;$i++){if($i-ne $Expected.index-and (Get-DiGiCoPresetMetadataSignature $Before.presets[$i])-ne (Get-DiGiCoPresetMetadataSignature $Now.presets[$i])){return $false}}
        $old=$Before.presets[$Expected.index];$entry=$Now.presets[$Expected.index]
        switch($Expected.op) {
            'rename_preset' {if($entry.name_hex-ne $Expected.name_hex-or (Get-DiGiCoPresetMetadataSignature $old @('name_hex'))-ne (Get-DiGiCoPresetMetadataSignature $entry @('name_hex'))){return $false}}
            'lock_preset' {if($entry.lock_flag-ne $Expected.lock_flag-or (Get-DiGiCoPresetMetadataSignature $old @('lock_flag'))-ne (Get-DiGiCoPresetMetadataSignature $entry @('lock_flag'))){return $false}}
            'update_input_preset' {if($null-eq $entry.content-or $entry.content.channel_snapshot_count-ne 1-or $entry.content.channels[0].channel_id-ne $Expected.channel_id-or $entry.content.controller_count-le 0-or $entry.time_stored_hex-eq $old.time_stored_hex-or (Get-DiGiCoPresetMetadataSignature $old @('time_stored_hex','channel_count'))-ne (Get-DiGiCoPresetMetadataSignature $entry @('time_stored_hex','channel_count'))){return $false}}
            'recall_input_preset' {if($Now.metadata_sha256-ne $Before.metadata_sha256){return $false}}
        }
    }
    if(Has-DiGiCoPresetProperty $Step 'expected_controller_values') {
        if($null-eq $entry.content-or -not (Test-DiGiCoPresetControllerSubset $entry.content $Step.expected_controller_values)){return $false}
    }
    return $true
}
function Wait-DiGiCoPresetEdit {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)]$Before,[Parameter(Mandatory=$true)]$Step,[Parameter(Mandatory=$true)]$Expected,
          [scriptblock]$ProcessingCheck=$null,[ValidateRange(100,30000)][int]$TimeoutMs=10000)
    if($Expected.op-eq 'recall_input_preset'-and $null-eq $ProcessingCheck){throw 'Missing processing-value verification callback.'}
    $timer=[Diagnostics.Stopwatch]::StartNew()
    while($timer.ElapsedMilliseconds-lt $TimeoutMs) {
        if($native.Client){Pump 75}else{[Threading.Thread]::Sleep(75)}
        try {
            $now=Read-DiGiCoNativePresets -TextEncoding $Before.text_encoding
            if($Expected.op-in @('store_input_preset','update_input_preset','recall_input_preset')-and $Expected.index-lt $now.preset_count) {
                $now=Read-DiGiCoNativePresets -TextEncoding $Before.text_encoding -ContentIndices @($Expected.index)
            }
            if(Test-DiGiCoPresetEdit $Before $now $Step $Expected) {
                if($ProcessingCheck-and -not (& $ProcessingCheck $Before $now $Step $Expected)){continue}
                return [pscustomobject]@{state=$now;metadata_verified=$true;stored_controller_subset_verified=(Has-DiGiCoPresetProperty $Step 'expected_controller_values');processing_verified=($null-ne $ProcessingCheck)}
            }
        } catch {
            if($_.Exception.Message-notlike '*changed during readback*'){throw}
        }
    }
    throw 'Native preset edit did not match readback; inspect state and do not replay blindly.'
}

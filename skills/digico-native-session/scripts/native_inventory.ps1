# Quantum1 V2126 read-only object inventory. Dot-source from the hash-checked runner.
# No process selection, socket traffic, UI calls, or memory writes occur here.

function ConvertTo-NativeInventoryHex([byte[]]$Bytes,[int]$Offset=0,[int]$Length=-1) {
    if($Length-lt 0){$Length=$Bytes.Length-$Offset}
    return ([BitConverter]::ToString($Bytes,$Offset,$Length)).Replace('-','').ToLowerInvariant()
}

function Get-NativeInventoryBytes($Context,[long]$Address,[int]$Length) {
    if($Address -le 0 -or $Address -gt 0xffffffffL -or $Length -lt 0 -or $Length -gt 1048576 -or ($Address+$Length) -gt 0x100000000L){throw 'Invalid native inventory read bounds.'}
    if($Length -eq 0){return ,([byte[]]@())}
    try{return ,([byte[]]$Context.Reader.Read($Address,$Length))}catch{throw ("Inventory RPM failed at 0x{0:x}, length {1}; object=0x{2:x}, id={3}: {4}" -f $Address,$Length,$Context.CurrentObject,$Context.CurrentId,$_.Exception.Message)}
}

function Get-NativeInventorySlice([byte[]]$Bytes,[int]$Offset,[int]$Length) {
    $copy=New-Object byte[] $Length
    if($Length -gt 0){[Array]::Copy($Bytes,$Offset,$copy,0,$Length)}
    return ,$copy
}

function Get-NativeInventoryShort($Context,[byte[]]$Bytes,[int]$Offset,[int]$Maximum,[bool]$UseAscii=$false) {
    $n=[int]$Bytes[$Offset]
    if($n -gt $Maximum -or ($Offset+1+$n) -gt $Bytes.Length){throw 'Invalid inventory ShortString length.'}
    $raw=Get-NativeInventorySlice $Bytes ($Offset+1) $n
    $text=$null;$valid=$true
    try{if($UseAscii){$text=$Context.Ascii.GetString($raw)}else{$text=$Context.Encoding.GetString($raw)}}catch{$valid=$false}
    return [pscustomobject][ordered]@{text=$text;raw_hex=(ConvertTo-NativeInventoryHex $raw);decoded=$valid}
}

function Get-NativeInventoryValue([byte[]]$Bytes,[int]$Offset) {
    $value=[BitConverter]::ToSingle($Bytes,$Offset);$finite=-not ([single]::IsNaN($value) -or [single]::IsInfinity($value))
    $number=$null;if($finite){$number=$value}
    return [pscustomobject][ordered]@{raw_hex=(ConvertTo-NativeInventoryHex $Bytes $Offset 4);int32=[BitConverter]::ToInt32($Bytes,$Offset);uint32=[BitConverter]::ToUInt32($Bytes,$Offset);float32=$number;float32_finite=$finite}
}

function Get-NativeInventoryType($Context,[long]$Address) {
    $vmt=[long]$Context.Reader.U32($Address)
    $key=[string]$vmt
    if($Context.Types.ContainsKey($key)){return $Context.Types[$key]}
    $ancestors=[Collections.Generic.List[long]]::new();$names=[Collections.Generic.List[string]]::new()
    $current=$vmt;$instanceSize=0
    for($depth=0;$current -ne 0;$depth++){
        if($depth -ge 32 -or $current -lt ($Context.Base+0x58) -or $current -ge $Context.End -or $ancestors.Contains($current)){throw 'Invalid or cyclic native VMT ancestry.'}
        $ancestors.Add($current)
        $metadata=Get-NativeInventoryBytes $Context ($current-0x38) 12
        $namePointer=[long][BitConverter]::ToUInt32($metadata,0)
        $size=[long][BitConverter]::ToUInt32($metadata,4)
        if($size -lt 4 -or $size -gt 1048576 -or $namePointer -lt $Context.Base -or $namePointer -ge $Context.End){throw 'Invalid native class metadata.'}
        if($depth -eq 0){$instanceSize=$size}
        $nameLength=[int]$Context.Reader.U8($namePointer)
        if($nameLength -lt 1 -or $nameLength -gt 100 -or ($namePointer+1+$nameLength) -gt $Context.End){throw 'Invalid native class name.'}
        $names.Add($Context.Ascii.GetString((Get-NativeInventoryBytes $Context ($namePointer+1) $nameLength)))
        $parentSlot=[long][BitConverter]::ToUInt32($metadata,8)
        if($parentSlot -eq 0){$current=0}else{
            if($parentSlot -lt $Context.Base -or ($parentSlot+4) -gt $Context.End){throw 'Invalid native VMT parent slot.'}
            $current=[long]$Context.Reader.U32($parentSlot)
        }
    }
    $kind='unknown'
    if($ancestors.Contains($Context.Base+0x8680FC)){$kind='controller'}
    elseif($ancestors.Contains($Context.Base+0x867A2C)){$kind='module'}
    elseif($ancestors.Contains($Context.Base+0x86660C)){$kind='channel'}
    elseif($ancestors.Contains($Context.Base+0x86600C)){$kind='group'}
    elseif($ancestors.Contains($Context.Base+0x864808)){$kind='console'}
    elseif($ancestors.Contains($Context.Base+0x8645D4)){$kind='console_list'}
    $type=[pscustomobject]@{vmt=$vmt;vmt_rva=($vmt-$Context.Base);name=$names[0];instance_size=$instanceSize;kind=$kind;ancestors=$ancestors.ToArray();ancestor_names=$names.ToArray()}
    $Context.Types[$key]=$type
    return $type
}

function Get-NativeInventoryList($Context,[long]$Address,[int]$Limit,[string]$Role) {
    if($Context.ContainsKey('Lists')-and $Context.Lists.ContainsKey([string]$Address)){$cached=$Context.Lists[[string]$Address];if($cached.count-gt $Limit){throw "Inventory $Role exceeds its list bound."};return $cached}
    $type=Get-NativeInventoryType $Context $Address
    if(-not ($type.ancestors -contains ($Context.Base+0x47660))){throw "Inventory $Role is not a TList descendant."}
    $header=Get-NativeInventoryBytes $Context $Address 16
    $items=[long][BitConverter]::ToUInt32($header,4);$count=[BitConverter]::ToInt32($header,8);$capacity=[BitConverter]::ToInt32($header,12)
    if($count -lt 0 -or $count -gt $Limit -or $capacity -lt $count -or $capacity -gt 1048576 -or ($count -gt 0 -and $items -eq 0)){throw "Invalid inventory $Role list bounds."}
    $raw=[byte[]]@();if($count -gt 0){$raw=Get-NativeInventoryBytes $Context $items (4*$count)}
    $values=New-Object uint32[] $count
    for($i=0;$i -lt $count;$i++){$values[$i]=[BitConverter]::ToUInt32($raw,4*$i)}
    $Context.ListWitnesses.Add([pscustomobject]@{address=$Address;items=$items;count=$count;header_hex=(ConvertTo-NativeInventoryHex $header);items_hex=(ConvertTo-NativeInventoryHex $raw);role=$Role})
    $result=[pscustomobject]@{pointer=$Address;vmt=$type.vmt;class_name=$type.name;items_pointer=$items;count=$count;capacity=$capacity;values=$values}
    if($Context.ContainsKey('Lists')){$Context.Lists[[string]$Address]=$result}
    return $result
}

function Add-NativeInventoryIdentity($Context,[long]$Address,[byte[]]$Bytes,[int]$Offset,[int]$Length) {
    $Context.Identities.Add([pscustomobject]@{address=($Address+$Offset);length=$Length;hex=(ConvertTo-NativeInventoryHex $Bytes $Offset $Length)})
}

function Test-NativeInventoryCompactIdentities($Context){
    # Validation uses a NEW cache of fresh RPM pages, never traversal data.
    # One page supplies many adjacent controller identity checks. An unreadable
    # page or a cross-page witness falls back to the original exact read.
    $pages=@{};$pageReads=0;$fallbacks=0
    foreach($w in $Context.Identities){
        $page=[long]$w.address-([long]$w.address%4096);$offset=[int]([long]$w.address-$page);$data=$null
        if($w.length-le 4096-and ($offset+$w.length)-le 4096){
            $key=[string]$page
            if(-not $pages.ContainsKey($key)){try{$pages[$key]=[byte[]]$Context.Reader.Read($page,4096);$pageReads++}catch{$pages[$key]=$null}}
            $data=$pages[$key]
        }
        if($null-ne $data){$actual=([BitConverter]::ToString([byte[]]$data,$offset,[int]$w.length)).Replace('-','').ToLowerInvariant()}
        else{$fallbacks++;$actual=([BitConverter]::ToString([byte[]]$Context.Reader.Read([long]$w.address,[int]$w.length))).Replace('-','').ToLowerInvariant()}
        if($actual-ne $w.hex){throw 'An object identity/context changed during compact inventory.'}
    }
    return [pscustomobject]@{identity_page_reads=$pageReads;identity_exact_fallback_reads=$fallbacks}
}

function Get-NativeInventoryDefinition($Context,[long]$Address) {
    if($Address -le 0 -or ($Address+58) -gt 0x100000000L){throw ("Invalid controller definition 0x{0:x}; controller=0x{1:x}, id={2}" -f $Address,$Context.CurrentObject,$Context.CurrentId)}
    $key=[string]$Address
    if($Context.Definitions.ContainsKey($key)){return $Context.Definitions[$key]}
    $raw=Get-NativeInventoryBytes $Context $Address 58
    Add-NativeInventoryIdentity $Context $Address $raw 0 58
    $name=Get-NativeInventoryShort $Context $raw 7 31 $true
    $units=Get-NativeInventoryShort $Context $raw 0x27 2 $true
    $seed=[long][BitConverter]::ToUInt32($raw,0x32);$lut=[long][BitConverter]::ToUInt32($raw,0x36)
    $rva=$null;$source='runtime_outside_image';if($Address -ge $Context.Base -and ($Address+58) -le $Context.End){$rva=$Address-$Context.Base;$source='executable_image'}
    $entry=[ordered]@{pointer=$Address;rva=$rva;location=$source;raw_hex=(ConvertTo-NativeInventoryHex $raw);data_type=[int]$raw[0];default_value=(Get-NativeInventoryValue $raw 1);band_id=[int]$raw[5];controller_id=[int]$raw[6];name=$name.text;name_hex=$name.raw_hex;name_decoded=$name.decoded;units=$units.text;units_hex=$units.raw_hex;auto_flags=[int]$raw[0x2A];snapshot_type=[int]$raw[0x2B];gang_flag=[int]$raw[0x2C];macro_flag=[int]$raw[0x2D];colour=[BitConverter]::ToInt32($raw,0x2E);response_seed_pointer=$seed;response_lut_pointer=$lut}
    if($Context.IncludeResponseTables){
        if($seed -ne 0){
            $seedRaw=Get-NativeInventoryBytes $Context $seed 38
            $n=[int][BitConverter]::ToUInt16($seedRaw,32);$pointsPointer=[long][BitConverter]::ToUInt32($seedRaw,34)
            if($n -gt 4096 -or ($n -gt 0 -and $pointsPointer -eq 0)){throw 'Invalid response-seed point bounds.'}
            $points=[Collections.Generic.List[object]]::new()
            if($n -gt 0){$pointRaw=Get-NativeInventoryBytes $Context $pointsPointer (8*$n);for($i=0;$i -lt $n;$i++){$point=Get-NativeInventorySlice $pointRaw (8*$i) 8;$points.Add([pscustomobject]@{position=[BitConverter]::ToInt32($point,0);value=(Get-NativeInventoryValue $point 4);raw_hex=(ConvertTo-NativeInventoryHex $point)})}}
            $entry.response_seed_raw_hex=ConvertTo-NativeInventoryHex $seedRaw;$entry.response_curve_type=[int]$seedRaw[0];$entry.response_point_count=$n;$entry.response_points=$points.ToArray()
        }
        if($lut -ne 0){$entry.response_lut_raw_hex=ConvertTo-NativeInventoryHex (Get-NativeInventoryBytes $Context $lut 1024)}
    }
    $definition=[pscustomobject]$entry;$Context.Definitions[$key]=$definition
    return $definition
}

function Add-NativeInventoryEdge($Context,[long]$Pointer,[long]$Parent,[string]$Role,[int]$Index=-1) {
    if($Pointer -ne 0){$Context.Queue.Enqueue([pscustomobject]@{pointer=$Pointer;parent=$Parent;role=$Role;index=$Index})}
}

function Get-NativeInventoryOwnerModeContext($Context,[long]$Pointer,$Type,[byte[]]$OwnerId,[long]$Callback) {
    $profile=$null
    if($Type.vmt_rva -eq 0x81C240){$profile=@{Name='compressor_v2126';ModeOffset=0x54;Parameter=0x3A;Band=0;ResendingOffset=0xE4;MinimumSize=0xE5;CallbackRva=0x82BAC0;TimerOffset=$null}}
    if($Type.vmt_rva -eq 0x81C8C0){$profile=@{Name='gate_v2126';ModeOffset=0x90;Parameter=0x0B;Band=3;ResendingOffset=0x9E;MinimumSize=0x9F;CallbackRva=0x82D3DC;TimerOffset=0x9D}}
    if($null -eq $profile){return $null}
    if($Type.instance_size -lt $profile.MinimumSize -or $Callback -ne ($Context.Base+$profile.CallbackRva)){throw 'Native dynamics context has an unexpected class size/callback.'}
    $modeSlot=Get-NativeInventoryBytes $Context ($Pointer+$profile.ModeOffset) 4
    $modePointer=[long][BitConverter]::ToUInt32($modeSlot,0)
    if($modePointer -eq 0){throw 'Native dynamics mode controller is missing.'}
    $modeType=Get-NativeInventoryType $Context $modePointer
    if($modeType.kind -ne 'controller' -or $modeType.instance_size -lt 0x5C){throw 'Native dynamics mode has an unexpected controller class.'}
    $mode=Get-NativeInventoryBytes $Context $modePointer 0x5C
    $modeId=Get-NativeInventorySlice $mode 4 5
    if($OwnerId[1] -ne 5 -or $modeId[0] -ne $profile.Parameter -or $modeId[1] -ne 5 -or $modeId[2] -ne $profile.Band -or $modeId[3] -ne $OwnerId[3] -or $modeId[4] -ne $OwnerId[4] -or [BitConverter]::ToUInt32($mode,0x44) -ne $Pointer){throw 'Native dynamics mode identity/owner binding differs.'}
    $definitionPointer=[long][BitConverter]::ToUInt32($mode,9)
    $definition=Get-NativeInventoryDefinition $Context $definitionPointer
    if($definition.data_type -ne 0 -or $definition.controller_id -ne $profile.Parameter -or $definition.band_id -ne $profile.Band){throw 'Native dynamics mode definition differs.'}
    $children=Get-NativeInventoryList $Context $Pointer 65536 'dynamics_context_owner'
    if(-not ($children.values -contains [uint32]$modePointer)){throw 'Native dynamics mode is not a member of its owner module.'}
    $resendingRaw=Get-NativeInventoryBytes $Context ($Pointer+$profile.ResendingOffset) 1
    $resending=0;if($resendingRaw[0] -ne 0){$resending=1}
    # Only metadata plus the context governing parameter bounds is witnessed.
    # Ordinary dynamics values remain sequential samples, not atomic state.
    Add-NativeInventoryIdentity $Context ($Pointer+$profile.ModeOffset) $modeSlot 0 4
    Add-NativeInventoryIdentity $Context ($Pointer+$profile.ResendingOffset) $resendingRaw 0 1
    Add-NativeInventoryIdentity $Context $modePointer $mode 0 13
    Add-NativeInventoryIdentity $Context $modePointer $mode 0x44 4
    Add-NativeInventoryIdentity $Context $modePointer $mode 0x2C 4
    Add-NativeInventoryEdge $Context $modePointer $Pointer 'dynamics_mode_context_control'
    $result=[ordered]@{profile=$profile.Name;owner_module_pointer=$Pointer;mode_controller_pointer=$modePointer;mode_id_hex=(ConvertTo-NativeInventoryHex $modeId);mode_output_raw_hex=(ConvertTo-NativeInventoryHex (Get-NativeInventorySlice $mode 0x2C 4));resending=$resending}
    if($null -ne $profile.TimerOffset){$timerRaw=Get-NativeInventoryBytes $Context ($Pointer+$profile.TimerOffset) 1;Add-NativeInventoryIdentity $Context ($Pointer+$profile.TimerOffset) $timerRaw 0 1;$result.mode_changed_timer_count=[int]$timerRaw[0]}
    return [pscustomobject]$result
}

function Get-NativeInventoryModuleOwner($Context,[long]$Pointer) {
    $key=[string]$Pointer
    if($Context.ModuleOwners.ContainsKey($key)){return $Context.ModuleOwners[$key]}
    $type=Get-NativeInventoryType $Context $Pointer
    if($type.kind -ne 'module'){throw ("Controller parent 0x{0:x} is not a module; controller=0x{1:x}, id={2}" -f $Pointer,$Context.CurrentObject,$Context.CurrentId)}
    $id=Get-NativeInventoryBytes $Context ($Pointer+0x30) 5
    $callback=[long]$Context.Reader.U32($type.vmt+0x40)
    if($callback -lt $Context.Base -or $callback -ge $Context.End){throw 'Module ProcessOutput callback is outside the verified image.'}
    Add-NativeInventoryIdentity $Context ($Pointer+0x30) $id 0 5
    $ownerContext=Get-NativeInventoryOwnerModeContext $Context $Pointer $type $id $callback
    $entry=[pscustomobject]@{pointer=$Pointer;type=$type;id_raw=$id;id_hex=(ConvertTo-NativeInventoryHex $id);process_output_rva=($callback-$Context.Base);owner_native_context=$ownerContext;eq_q_contexts=@{}}
    $Context.ModuleOwners[$key]=$entry
    return $entry
}

function Get-NativeInventoryEQQContext($Context,$Owner,[byte[]]$Id){
    if($Id[0] -ne 6 -or $Id[1] -ne 4 -or $Owner.type.vmt_rva -ne 0x81BD7C -or $Owner.process_output_rva -ne 0x82A938){return $null}
    $band=[int]$Id[2];if($band -gt 7){throw 'EQ Q band exceeds the inspected shape array.'}
    if($Owner.eq_q_contexts.ContainsKey([string]$band)){return $Owner.eq_q_contexts[[string]$band]}
    $slotOffset=0x58+24*$band
    if($Owner.type.instance_size -lt ($slotOffset+4)){throw 'EQ shape slot exceeds the native module instance.'}
    $slot=Get-NativeInventoryBytes $Context ($Owner.pointer+$slotOffset) 4
    $pointer=[long][BitConverter]::ToUInt32($slot,0)
    if($pointer -eq 0){throw 'EQ Q has no native shape controller.'}
    $type=Get-NativeInventoryType $Context $pointer
    if($type.kind -ne 'controller' -or $type.instance_size -lt 0x5C){throw 'EQ shape has an unexpected controller class.'}
    $raw=Get-NativeInventoryBytes $Context $pointer 0x5C
    if($raw[4] -ne 4 -or $raw[5] -ne 4 -or $raw[6] -ne $band -or $raw[7] -ne $Id[3] -or $raw[8] -ne $Id[4] -or [BitConverter]::ToUInt32($raw,0x44) -ne $Owner.pointer){throw 'EQ shape identity/owner binding differs.'}
    $definition=Get-NativeInventoryDefinition $Context ([long][BitConverter]::ToUInt32($raw,9))
    if($definition.data_type -ne 0 -or $definition.controller_id -ne 4 -or $definition.band_id -ne $band){throw 'EQ shape definition differs.'}
    $children=Get-NativeInventoryList $Context $Owner.pointer 65536 'eq_context_owner'
    if(-not ($children.values -contains [uint32]$pointer)){throw 'EQ shape is not a member of its owner module.'}
    Add-NativeInventoryIdentity $Context ($Owner.pointer+$slotOffset) $slot 0 4
    Add-NativeInventoryIdentity $Context $pointer $raw 0 13
    Add-NativeInventoryIdentity $Context $pointer $raw 0x44 4
    Add-NativeInventoryIdentity $Context $pointer $raw 0x2C 4
    Add-NativeInventoryEdge $Context $pointer $Owner.pointer 'eq_q_shape_context_control'
    $entry=[pscustomobject][ordered]@{profile='eq_q_v2126';owner_module_pointer=$Owner.pointer;mode_controller_pointer=$pointer;mode_id_hex=(ConvertTo-NativeInventoryHex (Get-NativeInventorySlice $raw 4 5));mode_output_raw_hex=(ConvertTo-NativeInventoryHex (Get-NativeInventorySlice $raw 0x2C 4))}
    $Owner.eq_q_contexts[[string]$band]=$entry
    return $entry
}

function Read-NativeInventoryIODeviceList($Context,[long]$GlobalRva,[string]$Role) {
    $globalAddress=$Context.Base+$GlobalRva;$pointer=[long]$Context.Reader.U32($globalAddress)
    $globalRaw=Get-NativeInventoryBytes $Context $globalAddress 4
    Add-NativeInventoryIdentity $Context $globalAddress $globalRaw 0 4
    $rows=[Collections.Generic.List[object]]::new()
    if($pointer -eq 0){return [pscustomobject]@{role=$Role;global_rva=$GlobalRva;pointer=0;initialized=$false;count=0;devices=$rows.ToArray()}}
    $type=Get-NativeInventoryType $Context $pointer
    if(-not ($type.ancestors -contains ($Context.Base+0x2F85BC))){throw 'Unexpected I/O-device-list class.'}
    $list=Get-NativeInventoryList $Context $pointer 4096 $Role
    for($i=0;$i -lt $list.count;$i++){
        $device=[long]$list.values[$i]
        if($device -eq 0){throw 'A native I/O device entry is null.'}
        $deviceType=Get-NativeInventoryType $Context $device
        if(-not ($deviceType.ancestors -contains ($Context.Base+0x2F7C58)) -or $deviceType.instance_size -lt 0x2C){throw 'Unexpected I/O-device class.'}
        $raw=Get-NativeInventoryBytes $Context $device 0x2C
        $inputs=[BitConverter]::ToInt32($raw,4);$outputs=[BitConverter]::ToInt32($raw,8)
        $inputArray=[long][BitConverter]::ToUInt32($raw,0x19);$outputArray=[long][BitConverter]::ToUInt32($raw,0x1D)
        if($inputs -lt 0 -or $inputs -gt 65536 -or $outputs -lt 0 -or $outputs -gt 65536 -or ($inputs -gt 0 -and $inputArray -eq 0) -or ($outputs -gt 0 -and $outputArray -eq 0)){throw 'Invalid native I/O socket-array bounds.'}
        $inputTypes=[byte[]]@();$outputTypes=[byte[]]@()
        if($inputs -gt 0){$inputTypes=Get-NativeInventoryBytes $Context $inputArray $inputs}
        if($outputs -gt 0){$outputTypes=Get-NativeInventoryBytes $Context $outputArray $outputs}
        Add-NativeInventoryIdentity $Context $device $raw 0 16
        Add-NativeInventoryIdentity $Context $device $raw 0x19 8
        $rows.Add([pscustomobject][ordered]@{index=$i;pointer=$device;class_name=$deviceType.name;vmt=$deviceType.vmt;data_raw_hex=(ConvertTo-NativeInventoryHex (Get-NativeInventorySlice $raw 4 12));input_count=$inputs;output_count=$outputs;device_type=[int]$raw[0xC];port_number=[int]$raw[0xE];port_type=[int]$raw[0xF];port_key=[int][BitConverter]::ToUInt16($raw,0xE);version=[BitConverter]::ToInt32($raw,0x10);online=([int]$raw[0x14] -ne 0);ayt_counter=[BitConverter]::ToInt32($raw,0x15);input_socket_types_hex=(ConvertTo-NativeInventoryHex $inputTypes);output_socket_types_hex=(ConvertTo-NativeInventoryHex $outputTypes);input_socket_types_pointer=$inputArray;output_socket_types_pointer=$outputArray})
    }
    return [pscustomobject]@{role=$Role;global_rva=$GlobalRva;pointer=$pointer;initialized=$true;count=$list.count;devices=$rows.ToArray()}
}

function Read-NativeInventorySocketGroups($Context,[long]$GlobalRva,[string]$Role) {
    $globalAddress=$Context.Base+$GlobalRva;$globalRaw=Get-NativeInventoryBytes $Context $globalAddress 4
    Add-NativeInventoryIdentity $Context $globalAddress $globalRaw 0 4
    $pointer=[long][BitConverter]::ToUInt32($globalRaw,0);$rows=[Collections.Generic.List[object]]::new()
    if($pointer -eq 0){return [pscustomobject]@{role=$Role;global_rva=$GlobalRva;pointer=0;initialized=$false;count=0;groups=$rows.ToArray()}}
    $type=Get-NativeInventoryType $Context $pointer
    if(-not ($type.ancestors -contains ($Context.Base+0x2F977C))){throw 'Unexpected socket-group-list class.'}
    # This is a TStringList, not a TList. Get/GetObject/GetCount/GetCapacity
    # independently establish +20 items / +24 count / +28 capacity, stride 8.
    $header=Get-NativeInventoryBytes $Context $pointer 0x2C
    $items=[long][BitConverter]::ToUInt32($header,0x20);$count=[BitConverter]::ToInt32($header,0x24);$capacity=[BitConverter]::ToInt32($header,0x28)
    if($count -lt 0 -or $count -gt 4096 -or $capacity -lt $count -or $capacity -gt 65536 -or ($count -gt 0 -and $items -eq 0)){throw 'Invalid socket-group TStringList bounds.'}
    Add-NativeInventoryIdentity $Context $pointer $header 0 4
    Add-NativeInventoryIdentity $Context $pointer $header 0x20 12
    $itemRaw=[byte[]]@();if($count -gt 0){$itemRaw=Get-NativeInventoryBytes $Context $items (8*$count);Add-NativeInventoryIdentity $Context $items $itemRaw 0 $itemRaw.Length}
    for($i=0;$i -lt $count;$i++){
        $stringPointer=[long][BitConverter]::ToUInt32($itemRaw,8*$i);$objectValue=[BitConverter]::ToUInt32($itemRaw,8*$i+4)
        $name='';$rawName=[byte[]]@();$decoded=$true;$length=0
        if($stringPointer -ne 0){
            $length=$Context.Reader.I32($stringPointer-4)
            if($length -lt 0 -or $length -gt 4096){throw 'Invalid socket-group UnicodeString length.'}
            $rawName=Get-NativeInventoryBytes $Context $stringPointer (2*$length)
            try{$name=$Context.Utf16.GetString($rawName)}catch{$name=$null;$decoded=$false}
        }
        $rows.Add([pscustomobject]@{index=$i;name=$name;name_raw_hex=(ConvertTo-NativeInventoryHex $rawName);name_encoding='utf-16le';name_decoded=$decoded;string_pointer=$stringPointer;object_value_raw=$objectValue;entry_raw_hex=(ConvertTo-NativeInventoryHex (Get-NativeInventorySlice $itemRaw (8*$i) 8))})
    }
    return [pscustomobject]@{role=$Role;global_rva=$GlobalRva;pointer=$pointer;initialized=$true;count=$count;groups=$rows.ToArray()}
}

function Read-NativeInventoryPorts($Context,$Groups) {
    # CE8E9C is an address indirection to the inline port-record array, not
    # an extra heap-list global. 6FA584 and B5B2A6 establish index and stride.
    $array=[long]$Context.Reader.U32($Context.Base+0x8E8E9C)
    $indices=@{}
    foreach($group in $Groups){$id=[int]$group.id;$index=-1;if($id -ge 0x1C -and $id -lt 0x4D){$index=$id-0x1C};if($id -ge 0x4E -and $id -lt 0x7F){$index=$id-0x4E};if($index -ge 0){$indices[[string]$index]=$index}}
    $rows=[Collections.Generic.List[object]]::new()
    foreach($index in @($indices.Values|Sort-Object)){
        $address=$array+[long]$index*0xD2;$raw=Get-NativeInventoryBytes $Context $address 0xD2
        Add-NativeInventoryIdentity $Context $address $raw 0 0xD2
        $rows.Add([pscustomobject]@{index=[int]$index;pointer=$address;configuration_group_id=(0x1C+[int]$index);runtime_group_id=(0x4E+[int]$index);raw_hex=(ConvertTo-NativeInventoryHex $raw);port_type=[int]$raw[0xD0];port_number=[int]$raw[0xD1];port_key=([int]$raw[0xD1]+256*[int]$raw[0xD0]);device_family_raw=[int]$raw[0];engine_port_raw=[int]$raw[1]})
    }
    return ,$rows.ToArray()
}

function Read-NativeInventory([switch]$IncludeResponseTables,[int]$ChannelId=-1,[int]$ChannelType=-1,[int]$LogicalChannel=-1,[switch]$IncludePhysicalSources,[switch]$IncludeGroupOutputs,[switch]$DetailedFullInventory,[scriptblock]$ProgressCallback=$null) {
    # The caller supplies these variables after verifying the exact PE hash.
    if($null -eq $reader -or [long]$base -le 0 -or $null -eq $ascii -or $null -eq $nameEncoding){throw 'Inventory requires the verified runner reader/base/encoding context.'}
    if($ChannelId -lt -1 -or $ChannelId -gt 65535 -or $ChannelType -lt -1 -or $ChannelType -gt 255 -or $LogicalChannel -lt -1 -or $LogicalChannel -gt 65535){throw 'Invalid inventory channel selection.'}
    $filtered=($ChannelId -ge 0 -or $ChannelType -ge 0 -or $LogicalChannel -ge 0)
    $compact=(-not $filtered-and -not $DetailedFullInventory)
    $elapsed=[Diagnostics.Stopwatch]::StartNew()
    $ctx=@{Reader=$reader;Base=[long]$base;End=([long]$base+0x4440000);Ascii=$ascii;Encoding=$nameEncoding;Utf16=[Text.UnicodeEncoding]::new($false,$false,$true);EncodingName=[string]$request.encoding;IncludeResponseTables=([bool]$IncludeResponseTables-and -not $compact);CurrentObject=0L;CurrentId='';Types=@{};Definitions=@{};Lists=@{};ModuleOwners=@{};Visited=@{};SourceOnlyChannels=@{};Queue=[Collections.Generic.Queue[object]]::new();ListWitnesses=[Collections.Generic.List[object]]::new();Identities=[Collections.Generic.List[object]]::new()}
    $console=[long]$reader.U32($ctx.Base+0x1279CFC)
    if($console -eq 0){throw 'Console inventory is not initialized.'}
    $consoleType=Get-NativeInventoryType $ctx $console
    if($consoleType.vmt -ne ($ctx.Base+0x864808)){throw 'Unexpected console VMT for this executable profile.'}
    $groups=[Collections.Generic.List[object]]::new();$channels=[Collections.Generic.List[object]]::new();$modules=[Collections.Generic.List[object]]::new();$controllers=[Collections.Generic.List[object]]::new();$unknown=[Collections.Generic.List[object]]::new();$edges=[Collections.Generic.List[object]]::new()
    $edgeCount=0
    Add-NativeInventoryEdge $ctx $console 0 'console_global'
    while($ctx.Queue.Count -gt 0){
        $edge=$ctx.Queue.Dequeue();$pointer=[long]$edge.pointer;$ctx.CurrentObject=$pointer;$ctx.CurrentId=''
        $edgeCount++;if(-not $compact){$edges.Add($edge)}
        if($ctx.Visited.ContainsKey([string]$pointer)){continue}
        if($ctx.Visited.Count -ge 300000){throw 'Inventory exceeded the object resource bound.'}
        $type=Get-NativeInventoryType $ctx $pointer;$ctx.Visited[[string]$pointer]=$type.kind
        $common=[ordered]@{pointer=$pointer;vmt=$type.vmt;vmt_rva=$type.vmt_rva;class_name=$type.name;instance_size=$type.instance_size;first_parent_pointer=$edge.parent;first_edge=$edge.role;first_index=$edge.index}
        $minSize=switch($type.kind){'console'{0x6C};'group'{0xA4};'channel'{0x1E4};'module'{0x54};'controller'{0x5C};'console_list'{0x34};default{4}}
        if($type.instance_size -lt $minSize){throw 'Native object is smaller than the known base class.'}
        $raw=Get-NativeInventoryBytes $ctx $pointer $minSize
        if($type.kind -ne 'controller'){Add-NativeInventoryIdentity $ctx $pointer $raw 0 4}
        if($ProgressCallback -and ($ctx.Visited.Count -eq 1 -or ($ctx.Visited.Count % 1000) -eq 0)){$null=& $ProgressCallback ([pscustomobject]@{objects=$ctx.Visited.Count;channels=$channels.Count;controllers=$controllers.Count;queue=$ctx.Queue.Count;current_pointer=$pointer;current_kind=$type.kind})}
        if($type.kind -eq 'controller'){
            $ctx.CurrentId=ConvertTo-NativeInventoryHex $raw 4 5
            $definitionPointer=[long][BitConverter]::ToUInt32($raw,9);$definition=Get-NativeInventoryDefinition $ctx $definitionPointer
            $owner=[long][BitConverter]::ToUInt32($raw,0x44);$ownerId=$null;$ownerType=$null;$ownerInfo=$null
            if($owner -ne 0){$ownerInfo=Get-NativeInventoryModuleOwner $ctx $owner;$ownerType=$ownerInfo.type;$ownerId=$ownerInfo.id_hex}
            if([int]$raw[4] -ne $definition.controller_id){throw ("Controller parameter/definition mismatch; controller=0x{0:x}, id={1}, definition=0x{2:x}" -f $pointer,$ctx.CurrentId,$definitionPointer)}
            if($owner -ne 0){
                $ownerRaw=$ownerInfo.id_raw
                $expectedBand=[int]$ownerRaw[2];if($definition.band_id -ne 0 -or [int]$raw[5] -eq 5){$expectedBand=$definition.band_id}
                if([int]$raw[5] -ne [int]$ownerRaw[1] -or [int]$raw[6] -ne $expectedBand -or [int]$raw[7] -ne [int]$ownerRaw[3] -or [int]$raw[8] -ne [int]$ownerRaw[4]){throw ("Controller/module binding mismatch; controller=0x{0:x}, id={1}, module=0x{2:x}, moduleid={3}, definition=0x{4:x}" -f $pointer,$ctx.CurrentId,$owner,$ownerId,$definitionPointer)}
            }
            Add-NativeInventoryIdentity $ctx $pointer $raw 0 13
            Add-NativeInventoryIdentity $ctx $pointer $raw 0x44 4
            $common.raw_id_hex=$ctx.CurrentId
            $common.parameter=[int]$raw[4];$common.module=[int]$raw[5];$common.band=[int]$raw[6];$common.channel=[int]$raw[7];$common.channel_type=[int]$raw[8]
            $common.definition_pointer=$definitionPointer;$common.definition_rva=$definition.rva;$common.definition_location=$definition.location;$common.definition_raw_hex=$definition.raw_hex;$common.data_type=$definition.data_type;$common.definition_name=$definition.name;$common.definition_binding_verified=($owner -ne 0)
            $common.owner_module_pointer=$owner;$common.owner_module_id_raw=$ownerId;$common.module_id_hex=$ownerId
            $common.module_vmt_rva=$null;$common.process_output_rva=$null
            if($ownerType){$common.module_vmt_rva=$ownerType.vmt_rva;$common.process_output_rva=$ownerInfo.process_output_rva}
            if($ownerInfo -and $null -ne $ownerInfo.owner_native_context){$common.owner_native_context=$ownerInfo.owner_native_context;$common.native_context=$ownerInfo.owner_native_context}
            if($ownerInfo -and $raw[4] -eq 6 -and $raw[5] -eq 4){$qContext=Get-NativeInventoryEQQContext $ctx $ownerInfo (Get-NativeInventorySlice $raw 4 5);if($null-ne $qContext){$common.owner_native_context=$qContext;$common.native_context=$qContext}}
            $common.states=[int]$raw[0x11];$common.flags_u16=[int][BitConverter]::ToUInt16($raw,0x12);$common.changes=$common.flags_u16
            if(-not $compact){$common.value=Get-NativeInventoryValue $raw 0x18;$common.next_value=Get-NativeInventoryValue $raw 0x1C;$common.db_value=Get-NativeInventoryValue $raw 0x20;$common.next_db_value=Get-NativeInventoryValue $raw 0x24;$common.previous_output_value=Get-NativeInventoryValue $raw 0x28;$common.output_value=Get-NativeInventoryValue $raw 0x2C;$common.restore_value=Get-NativeInventoryValue $raw 0x50}
            $common.value_raw_hex=ConvertTo-NativeInventoryHex $raw 0x18 4;$common.target_raw_hex=ConvertTo-NativeInventoryHex $raw 0x1C 4;$common.output_value_raw_hex=ConvertTo-NativeInventoryHex $raw 0x2C 4
            if($compact){$common.value_region_raw_hex=ConvertTo-NativeInventoryHex $raw 0x18 24;$common.restore_value_raw_hex=ConvertTo-NativeInventoryHex $raw 0x50 4}
            $common.auto_mode=[int]$raw[0x30];$common.next_auto_mode=[int]$raw[0x31];$common.display_style=[int]$raw[0x35];$common.flash_level=[int]$raw[0x36];$common.input_time=[BitConverter]::ToInt32($raw,0x3B)
            $common.audio_id_raw_hex=ConvertTo-NativeInventoryHex (Get-NativeInventorySlice $raw 0x48 8)
            $controllers.Add([pscustomobject]$common)
            continue
        }
        if($type.kind -eq 'unknown'){$unknown.Add([pscustomobject]$common);continue}
        if($type.kind -eq 'channel'){
            $selected=(($ChannelId -lt 0 -or [int][BitConverter]::ToUInt16($raw,0x30) -eq $ChannelId) -and ($ChannelType -lt 0 -or [int]$raw[0x31] -eq $ChannelType) -and ($LogicalChannel -lt 0 -or [int][BitConverter]::ToUInt16($raw,0x32) -eq $LogicalChannel))
            $physical='';if([int]$raw[0x31] -ge 0x1C -and [int]$raw[0x31] -lt 0x4D){$physical='input'};if([int]$raw[0x31] -ge 0x4E -and [int]$raw[0x31] -lt 0x7F){$physical='output'}
            if(-not $selected){if($IncludePhysicalSources -and $physical){$ctx.SourceOnlyChannels[[string]$pointer]=$physical}elseif($IncludeGroupOutputs -and [int]$raw[0x31]-eq 6){$ctx.SourceOnlyChannels[[string]$pointer]='group'}else{continue}}
        }
        $common.parent_pointer=[long][BitConverter]::ToUInt32($raw,0x24)
        $sourceOnly=($type.kind -eq 'channel' -and $ctx.SourceOnlyChannels.ContainsKey([string]$pointer))
        $sourceOnlyModule=($type.kind -eq 'module' -and $ctx.SourceOnlyChannels.ContainsKey([string]$common.parent_pointer))
        $selectionRole='';if($sourceOnly){$selectionRole=$ctx.SourceOnlyChannels[[string]$pointer]};if($sourceOnlyModule){$selectionRole=$ctx.SourceOnlyChannels[[string]$common.parent_pointer]}
        if($sourceOnlyModule){$allowedModule=@('tInputSocketModule','tOutputSocketModule');if($selectionRole-eq 'group'){$allowedModule=@('tFaderModule','tOutputModule')};if($type.name-notin $allowedModule){continue}}
        $common.physical_source_only=(($sourceOnly -or $sourceOnlyModule)-and $selectionRole-ne 'group');$common.group_output_only=($selectionRole-eq 'group')
        Add-NativeInventoryIdentity $ctx $pointer $raw 0x24 4
        $list=Get-NativeInventoryList $ctx $pointer 65536 $type.kind
        $common.child_count=$list.count
        for($i=0;$i -lt $list.count;$i++){if($list.values[$i] -eq 0){throw 'A console-tree child is null.'};if(-not $sourceOnlyModule){Add-NativeInventoryEdge $ctx ([long]$list.values[$i]) $pointer 'list_child' $i}}
        switch($type.kind){
            'console'{
                if(-not $filtered){for($i=0;$i -lt 4;$i++){Add-NativeInventoryEdge $ctx ([long][BitConverter]::ToUInt32($raw,0x30+4*$i)) $pointer 'global_rp_control' $i};Add-NativeInventoryEdge $ctx ([long][BitConverter]::ToUInt32($raw,0x40)) $pointer 'global_track_arm'}
            }
            'group'{
                $name=Get-NativeInventoryShort $ctx $raw 0x31 30
                $common.id=[int]$raw[0x30];$common.name=$name.text;$common.name_hex=$name.raw_hex;$common.name_decoded=$name.decoded
                Add-NativeInventoryIdentity $ctx $pointer $raw 0x30 1
                $common.controller_recall_scope_hex=ConvertTo-NativeInventoryHex (Get-NativeInventorySlice $raw 0x59 32);$common.controller_update_scope_hex=ConvertTo-NativeInventoryHex (Get-NativeInventorySlice $raw 0x79 32)
                $groups.Add([pscustomobject]$common)
            }
            'channel'{
                $name=Get-NativeInventoryShort $ctx $raw 0x34 31
                $common.id=[int][BitConverter]::ToUInt16($raw,0x30);$common.logical_channel=[int][BitConverter]::ToUInt16($raw,0x32);$common.channel_type=[int]$raw[0x31];$common.name=$name.text;$common.name_hex=$name.raw_hex;$common.name_decoded=$name.decoded
                Add-NativeInventoryIdentity $ctx $pointer $raw 0x30 4
                $common.stereo_mode=[int]$raw[0x55];$common.buss_id=[int][BitConverter]::ToUInt16($raw,0x62);$common.engine_channel=[int][BitConverter]::ToInt16($raw,0x80);$common.tidal_channel=[int][BitConverter]::ToInt16($raw,0x82)
                $common.socket_group_index=[BitConverter]::ToInt32($raw,0xAE);$common.input_route=[BitConverter]::ToInt32($raw,0xB2);$common.alt_input_route=[BitConverter]::ToInt32($raw,0xB6);$common.key_route=[BitConverter]::ToInt32($raw,0xBA);$common.buss_routes_hex=ConvertTo-NativeInventoryHex (Get-NativeInventorySlice $raw 0xBE 32)
                $sockets=[long][BitConverter]::ToUInt32($raw,0xF1);$common.output_sockets_pointer=$sockets;$common.output_sockets=@()
                if($sockets -ne 0){$socketList=Get-NativeInventoryList $ctx $sockets 65536 'output_sockets';$common.output_sockets=$socketList.values}
                if(-not $sourceOnly){for($i=0;$i -lt 4;$i++){Add-NativeInventoryEdge $ctx ([long][BitConverter]::ToUInt32($raw,0x1BA+4*$i)) $pointer 'channel_control_reference' $i}}
                for($i=0;$i -lt 3;$i++){Add-NativeInventoryEdge $ctx ([long][BitConverter]::ToUInt32($raw,0x1AE+4*$i)) $pointer 'channel_module_reference' $i}
                $common.data_record_raw_hex=ConvertTo-NativeInventoryHex (Get-NativeInventorySlice $raw 0x30 0x175)
                $channels.Add([pscustomobject]$common)
            }
            'module'{
                $common.raw_id_hex=ConvertTo-NativeInventoryHex (Get-NativeInventorySlice $raw 0x30 5)
                $lib=[long][BitConverter]::ToUInt32($raw,0x35);$common.definition_pointer=$lib
                if($lib -ne 0){$libRaw=Get-NativeInventoryBytes $ctx $lib 23;$name=Get-NativeInventoryShort $ctx $libRaw 1 15 $true;$common.definition_raw_hex=ConvertTo-NativeInventoryHex $libRaw;$common.type_id=[int]$libRaw[0];$common.name=$name.text;$common.name_hex=$name.raw_hex;$common.library_controller_count=[int][BitConverter]::ToUInt16($libRaw,0x11);$common.library_controllers_pointer=[long][BitConverter]::ToUInt32($libRaw,0x13)}
                Add-NativeInventoryIdentity $ctx $pointer $raw 0x30 9
                $common.changed_since_snapshot=([int]$raw[0x39] -ne 0);$common.child_preset_active=([int]$raw[0x3A] -ne 0);$common.input_gain=Get-NativeInventoryValue $raw 0x3B;$common.n_signals=[BitConverter]::ToInt32($raw,0x4A)
                if($type.vmt_rva -in @(0x81C240,0x81C8C0)){$ownerInfo=Get-NativeInventoryModuleOwner $ctx $pointer;$common.owner_native_context=$ownerInfo.owner_native_context;$common.native_context=$ownerInfo.owner_native_context}
                if($type.name -eq 'tInputSocketModule'){
                    $socketRaw=Get-NativeInventoryBytes $ctx $pointer 0x80
                    $common.socket_direction='input';$common.copy_to_route_control_pointer=[long][BitConverter]::ToUInt32($socketRaw,0x78);$common.listen_to_copy_control_pointer=[long][BitConverter]::ToUInt32($socketRaw,0x7C)
                    Add-NativeInventoryIdentity $ctx $pointer $socketRaw 0x78 8
                    Add-NativeInventoryEdge $ctx $common.copy_to_route_control_pointer $pointer 'physical_copy_route_control'
                    Add-NativeInventoryEdge $ctx $common.listen_to_copy_control_pointer $pointer 'physical_listen_copy_control'
                }
                if($type.name -eq 'tOutputSocketModule'){
                    $socketRaw=Get-NativeInventoryBytes $ctx $pointer 0x88
                    $common.socket_direction='output';$common.route_control_pointer=[long][BitConverter]::ToUInt32($socketRaw,0x54);$common.socket_active=([int]$socketRaw[0x83] -ne 0)
                    Add-NativeInventoryIdentity $ctx $pointer $socketRaw 0x54 4
                    Add-NativeInventoryEdge $ctx $common.route_control_pointer $pointer 'physical_output_source_route_control'
                }
                if(-not $sourceOnlyModule){Add-NativeInventoryEdge $ctx ([long][BitConverter]::ToUInt32($raw,0x44)) $pointer 'module_gang_control'}
                $modules.Add([pscustomobject]$common)
            }
            default{$unknown.Add([pscustomobject]$common)}
        }
    }
    # These distinct global tables are used by session configuration and runtime
    # device handling respectively. "online" is the editor's flag, not a probe.
    $configuredIO=Read-NativeInventoryIODeviceList $ctx 0x911110 'session_io_configuration'
    $runtimeIO=Read-NativeInventoryIODeviceList $ctx 0x911114 'runtime_io_devices'
    $configuredGroups=Read-NativeInventorySocketGroups $ctx 0x913A2C 'session_socket_groups'
    $runtimeGroups=Read-NativeInventorySocketGroups $ctx 0x913A30 'runtime_socket_groups'
    $ports=Read-NativeInventoryPorts $ctx $groups
    $discriminatorPointer=[long]$reader.U32($ctx.Base+0x8E92A8)
    $discriminatorRaw=Get-NativeInventoryBytes $ctx $discriminatorPointer 1
    Add-NativeInventoryIdentity $ctx $discriminatorPointer $discriminatorRaw 0 1
    $discriminator=[int]$discriminatorRaw[0]
    $walkElapsed=$elapsed.ElapsedMilliseconds
    # Validate structural witnesses after traversal; values are a sequential read,
    # not an atomic audio-state snapshot. A load/rebuild makes this call fail.
    if([long]$reader.U32($ctx.Base+0x1279CFC) -ne $console){throw 'Console changed during inventory.'}
    foreach($w in $ctx.ListWitnesses){
        if((ConvertTo-NativeInventoryHex (Get-NativeInventoryBytes $ctx $w.address 16)) -ne $w.header_hex){throw 'A list header changed during inventory.'}
        if($w.count -gt 0 -and (ConvertTo-NativeInventoryHex (Get-NativeInventoryBytes $ctx $w.items (4*$w.count))) -ne $w.items_hex){throw 'A list membership changed during inventory.'}
    }
    $verificationStats=$null
    if($compact){$verificationStats=Test-NativeInventoryCompactIdentities $ctx}else{foreach($w in $ctx.Identities){if((ConvertTo-NativeInventoryHex (Get-NativeInventoryBytes $ctx $w.address $w.length)) -ne $w.hex){throw 'An object identity changed during inventory.'}}}
    $groupSelectionCount=@($ctx.SourceOnlyChannels.Values|Where-Object {$_-eq 'group'}).Count
    $detail='filtered_detailed';if(-not $filtered){$detail='full_detailed';if($compact){$detail='full_compact_raw'}}
    return [pscustomobject][ordered]@{schema_version=1;profile='quantum1-v2126';executable_sha256='535097f38bb459e6e299b5da50b62fa811511d92af6d7215a13d703a416f177d';image_base=$ctx.Base;memory_read_only=$true;structure_verified=$true;values_atomic=$false;inventory_detail=$detail;response_tables_requested=[bool]$IncludeResponseTables;response_tables_included=[bool]$ctx.IncludeResponseTables;value_views_included=(-not $compact);edges_included=(-not $compact);edge_count=$edgeCount;performance=[pscustomobject]@{walk_elapsed_ms=$walkElapsed;verification_elapsed_ms=($elapsed.ElapsedMilliseconds-$walkElapsed);object_count=$ctx.Visited.Count;identity_witness_count=$ctx.Identities.Count;list_witness_count=$ctx.ListWitnesses.Count;compact_validation=$verificationStats};selection=[pscustomobject]@{full_tree=(-not $filtered);channel_id=$ChannelId;channel_type=$ChannelType;logical_channel=$LogicalChannel;include_physical_sources=[bool]$IncludePhysicalSources;include_group_outputs=[bool]$IncludeGroupOutputs;physical_source_only_channel_count=($ctx.SourceOnlyChannels.Count-$groupSelectionCount);group_output_only_channel_count=$groupSelectionCount;physical_source_only_controls_included=$true;physical_source_control_scope='input copy-route/listen-copy and output source-route only; Group outputs: Fader/Output metadata'};name_encoding=$ctx.EncodingName;console_pointer=$console;console_vmt=$consoleType.vmt;console_type=$discriminator;get_valid_values_discriminator=$discriminator;group_count=$groups.Count;channel_count=$channels.Count;module_count=$modules.Count;controller_count=$controllers.Count;unknown_node_count=$unknown.Count;groups=$groups.ToArray();channels=$channels.ToArray();modules=$modules.ToArray();controllers=$controllers.ToArray();definitions=@($ctx.Definitions.Values|Sort-Object pointer);edges=$edges.ToArray();unknown_nodes=$unknown.ToArray();io_inventory_complete=($configuredIO.initialized -and $runtimeIO.initialized -and $configuredGroups.initialized -and $runtimeGroups.initialized);io_devices=@($configuredIO,$runtimeIO);socket_groups=@($configuredGroups,$runtimeGroups);ports=$ports;output_socket_encoding='raw uint32 entries; packed route semantics pending'}
}

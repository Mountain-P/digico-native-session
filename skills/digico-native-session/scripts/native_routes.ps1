# Semantic physical routing gates. Functions only; no socket/UI/memory writes.
# Requires native_inventory.ps1 and native_controls.ps1 in the verified runner.
function Get-NativeRouteProfile([string]$Kind,[int]$InsertIndex=0){
    switch($Kind){
        'main'{return @{Kind='main';Module=1;Parameter=0;Band=0;VmtRva=0x81AA44;CallbackRva=0x826848;DefinitionRva=0x8D16C0;ControllerOffset=0x54;FieldOffset=0xB2;FieldName='input_route';Direction='input'}}
        'alt'{return @{Kind='alt';Module=1;Parameter=0;Band=1;VmtRva=0x81AA44;CallbackRva=0x826848;DefinitionRva=0x8D1BF0;ControllerOffset=0x58;FieldOffset=0xB6;FieldName='alt_input_route';Direction='input'}}
        'key'{return @{Kind='key';Module=5;Parameter=0x16;Band=0;VmtRva=0x81C8C0;CallbackRva=0x82D3DC;DefinitionRva=0x8D5A0D;ControllerOffset=0x64;FieldOffset=0xBA;FieldName='key_route';Direction='input'}}
        'mustard_key'{return @{Kind='mustard_key';Module=0x1E;Parameter=0x16;Band=0;VmtRva=0x75FA28;CallbackRva=0x762288;DefinitionRva=0x8D5A0D;ControllerOffset=0x5C;FieldOffset=$null;FieldName=$null;Direction='input'}}
        'insert_return'{if($InsertIndex-notin @(0,1)){throw 'Insert index must be 0 or 1.'};return @{Kind='insert_return';Module=8;Parameter=0;Band=$InsertIndex;VmtRva=0x81B570;CallbackRva=0x828C68;DefinitionRva=0x8D2D1B;ControllerOffset=0x5C;FieldOffset=$null;FieldName=$null;Direction='input'}}
        'insert_send'{if($InsertIndex-notin @(0,1)){throw 'Insert index must be 0 or 1.'};return @{Kind='insert_send';Module=8;Parameter=0xFE;Band=$InsertIndex;VmtRva=0x81B570;CallbackRva=0x828C68;DefinitionRva=0x8D2CE1;ControllerOffset=0x58;FieldOffset=$null;FieldName=$null;Direction='output'}}
        'output'{return @{Kind='output';Module=14;Parameter=0xFE;Band=0;VmtRva=0x81A3CC;CallbackRva=0x824F9C;DefinitionRva=0x8D738F;ControllerOffset=0x50;FieldOffset=$null;FieldName=$null;Direction='output'}}
        default{throw 'Unsupported semantic route kind.'}
    }
}

function Assert-NativeRouteTreeNode([long]$Pointer){
    $node=$Pointer
    for($depth=0;$depth-lt 8;$depth++){
        $parent=[long]$reader.U32($node+0x24)
        if($parent-eq 0){throw 'Physical routing endpoint has no console owner chain.'}
        Assert-NativeChild $parent $node
        $ctx=New-NativeControlContext;$type=Get-NativeInventoryType $ctx $parent
        if($type.kind-eq 'console'){if($parent-ne $reader.U32($base+0x1279CFC)){throw 'Physical endpoint belongs to another console.'};return}
        $node=$parent
    }
    throw 'Physical routing endpoint owner depth exceeded.'
}

function Assert-NativeRouteEndpoint($Endpoint,[string]$Direction='input'){
    if($Direction-notin @('input','output')){throw 'Unknown physical routing endpoint direction.'}
    $portIndex=[int]$Endpoint.port_index;$socket=[int]$Endpoint.socket_index
    if($portIndex-lt 0-or $portIndex-gt 48-or $socket-lt 0-or $socket-gt 255){throw 'Invalid physical endpoint index.'}
    $groupBase=0x1C;$expectedVmt=0x759440;if($Direction-eq 'output'){$groupBase=0x4E;$expectedVmt=0x75A0E0}
    $channelId=(($groupBase+$portIndex)-shl 8)-bor $socket
    $channel=[long]$Endpoint.channel_pointer;$module=[long]$Endpoint.source_module_pointer
    if($channelId-ne [int]$Endpoint.source_channel_id-or $reader.U16($channel+0x30)-ne $channelId){throw 'Physical source channel identity changed.'}
    $ctx=New-NativeControlContext;$channelType=Get-NativeInventoryType $ctx $channel;$moduleType=Get-NativeInventoryType $ctx $module
    if($channelType.kind-ne 'channel'-or $moduleType.vmt_rva-ne $expectedVmt-or $reader.U32($module+0x24)-ne $channel-or $reader.I32($module+0x4A)-ne 1){throw 'Physical socket-module binding changed.'}
    if((Hex ($reader.Read($module+0x30,5)))-cne $Endpoint.source_module_id_hex){throw 'Physical source module ID changed.'}
    Assert-NativeChild $channel $module;Assert-NativeRouteTreeNode $channel
    if($Direction-eq 'input'){
        $listen=[long]$reader.U32($module+0x7C)
        if($listen-eq 0-or $listen-ne [long]$Endpoint.listen_to_copy_control_pointer-or $reader.U32($listen+0x44)-ne $module){throw 'Physical source copy-listen controller binding differs.'}
        $next=$reader.Read($listen+0x1C,4)
        if((Hex $next)-cne $Endpoint.listen_to_copy_next_raw_hex-or [BitConverter]::ToSingle($next,0)-ne 0){throw 'Physical source uses its copy route; refusing an ambiguous physical source.'}
    }else{
        $routeControl=[long]$reader.U32($module+0x54)
        if($reader.U8($module+0x83)-eq 0-or $routeControl-eq 0-or $routeControl-ne [long]$Endpoint.route_control_pointer-or $reader.U32($routeControl+0x44)-ne $module){throw 'Physical output is inactive or its source controller changed.'}
        $lib=[long]$reader.U32($routeControl+9)
        if($reader.U8($lib)-ne 2-or $reader.U8($routeControl+4)-ne 0-or (Hex ($reader.Read($routeControl+0x2C,4)))-cne $Endpoint.current_source_raw_hex){throw 'Physical output source value or datatype changed.'}
    }
    $portArray=[long]$reader.U32($base+0x8E8E9C);$record=$portArray+0xD2*$portIndex
    if($record-ne [long]$Endpoint.port_record_pointer-or (Hex ($reader.Read($record,0xD2)))-cne $Endpoint.port_record_raw_hex){throw 'Native port-record binding changed.'}
    if($reader.U8($record+0xD0)-ne [int]$Endpoint.port_type-or $reader.U8($record+0xD1)-ne [int]$Endpoint.port_number){throw 'Native physical port key differs.'}
    $io=[long]$reader.U32($base+0x911110);$device=[long]$Endpoint.configured_device_pointer
    if($io-eq 0-or $reader.U32($io)-ne ($base+0x2F85BC)-or $reader.U32($device)-ne ($base+0x2F7C58)){throw 'Configured I/O table/device class differs.'}
    Assert-NativeChild $io $device
    if($reader.U8($device+0xE)-ne [int]$Endpoint.port_number-or $reader.U8($device+0xF)-ne [int]$Endpoint.port_type){throw 'Configured I/O device no longer matches the port.'}
    $countOffset=4;$arrayOffset=0x19;if($Direction-eq 'output'){$countOffset=8;$arrayOffset=0x1D}
    $count=$reader.I32($device+$countOffset);$types=[long]$reader.U32($device+$arrayOffset)
    if($count-lt 0-or $count-gt 65536-or $socket-ge $count-or $types-eq 0){throw 'Physical source socket is outside the configured input array.'}
    $socketType=[int]$reader.U8($types+$socket)
    if($socketType-in @(0,0x1B)-or $socketType-ne [int]$Endpoint.socket_type){throw 'Physical source socket is absent or its type changed.'}
    return [uint32]($channelId-shl 16)
}

function Read-NativeRouteBinding($Step){
    $kind='main';if($Step.PSObject.Properties['route_kind']){$kind=[string]$Step.route_kind}
    $insertIndex=0;if($Step.PSObject.Properties['insert_index']){$insertIndex=[int]$Step.insert_index}
    $profile=Get-NativeRouteProfile $kind $insertIndex
    $bound=Read-NativeControlBinding $Step
    $id=ConvertFrom-NativeControlHex $Step.id 5
    $channelId=[int]$Step.channel_id
    if($channelId-lt 0x100-or $channelId-gt 0x1FF-or $id[0]-ne $profile.Parameter-or $id[1]-ne $profile.Module-or $id[2]-ne $profile.Band-or $id[3]-ne ($channelId-band 255)-or $id[4]-ne 1){throw 'Route kind/controller/channel identity differs.'}
    if($bound.raw_type-ne 2-or $bound.definition_pointer-ne ($base+$profile.DefinitionRva)-or [long]$Step.control_contract.module_vmt_rva-ne $profile.VmtRva-or [long]$Step.control_contract.process_output_rva-ne $profile.CallbackRva){throw 'Native route definition or code binding differs.'}
    if($reader.U32($bound.owner_pointer+$profile.ControllerOffset)-ne $bound.pointer){throw 'Owner does not reference this semantic route controller.'}
    $channel=[long]$reader.U32($bound.owner_pointer+0x24)
    if($reader.U16($channel+0x30)-ne $channelId-or $reader.U8($channel+0x55)-ne 1-or $reader.I32($bound.owner_pointer+0x4A)-ne 1){throw 'Routing currently requires the observed mono Input channel.'}
    $routeRaw=$bound.output_raw
    if($null-ne $profile.FieldOffset){$routeRaw=$reader.Read($channel+$profile.FieldOffset,4)}
    $route=[BitConverter]::ToInt32($routeRaw,0)
    return [pscustomobject]@{control=$bound;profile=$profile;channel_pointer=$channel;route=$route;route_raw=$routeRaw}
}

function Read-NativeRouteOutputSockets([long]$Channel){
    $list=[long]$reader.U32($Channel+0xF1)
    if($list-eq 0){throw 'Output socket list is missing.'}
    $ctx=New-NativeControlContext;$type=Get-NativeInventoryType $ctx $list
    if(-not ($type.ancestors-contains ($base+0x47660))){throw 'Output socket list has an unexpected class.'}
    $n=$reader.I32($list+8);$a=[long]$reader.U32($list+4)
    if($n-lt 0-or $n-gt 384-or ($n-gt 0-and $a-eq 0)){throw 'Invalid output socket bounds.'}
    $raw=[byte[]]@();if($n-gt 0){$raw=$reader.Read($a,4*$n)};$values=[Collections.Generic.List[long]]::new()
    for($i=0;$i-lt $n;$i++){$v=[long][BitConverter]::ToUInt32($raw,4*$i);if($values.Contains($v)){throw 'Duplicate native output-socket entry.'};$values.Add($v)}
    if($reader.U32($Channel+0xF1)-ne $list-or $reader.U32($list+4)-ne $a-or $reader.I32($list+8)-ne $n){throw 'Output socket list changed while reading.'}
    return ,@($values.ToArray()|Sort-Object)
}

function Test-NativeRouteOutputSet([long]$Channel,$Expected){
    $actual=Read-NativeRouteOutputSockets $Channel
    $wanted=@($Expected|ForEach-Object {[long]$_}|Sort-Object)
    return (($actual-join ',')-ceq ($wanted-join ','))
}

function Assert-NativeRouteValue($Binding,$Step){
    $body=ConvertFrom-NativeControlHex $Step.control_contract.payload_hex 4
    $requested=[BitConverter]::ToInt32($body,0);$expected=ConvertFrom-NativeControlHex $Step.control_contract.expected_payload_hex 4
    if((Hex $Binding.control.value_raw)-cne (Hex $expected)-or (Hex $Binding.control.next_raw)-cne (Hex $expected)-or (Hex $Binding.control.output_raw)-cne (Hex $expected)){throw 'Native route controller compare-before-write state changed.'}
    if($Binding.profile.Kind-ne 'output'-and ((Hex $Binding.route_raw)-cne (Hex $expected)-or $Binding.route-ne [int]$Step.expected_input_route)){throw 'Native semantic route compare-before-write state changed.'}
    $resolved=[long]0
    if($Binding.profile.Kind-eq 'output'){
        $resolved=[long](Assert-NativeRouteEndpoint $Step.destination 'output')
        if(-not (Test-NativeRouteOutputSet $Binding.channel_pointer $Step.route_contract.previous_output_sockets)){throw 'Source output socket set changed before write.'}
        $actualSockets=Read-NativeRouteOutputSockets $Binding.channel_pointer
        $present=($actualSockets-contains $resolved);$selfSource=[uint32]([int]$Step.channel_id-shl 16)
        $targetPointer=[long]$Step.destination.route_control_pointer;$targetSource=$reader.U32($targetPointer+0x2C)
        if($present-ne ($targetSource-eq $selfSource)-or ([bool]$Step.connected-and $targetSource-ne 0-and $targetSource-ne $selfSource)){throw 'Output socket ownership is inconsistent or another source occupies it.'}
        $post=[Collections.Generic.List[long]]::new();foreach($v in $actualSockets){if([bool]$Step.connected-or $v-ne $resolved){$post.Add($v)}}
        if([bool]$Step.connected-and -not $present){if($post.Count-ge 384){throw 'Native output socket capacity reached.'};$post.Add($resolved)}
        if((@($post.ToArray()|Sort-Object)-join ',')-cne (@($Step.route_contract.expected_output_sockets|ForEach-Object {[long]$_}|Sort-Object)-join ',')){throw 'Output socket postcondition is not the requested one-socket change.'}
        $postSource=$targetSource;if([bool]$Step.connected){$postSource=$selfSource}elseif($present){$postSource=0}
        if((Hex ([BitConverter]::GetBytes([uint32]$postSource)))-cne $Step.route_contract.target_source_raw_hex){throw 'Output source postcondition differs from native ownership semantics.'}
        if(-not [bool]$Step.connected){$resolved=-$resolved}
    }else{
        if($null-ne $Step.source){$resolved=[long](Assert-NativeRouteEndpoint $Step.source $Binding.profile.Direction)}
        if($Binding.profile.Kind-eq 'insert_send'){
            $selfSource=[uint32](([int]$Step.channel_id-shl 16)-bor ([int]$Binding.profile.Band-shl 12)-bor 8)
            if($null-ne $Step.source){$p=[long]$Step.source.route_control_pointer;$targetSource=$reader.U32($p+0x2C);if($targetSource-ne 0-and $targetSource-ne $selfSource){throw 'Insert-send output is occupied by another source.'}}
            if($null-ne $Step.route_contract.previous_endpoint){[void](Assert-NativeRouteEndpoint $Step.route_contract.previous_endpoint 'output');$p=[long]$Step.route_contract.previous_endpoint.route_control_pointer;if($reader.U32($p+0x2C)-ne $selfSource){throw 'Previous insert-send target is not owned by this insert.'}}
            if((Hex ([BitConverter]::GetBytes($selfSource)))-cne $Step.route_contract.target_source_raw_hex){throw 'Insert-send source route suffix differs from the owner module/band.'}
        }
    }
    if($resolved-ne [long]$requested-or [long]$Step.control_contract.value-ne [long]$requested){throw 'Packed route differs from the independently resolved physical endpoint.'}
    return ,$body
}

function Wait-NativeRouteWrite($Step){
    $timer=[Diagnostics.Stopwatch]::StartNew();$stable=0
    while($timer.ElapsedMilliseconds-lt 10000){
        Pump 75;$binding=Read-NativeRouteBinding $Step
        $want=[string]$Step.control_contract.payload_hex
        $matches=((Hex $binding.control.value_raw)-ceq $want-and (Hex $binding.control.output_raw)-ceq $want)
        # FE is an incremental output command. The target has been observed to
        # clear the remove command after applying it. Only this exact removal
        # profile accepts that settled zero; semantic set/socket checks below
        # remain mandatory and other route controllers keep exact echo checks.
        if($binding.profile.Kind-eq 'output'-and -not [bool]$Step.connected){
            $negative=[BitConverter]::ToInt32((ConvertFrom-NativeControlHex $want 4),0)-lt 0
            $echoed=((Hex $binding.control.value_raw)-ceq $want-and (Hex $binding.control.next_raw)-ceq $want-and (Hex $binding.control.output_raw)-ceq $want)
            $cleared=((Hex $binding.control.value_raw)-ceq '00000000'-and (Hex $binding.control.next_raw)-ceq '00000000'-and (Hex $binding.control.output_raw)-ceq '00000000')
            $matches=($negative-and ($echoed-or $cleared))
        }
        if($null-ne $binding.profile.FieldOffset){$matches=$matches-and ((Hex $binding.route_raw)-ceq $want)}
        if($binding.profile.Kind-eq 'output'){
            $matches=$matches-and (Test-NativeRouteOutputSet $binding.channel_pointer $Step.route_contract.expected_output_sockets)
            $routeControl=[long]$reader.U32([long]$Step.destination.source_module_pointer+0x54)
            $matches=$matches-and ($routeControl-eq [long]$Step.destination.route_control_pointer)-and ((Hex ($reader.Read($routeControl+0x2C,4)))-ceq $Step.route_contract.target_source_raw_hex)
        }
        if($binding.profile.Kind-eq 'insert_send'){
            if($null-ne $Step.source){$p=[long]$Step.source.route_control_pointer;$matches=$matches-and ((Hex ($reader.Read($p+0x2C,4)))-ceq $Step.route_contract.target_source_raw_hex)}
            $old=$Step.route_contract.previous_endpoint
            if($null-ne $old-and ($null-eq $Step.source-or [long]$old.route_uint32-ne [long]$Step.source.route_uint32)){$p=[long]$old.route_control_pointer;$matches=$matches-and ((Hex ($reader.Read($p+0x2C,4)))-ceq '00000000')}
        }
        if($matches){$stable++}else{$stable=0}
        if($stable-ge 2){return $binding}
    }
    throw 'Native route controller and semantic channel route did not both reach the requested value.'
}

function Read-NativeGroupAssignmentBinding($Step){
    $bound=Read-NativeControlBinding $Step;$id=ConvertFrom-NativeControlHex $Step.id 5
    $channelId=[int]$Step.channel_id;$groupId=[int]$Step.group_channel_id;$index=$groupId-band 255;$contract=$Step.group_contract
    if($channelId-lt 0x100-or $channelId-gt 0x1FF-or $groupId-lt 0x600-or $groupId-gt 0x6FF-or $id[0]-ne 9-or $id[1]-ne 3-or $id[2]-ne $index-or $id[3]-ne ($channelId-band 255)-or $id[4]-ne 1){throw 'Group assignment packed identity differs.'}
    if($bound.raw_type-ne 0-or [long]$Step.control_contract.module_vmt_rva-ne 0x81D950-or [long]$Step.control_contract.process_output_rva-ne 0x83042C-or $bound.definition_pointer-ne [long]$Step.control_contract.definition_pointer-or $bound.definition[5]-ne $index){throw 'Group assignment code/definition binding differs.'}
    $source=[long]$reader.U32($bound.owner_pointer+0x24)
    if($source-ne [long]$contract.source_channel_pointer-or $reader.U16($source+0x30)-ne $channelId-or $reader.U8($source+0x55)-ne 1-or $reader.I32($bound.owner_pointer+0x4A)-ne 1){throw 'Group assignment source is not the observed mono Input.'}
    $target=[long]$contract.target_channel_pointer;$fader=[long]$contract.target_fader_pointer
    $ctx=New-NativeControlContext;$type=Get-NativeInventoryType $ctx $target
    $mode=[int]$reader.U8($target+0x55)
    if($type.kind-ne 'channel'-or $reader.U16($target+0x30)-ne $groupId-or $mode-notin @(1,2)-or $mode-ne [int]$contract.target_stereo_mode-or $reader.U16($target+0x62)-ne [int]$contract.target_buss_id){throw 'Target Group Output identity/layout differs.'}
    if($reader.U32($target+0x1AE)-ne $fader-or $reader.U32($fader)-ne ($base+0x81E510)-or $reader.U32($fader+0x24)-ne $target-or $reader.I32($fader+0x4A)-ne $mode-or (Hex ($reader.Read($fader+0x30,5)))-cne $contract.target_fader_id_hex){throw 'Target Group Fader code/owner/signals binding differs.'}
    Assert-NativeChild $target $fader;Assert-NativeRouteTreeNode $target
    $bits=$reader.Read($source+0xBE,32)
    return [pscustomobject]@{control=$bound;source_channel_pointer=$source;target_channel_pointer=$target;bit_index=$index;buss_routes=$bits}
}

function Assert-NativeGroupAssignmentValue($Binding,$Step){
    if($Step.enabled-isnot [bool]){throw 'Group assignment enabled must be a boolean.'}
    $body=ConvertFrom-NativeControlHex $Step.control_contract.payload_hex 4
    $want='00000000';if($Step.enabled){$want='0000803f'}
    if((Hex $body)-cne $want-or [double]$Step.control_contract.value-ne [int][bool]$Step.enabled){throw 'Group assignment must use native float32 zero or one.'}
    $expected=[string]$Step.control_contract.expected_payload_hex
    if($expected-notin @('00000000','0000803f')-or (Hex $Binding.control.value_raw)-cne $expected-or (Hex $Binding.control.next_raw)-cne $expected-or (Hex $Binding.control.output_raw)-cne $expected-or (Hex $Binding.buss_routes)-cne $Step.group_contract.expected_buss_routes_hex){throw 'Group assignment compare-before-write state changed.'}
    $index=[int]$Binding.bit_index;$byteIndex=[int][math]::Floor($index/8);$mask=1-shl ($index-band 7)
    $beforeSet=(($Binding.buss_routes[$byteIndex]-band $mask)-ne 0)
    if($beforeSet-ne ($expected-ceq '0000803f')){throw 'Group controller and BussRoutes bit disagree.'}
    $post=[byte[]]$Binding.buss_routes.Clone()
    if($Step.enabled){$post[$byteIndex]=$post[$byteIndex]-bor $mask}else{$post[$byteIndex]=$post[$byteIndex]-band (255-bxor $mask)}
    if((Hex $post)-cne $Step.group_contract.post_buss_routes_hex){throw 'Group assignment postcondition changes more than the requested bit.'}
    return ,$body
}

function Wait-NativeGroupAssignment($Step){
    $timer=[Diagnostics.Stopwatch]::StartNew();$stable=0
    while($timer.ElapsedMilliseconds-lt 10000){
        Pump 75;$binding=Read-NativeGroupAssignmentBinding $Step;$want=[string]$Step.control_contract.payload_hex
        if((Hex $binding.control.value_raw)-ceq $want-and (Hex $binding.control.output_raw)-ceq $want-and (Hex $binding.buss_routes)-ceq $Step.group_contract.post_buss_routes_hex){$stable++}else{$stable=0}
        if($stable-ge 2){return $binding}
    }
    throw 'Native Group controller and exact BussRoutes bitset did not reach the requested assignment.'
}

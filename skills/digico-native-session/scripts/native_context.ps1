# Exact V2126 context gates. Dot-source after native_controls.ps1 and the verified
# reader/base context. This only reads memory; it does not send/call/write target.

function ConvertFrom-NativeContextHex([object]$Value,[int]$Length,[string]$Label) {
    if($Value -isnot [string] -or $Value -cnotmatch ('^[0-9a-fA-F]{'+(2*$Length)+'}$')){throw "Invalid native context $Label."}
    $bytes=New-Object byte[] $Length
    for($i=0;$i -lt $Length;$i++){$bytes[$i]=[Convert]::ToByte($Value.Substring(2*$i,2),16)}
    return ,$bytes
}
function ConvertTo-NativeContextHex([byte[]]$Bytes) {
    return ([BitConverter]::ToString($Bytes)).Replace('-','').ToLowerInvariant()
}
function Get-NativeContextProperty($Value,[string]$Name) {
    if($null -ne $Value -and $null -ne $Value.PSObject.Properties[$Name]){return $Value.$Name}
    return $null
}
function Get-NativeContextFloat([uint32]$Bits) {
    return [double][BitConverter]::ToSingle([BitConverter]::GetBytes($Bits),0)
}
function Get-NativeSemanticLimits([string]$Profile,[int]$Parameter,[int]$Mode=-1) {
    $profileEntry=$script:NativeBuildProfile.semantic_limits.PSObject.Properties[$Profile]
    if($null-eq $profileEntry){throw 'Unknown native semantic limit profile.'}
    $parameterEntry=$profileEntry.Value.PSObject.Properties[$Parameter.ToString()]
    if($null-eq $parameterEntry){throw 'Unknown native semantic parameter.'}
    $modes=$parameterEntry.Value;$key=$Mode.ToString()
    if($null-ne $modes.PSObject.Properties['default']){$key='default'}
    $pair=$modes.PSObject.Properties[$key]
    if($null-eq $pair-or @($pair.Value).Count-ne 2){throw 'Unknown native semantic context mode.'}
    $low=[uint32]$pair.Value[0];$high=[uint32]$pair.Value[1]
    return [pscustomobject]@{minimum=(Get-NativeContextFloat $low);maximum=(Get-NativeContextFloat $high);allowed=$null;minimum_raw_hex=(ConvertTo-NativeContextHex ([BitConverter]::GetBytes($low)));maximum_raw_hex=(ConvertTo-NativeContextHex ([BitConverter]::GetBytes($high)));source='native_callback_context'}
}

function Assert-NativeControlContext {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)]$Binding,[Parameter(Mandatory=$true)]$Step)
    $id=ConvertFrom-NativeContextHex $Step.id 5 'control ID'
    $entry=$null;$modeBand=0;$modeSlot=0
    if($id[1]-eq 4-and $id[0]-eq 6-and $id[2]-le 7){
        $entry=$script:NativeBuildProfile.contexts.eq_q;$modeBand=[int]$id[2]
        $modeSlot=[int]$entry.mode_slot_base+[int]$entry.mode_slot_stride*$modeBand
    }elseif($id[1]-eq 5-and $id[0]-in @(0x11,0x12)){
        if($id[2]-in @(0,1,2)){$entry=$script:NativeBuildProfile.contexts.compressor}
        elseif($id[2]-eq 3){$entry=$script:NativeBuildProfile.contexts.gate}
        else{throw 'Unsupported Dynamics time-control context band.'}
        $modeBand=[int]$entry.mode_band;$modeSlot=[int]$entry.mode_slot
    }elseif($id[1]-eq 9-and $id[0]-eq 8){$entry=$script:NativeBuildProfile.contexts.delay}
    else{return $null}
    $profile=[string]$entry.profile;$vmtRva=[long]$entry.vmt;$callbackRva=[long]$entry.callback
    $resendingOffset=0;$timerOffset=0;$modeParameter=0;$modeModule=0
    if($entry.PSObject.Properties['resending_offset']){$resendingOffset=[int]$entry.resending_offset}
    if($entry.PSObject.Properties['timer_offset']){$timerOffset=[int]$entry.timer_offset}
    if($entry.PSObject.Properties['mode_parameter']){$modeParameter=[int]$entry.mode_parameter;$modeModule=[int]$entry.mode_module}
    if($Binding.raw_type -ne 1){throw 'Native time context requires float32 datatype1.'}
    if($null -eq $reader -or [long]$base -le 0){throw 'Native context requires verified reader/base.'}
    $owner=[long]$Binding.owner_pointer
    if($owner -le 0 -or $owner -gt 0xffffffffL){throw 'Invalid native semantic owner.'}
    $vmt=[long]$reader.U32($owner)
    if($vmt -ne ($base+$vmtRva) -or [long]$reader.U32($vmt+$script:NativeBuildProfile.layouts.module.process_output_vmt_slot) -ne ($base+$callbackRva)){throw 'Native semantic callback profile changed.'}
    $context=Get-NativeContextProperty $Step.control_contract 'semantic_context'
    if($null -eq $context -or $context.profile -cne $profile -or [long]$context.owner_module_pointer -ne $owner){throw 'Missing or changed compiled semantic context.'}
    $mode=-1
    if($profile -ceq $script:NativeBuildProfile.semantic_profiles.delay_v2126){
        if([long]$reader.U32($owner+$script:NativeBuildProfile.contexts.delay.controller_slot) -ne [long]$Binding.pointer){throw 'Delay main controller slot differs from the bound target.'}
    }else{
        if($resendingOffset -ne 0 -and ([int]$context.resending -ne 0 -or $reader.U8($owner+$resendingOffset) -ne 0)){throw 'Native owner is resending; do not send a time value.'}
        if($timerOffset -ne 0 -and ([int]$context.mode_changed_timer_count -ne 0 -or $reader.U8($owner+$timerOffset) -ne 0)){throw 'Native gate mode-change timer is active; time values would be ignored.'}
        $modePointer=[long]$reader.U32($owner+$modeSlot)
        if($modePointer -le 0 -or $modePointer -ne [long]$context.mode_controller_pointer){throw 'Native owner mode-controller slot changed.'}
        $raw=$reader.Read($modePointer,$script:NativeBuildProfile.layouts.controller.size)
        if([long][BitConverter]::ToUInt32($raw,0) -ne ($base+$script:NativeBuildProfile.addresses.hex_8680fc) -or [long][BitConverter]::ToUInt32($raw,$script:NativeBuildProfile.layouts.controller.parent) -ne $owner){throw 'Native mode-controller VMT/owner changed.'}
        $modeId=[byte[]]$raw[4..8]
        if($modeId[0] -ne $modeParameter -or $modeId[1] -ne $modeModule -or $modeId[2] -ne $modeBand -or $modeId[3] -ne $id[3] -or $modeId[4] -ne $id[4]){throw 'Native mode-controller ID does not match the exact semantic profile.'}
        $expectedId=ConvertFrom-NativeContextHex $context.mode_id_hex 5 'mode ID'
        if((ConvertTo-NativeContextHex $modeId) -cne (ConvertTo-NativeContextHex $expectedId)){throw 'Copied native mode ID changed.'}
        $definitionPointer=[long][BitConverter]::ToUInt32($raw,$script:NativeBuildProfile.layouts.controller.definition)
        if($definitionPointer -le 0 -or $definitionPointer -ne [long]$context.mode_definition_pointer){throw 'Native mode definition pointer changed.'}
        $definition=$reader.Read($definitionPointer,$script:NativeBuildProfile.layouts.definition.size)
        $expectedDefinition=ConvertFrom-NativeContextHex $context.mode_definition_raw_hex $script:NativeBuildProfile.layouts.definition.size 'mode definition'
        if((ConvertTo-NativeContextHex $definition) -cne (ConvertTo-NativeContextHex $expectedDefinition) -or $definition[$script:NativeBuildProfile.layouts.definition.data_type] -ne 0 -or $definition[$script:NativeBuildProfile.layouts.definition.band] -ne $modeBand -or $definition[$script:NativeBuildProfile.layouts.definition.parameter] -ne $modeParameter){throw 'Native mode definition changed or is not the discrete discriminator.'}
        # Verify that the exact native mode pointer still belongs to this module.
        $items=[long]$reader.U32($owner+4);$count=$reader.I32($owner+8)
        if($count -lt 1 -or $count -gt 65536 -or $items -eq 0){throw 'Invalid native semantic owner membership.'}
        $members=$reader.Read($items,4*$count);$matches=0
        for($i=0;$i -lt $count;$i++){if([long][BitConverter]::ToUInt32($members,4*$i) -eq $modePointer){$matches++}}
        if($matches -ne 1){throw 'Native mode pointer is not a unique owner member.'}
        $output=[byte[]]$raw[($script:NativeBuildProfile.layouts.controller.output)..($script:NativeBuildProfile.layouts.controller.output+3)]
        $expectedOutput=ConvertFrom-NativeContextHex $context.mode_output_raw_hex 4 'mode output'
        if((ConvertTo-NativeContextHex $output) -cne (ConvertTo-NativeContextHex $expectedOutput)){throw 'Native mode output changed after planning; take a new inventory.'}
        $modeValue=[BitConverter]::ToSingle($output,0)
        $supportedModes=@(0.0,1.0,2.0);if($profile -ceq $script:NativeBuildProfile.semantic_profiles.eq_q_v2126){$supportedModes=@(0.0,1.0,2.0,3.0,4.0)}
        if([single]::IsNaN($modeValue) -or [single]::IsInfinity($modeValue) -or $modeValue -notin $supportedModes){throw 'Native mode output is outside the typed discrete domain.'}
        $mode=[int]$modeValue
        if(($resendingOffset -ne 0 -and $reader.U8($owner+$resendingOffset) -ne 0) -or [long]$reader.U32($owner+$modeSlot) -ne $modePointer -or (ConvertTo-NativeContextHex ($reader.Read($modePointer+$script:NativeBuildProfile.layouts.controller.output,4))) -cne (ConvertTo-NativeContextHex $output)){throw 'Native context changed during the fresh read.'}
        if($timerOffset -ne 0 -and $reader.U8($owner+$timerOffset) -ne 0){throw 'Native gate mode-change timer started during context read.'}
    }
    $domain=Get-NativeSemanticLimits $profile ([int]$id[0]) $mode
    $claimed=Get-NativeContextProperty $context 'limits'
    if($null -eq $claimed -or (ConvertTo-NativeContextHex (ConvertFrom-NativeContextHex $claimed.minimum_raw_hex 4 'minimum')) -cne $domain.minimum_raw_hex -or (ConvertTo-NativeContextHex (ConvertFrom-NativeContextHex $claimed.maximum_raw_hex 4 'maximum')) -cne $domain.maximum_raw_hex){throw 'Compiled semantic limits differ from the independently read native profile.'}
    $payload=ConvertFrom-NativeContextHex $Step.control_contract.payload_hex 4 'payload'
    $value=[BitConverter]::ToSingle($payload,0)
    if([single]::IsNaN($value) -or [single]::IsInfinity($value) -or $value -lt $domain.minimum -or $value -gt $domain.maximum){throw 'Requested float32 is outside fresh native semantic limits; no packet sent.'}
    # Caller uses this domain INSTEAD OF seed bounds for these known callbacks.
    return $domain
}

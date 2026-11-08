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
    $low=[uint32]0;$high=[uint32]0
    if($Profile -ceq 'delay_v2126' -and $Parameter -eq 8){$low=0;$high=0x3FA66666}
    elseif($Profile -ceq 'eq_q_v2126' -and $Parameter -eq 6){
        if($Mode -notin @(0,1,2,3,4)){throw 'Unknown EQ shape context.'}
        $low=0x3DCCCCCD;$high=0x41A00000;if($Mode -in @(3,4)){$high=0x3F59999A}
    }
    elseif($Profile -ceq 'compressor_v2126' -and $Parameter -in @(0x11,0x12)){
        if($Mode -notin @(0,1,2)){throw 'Unknown compressor context mode.'}
        if($Mode -eq 2){if($Parameter -eq 0x11){$low=0x37A7C5AC;$high=0x3CA3D70A}else{$low=0x3A83126F;$high=0x3DCCCCCD}}
        else{if($Parameter -eq 0x11){$low=0x3A03126F;$high=0x3DCCCCCD}else{$low=0x3C23D70A;$high=0x41200000}}
    }elseif($Profile -ceq 'gate_v2126' -and $Parameter -in @(0x11,0x12)){
        if($Mode -notin @(0,1,2)){throw 'Unknown gate context mode.'}
        if($Mode -eq 2){if($Parameter -eq 0x11){$low=0x3A03126F;$high=0x3DCCCCCD}else{$low=0x3C23D70A;$high=0x41200000}}
        else{if($Parameter -eq 0x11){$low=0x3851B717;$high=0x3DCCCCCD}else{$low=0x3BA3D70A;$high=0x40A00000}}
    }else{throw 'Unknown native semantic limit profile.'}
    return [pscustomobject]@{minimum=(Get-NativeContextFloat $low);maximum=(Get-NativeContextFloat $high);allowed=$null;minimum_raw_hex=(ConvertTo-NativeContextHex ([BitConverter]::GetBytes([uint32]$low)));maximum_raw_hex=(ConvertTo-NativeContextHex ([BitConverter]::GetBytes([uint32]$high)));source='native_callback_context'}
}

function Assert-NativeControlContext {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)]$Binding,[Parameter(Mandatory=$true)]$Step)
    $id=ConvertFrom-NativeContextHex $Step.id 5 'control ID'
    $profile=$null;$vmtRva=0;$callbackRva=0;$modeSlot=0;$resendingOffset=0;$modeParameter=0;$modeModule=5;$modeBand=0;$timerOffset=0
    if($id[1] -eq 4 -and $id[0] -eq 6 -and $id[2] -le 7){
        $profile='eq_q_v2126';$vmtRva=0x81BD7C;$callbackRva=0x82A938;$modeSlot=0x58+24*[int]$id[2];$modeParameter=4;$modeModule=4;$modeBand=[int]$id[2]
    }elseif($id[1] -eq 5 -and $id[0] -in @(0x11,0x12)){
        if($id[2] -in @(0,1,2)){$profile='compressor_v2126';$vmtRva=0x81C240;$callbackRva=0x82BAC0;$modeSlot=0x54;$resendingOffset=0xE4;$modeParameter=0x3A;$modeBand=0}
        elseif($id[2] -eq 3){$profile='gate_v2126';$vmtRva=0x81C8C0;$callbackRva=0x82D3DC;$modeSlot=0x90;$resendingOffset=0x9E;$modeParameter=0x0B;$modeBand=3;$timerOffset=0x9D}
        else{throw 'Unsupported Dynamics time-control context band.'}
    }elseif($id[1] -eq 9 -and $id[0] -eq 8){$profile='delay_v2126';$vmtRva=0x81B2F4;$callbackRva=0x828640}
    else{return $null}
    if($Binding.raw_type -ne 1){throw 'Native time context requires float32 datatype1.'}
    if($null -eq $reader -or [long]$base -le 0){throw 'Native context requires verified reader/base.'}
    $owner=[long]$Binding.owner_pointer
    if($owner -le 0 -or $owner -gt 0xffffffffL){throw 'Invalid native semantic owner.'}
    $vmt=[long]$reader.U32($owner)
    if($vmt -ne ($base+$vmtRva) -or [long]$reader.U32($vmt+0x40) -ne ($base+$callbackRva)){throw 'Native semantic callback profile changed.'}
    $context=Get-NativeContextProperty $Step.control_contract 'semantic_context'
    if($null -eq $context -or $context.profile -cne $profile -or [long]$context.owner_module_pointer -ne $owner){throw 'Missing or changed compiled semantic context.'}
    $mode=-1
    if($profile -ceq 'delay_v2126'){
        if([long]$reader.U32($owner+0x54) -ne [long]$Binding.pointer){throw 'Delay main controller slot differs from the bound target.'}
    }else{
        if($resendingOffset -ne 0 -and ([int]$context.resending -ne 0 -or $reader.U8($owner+$resendingOffset) -ne 0)){throw 'Native owner is resending; do not send a time value.'}
        if($timerOffset -ne 0 -and ([int]$context.mode_changed_timer_count -ne 0 -or $reader.U8($owner+$timerOffset) -ne 0)){throw 'Native gate mode-change timer is active; time values would be ignored.'}
        $modePointer=[long]$reader.U32($owner+$modeSlot)
        if($modePointer -le 0 -or $modePointer -ne [long]$context.mode_controller_pointer){throw 'Native owner mode-controller slot changed.'}
        $raw=$reader.Read($modePointer,0x48)
        if([long][BitConverter]::ToUInt32($raw,0) -ne ($base+0x8680FC) -or [long][BitConverter]::ToUInt32($raw,0x44) -ne $owner){throw 'Native mode-controller VMT/owner changed.'}
        $modeId=[byte[]]$raw[4..8]
        if($modeId[0] -ne $modeParameter -or $modeId[1] -ne $modeModule -or $modeId[2] -ne $modeBand -or $modeId[3] -ne $id[3] -or $modeId[4] -ne $id[4]){throw 'Native mode-controller ID does not match the exact semantic profile.'}
        $expectedId=ConvertFrom-NativeContextHex $context.mode_id_hex 5 'mode ID'
        if((ConvertTo-NativeContextHex $modeId) -cne (ConvertTo-NativeContextHex $expectedId)){throw 'Copied native mode ID changed.'}
        $definitionPointer=[long][BitConverter]::ToUInt32($raw,9)
        if($definitionPointer -le 0 -or $definitionPointer -ne [long]$context.mode_definition_pointer){throw 'Native mode definition pointer changed.'}
        $definition=$reader.Read($definitionPointer,58)
        $expectedDefinition=ConvertFrom-NativeContextHex $context.mode_definition_raw_hex 58 'mode definition'
        if((ConvertTo-NativeContextHex $definition) -cne (ConvertTo-NativeContextHex $expectedDefinition) -or $definition[0] -ne 0 -or $definition[5] -ne $modeBand -or $definition[6] -ne $modeParameter){throw 'Native mode definition changed or is not the discrete discriminator.'}
        # Verify that the exact native mode pointer still belongs to this module.
        $items=[long]$reader.U32($owner+4);$count=$reader.I32($owner+8)
        if($count -lt 1 -or $count -gt 65536 -or $items -eq 0){throw 'Invalid native semantic owner membership.'}
        $members=$reader.Read($items,4*$count);$matches=0
        for($i=0;$i -lt $count;$i++){if([long][BitConverter]::ToUInt32($members,4*$i) -eq $modePointer){$matches++}}
        if($matches -ne 1){throw 'Native mode pointer is not a unique owner member.'}
        $output=[byte[]]$raw[0x2C..0x2F]
        $expectedOutput=ConvertFrom-NativeContextHex $context.mode_output_raw_hex 4 'mode output'
        if((ConvertTo-NativeContextHex $output) -cne (ConvertTo-NativeContextHex $expectedOutput)){throw 'Native mode output changed after planning; take a new inventory.'}
        $modeValue=[BitConverter]::ToSingle($output,0)
        $supportedModes=@(0.0,1.0,2.0);if($profile -ceq 'eq_q_v2126'){$supportedModes=@(0.0,1.0,2.0,3.0,4.0)}
        if([single]::IsNaN($modeValue) -or [single]::IsInfinity($modeValue) -or $modeValue -notin $supportedModes){throw 'Native mode output is outside the typed discrete domain.'}
        $mode=[int]$modeValue
        if(($resendingOffset -ne 0 -and $reader.U8($owner+$resendingOffset) -ne 0) -or [long]$reader.U32($owner+$modeSlot) -ne $modePointer -or (ConvertTo-NativeContextHex ($reader.Read($modePointer+0x2C,4))) -cne (ConvertTo-NativeContextHex $output)){throw 'Native context changed during the fresh read.'}
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

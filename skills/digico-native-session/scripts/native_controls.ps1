# Typed native Cat8 writes. All process access below is read-only.
function ConvertFrom-NativeControlHex([string]$Text,[int]$Length){
    if($Text-notmatch ('\A[0-9a-fA-F]{'+($Length*2)+'}\z')){throw 'Invalid native control hex length.'}
    $b=New-Object byte[] $Length;for($i=0;$i-lt $Length;$i++){$b[$i]=[Convert]::ToByte($Text.Substring(2*$i,2),16)};return ,$b
}
function New-NativeControlContext{
    Assert-NativeBuildFeature 'inventory'
    return @{Reader=$reader;Base=[long]$base;End=([long]$base+$script:NativeBuildProfile.executable.image_size);Ascii=$ascii;Encoding=$nameEncoding;Types=@{};Definitions=@{};IncludeResponseTables=$true;CurrentObject=0L;CurrentId='';ListWitnesses=[Collections.Generic.List[object]]::new();Identities=[Collections.Generic.List[object]]::new()}
}
function Assert-NativeChild([long]$Parent,[long]$Child){
    $n=$reader.I32($Parent+8);$a=[long]$reader.U32($Parent+4)
    if($n-lt 0-or $n-gt 65536-or ($n-gt 0-and $a-eq 0)){throw 'Invalid control-owner list.'}
    $b=$reader.Read($a,4*$n);$found=$false
    for($j=0;$j-lt $n;$j++){if([BitConverter]::ToUInt32($b,4*$j)-eq $Child){$found=$true;break}}
    if(-not $found){throw 'The control owner is no longer a member of the live console tree.'}
}
function Read-NativeControlBinding($Step){
    Assert-NativeBuildContract $Step
    Assert-NativeBuildContract $Step.control_contract
    $contract=$Step.control_contract;$id=ConvertFrom-NativeControlHex $Step.id 5
    if($id[1]-eq 10){throw 'Cue/Automation controls are excluded.'}
    if($id[0]-eq 252){throw 'Channel label colour is a derived status control, not a palette setter.'}
    if($id[1]-eq 9-and $id[0]-eq 0x18){throw 'Total delay is a derived main-plus-fine value.'}
    $pointer=[long]$contract.control_pointer;$owner=[long]$contract.owner_module_pointer
    if($pointer-le 0-or $pointer-gt 0xffffffffL-or $owner-le 0-or $owner-gt 0xffffffffL){throw 'Invalid native control binding pointers.'}
    $ctx=New-NativeControlContext;$type=Get-NativeInventoryType $ctx $pointer
    if($type.kind-ne 'controller'){throw 'Bound object is not a native controller.'}
    $raw=$reader.Read($pointer,$script:NativeBuildProfile.layouts.controller.size)
    if((Hex ([byte[]]$raw[4..8]))-cne $Step.id-or [BitConverter]::ToUInt32($raw,$script:NativeBuildProfile.layouts.controller.parent)-ne $owner){throw 'Native control identity/owner changed.'}
    $ownerType=Get-NativeInventoryType $ctx $owner
    if($ownerType.kind-ne 'module'-or $ownerType.vmt_rva-ne $contract.module_vmt_rva-or $reader.U32($ownerType.vmt+$script:NativeBuildProfile.layouts.module.process_output_vmt_slot)-ne ($base+[long]$contract.process_output_rva)){throw 'Native owner VMT/callback binding changed.'}
    $ownerID=$reader.Read($owner+$script:NativeBuildProfile.layouts.module.id,5)
    if($ownerID[1]-ne $id[1]-or $ownerID[3]-ne $id[3]-or $ownerID[4]-ne $id[4]){throw 'Native owner module ID differs.'}
    Assert-NativeChild $owner $pointer
    $node=$owner
    for($depth=0;$depth-lt 8;$depth++){
        $parent=[long]$reader.U32($node+$script:NativeBuildProfile.layouts.console_list.parent)
        if($parent-eq 0){throw 'Native control has no console owner chain.'}
        Assert-NativeChild $parent $node
        $parentType=Get-NativeInventoryType $ctx $parent
        if($parentType.kind-eq 'console'){
            if($parent-ne $reader.U32($base+$script:NativeBuildProfile.addresses.hex_1279cfc)){throw 'Control belongs to another console.'};break
        }
        $node=$parent
    }
    if($depth-ge 8){throw 'Native control owner depth exceeded.'}
    $definitionPointer=[long][BitConverter]::ToUInt32($raw,$script:NativeBuildProfile.layouts.controller.definition);$definition=$reader.Read($definitionPointer,$script:NativeBuildProfile.layouts.definition.size)
    if((Hex $definition)-cne $contract.definition_raw_hex-or [int]$definition[$script:NativeBuildProfile.layouts.definition.data_type]-ne $contract.raw_type-or $definition[$script:NativeBuildProfile.layouts.definition.parameter]-ne $id[0]){throw 'Native controller definition changed.'}
    if(($definition[$script:NativeBuildProfile.layouts.definition.band]-ne 0-or $id[1]-eq 5)-and $definition[$script:NativeBuildProfile.layouts.definition.band]-ne $id[2]){throw 'Native band binding differs.'}
    return [pscustomobject]@{pointer=$pointer;id=$Step.id;raw_type=[int]$definition[$script:NativeBuildProfile.layouts.definition.data_type];definition=$definition;definition_pointer=$definitionPointer;owner_pointer=$owner;value_raw=([byte[]]$raw[($script:NativeBuildProfile.layouts.controller.value)..($script:NativeBuildProfile.layouts.controller.value+3)]);next_raw=([byte[]]$raw[($script:NativeBuildProfile.layouts.controller.target)..($script:NativeBuildProfile.layouts.controller.target+3)]);output_raw=([byte[]]$raw[($script:NativeBuildProfile.layouts.controller.output)..($script:NativeBuildProfile.layouts.controller.output+3)]);flags=[BitConverter]::ToUInt16($raw,$script:NativeBuildProfile.layouts.controller.flags)}
}
function Get-NativeControlDomain($Binding,[byte[]]$Id){
    $def=$Binding.definition;$seed=[long][BitConverter]::ToUInt32($def,$script:NativeBuildProfile.layouts.definition.seed)
    if($seed-ne 0){
        $s=$reader.Read($seed,$script:NativeBuildProfile.layouts.response_seed.size);$n=[int][BitConverter]::ToUInt16($s,$script:NativeBuildProfile.layouts.response_seed.point_count);$p=[long][BitConverter]::ToUInt32($s,$script:NativeBuildProfile.layouts.response_seed.points)
        if($n-lt 1-or $n-gt 4096-or $s[0]-gt 9){throw 'Invalid native response seed.'}
        $points=$reader.Read($p,8*$n);$low=[double]::PositiveInfinity;$high=[double]::NegativeInfinity;$previous=-1
        for($i=0;$i-lt $n;$i++){
            $position=[BitConverter]::ToInt32($points,8*$i);$value=[BitConverter]::ToSingle($points,8*$i+4)
            if($position-le $previous-or $position-gt 255-or $position-lt 0-or [single]::IsNaN($value)-or [single]::IsInfinity($value)){throw 'Invalid native response points.'}
            $previous=$position;$low=[math]::Min($low,$value);$high=[math]::Max($high,$value)
        }
        if($Binding.raw_type-eq 0-and ($s[0]-ne 0-or $low-ne [math]::Truncate($low)-or $high-ne [math]::Truncate($high))){throw 'Unresolved discrete response domain.'}
        return [pscustomobject]@{minimum=$low;maximum=$high;allowed=$null}
    }
    if($Binding.raw_type-ne 0){throw 'Native numeric response bounds unavailable.'}
    $profile=$script:NativeBuildProfile.control_enums
    $idlo=[BitConverter]::ToUInt16($Id,0);$key=$idlo.ToString();$allowed=@(0,1)
    if($script:NativeBuildProfile.implementation-ceq 'quantum-layout-v2'){
        if($null-eq $profile.PSObject.Properties[$key]){throw 'Native enum has no explicit v2 profile restriction.'}
        $allowed=@($profile.$key)
    }elseif($idlo-eq 0x0404){
        $band=[int]$Id[2]
        if($band-eq 0){$allowed=@(1,4)}elseif($band-eq 3){$allowed=@(1,3)}else{
            if($band-notin @(1,2,4,5,6,7)){throw 'Unknown EQ shape band.'}
            $consoleType=$reader.U8([long]$reader.U32($base+$script:NativeBuildProfile.addresses.hex_8e92a8))
            if($band-in @(1,2)){
                if($consoleType-gt 14-and $consoleType-notin @(19,20,21,24)){$allowed=@(1)}elseif($band-eq 1){$allowed=@(1,4)}else{$allowed=@(1,3)}
            }elseif($consoleType-notin @(14,17,19,20,21,24)){$allowed=@(1)}elseif($band-in @(4,5)){$allowed=@(0,1)}else{$allowed=@(1,2)}
        }
    }elseif($idlo-eq 0x1C04){
        switch([int]$Id[2]){0{$allowed=@(1,4)};1{$allowed=@(1,9)};2{$allowed=@(1,9)};3{$allowed=@(1,3)};default{throw 'Unknown EQ shape band.'}}
    }elseif($profile.PSObject.Properties[$key]){$allowed=@($profile.$key)}
    if($allowed-notcontains [BitConverter]::ToSingle($def,1)){throw 'Native enum default contradicts the known domain.'}
    return [pscustomobject]@{minimum=$null;maximum=$null;allowed=$allowed}
}
function Assert-NativeDefinitionResponseBinding($Binding,$Step){
    $profile=Get-NativeBuildProfile
    if($profile.implementation-cne 'quantum-layout-v2'){return}
    $seedSlot=$profile.layouts.controller.PSObject.Properties['current_response_seed']
    $lutSlot=$profile.layouts.controller.PSObject.Properties['current_response_lut']
    $claimed=$Step.control_contract.PSObject.Properties['response_pointer_binding']
    if($null-eq $seedSlot-or $null-eq $seedSlot.Value-or $null-eq $lutSlot-or $null-eq $lutSlot.Value-or $null-eq $claimed-or $null-eq $claimed.Value){throw 'Current response pointer binding is missing; v2 control write is not admitted.'}
    $context=$claimed.Value
    foreach($field in @('current_response_seed_pointer','current_response_lut_pointer','definition_response_seed_pointer','definition_response_lut_pointer','effective_response_seed_pointer','effective_response_lut_pointer')){
        if(-not (Test-NativeBuildInteger $context.$field)-or $context.$field-lt 0-or $context.$field-gt 0xffffffffL){throw 'Invalid current response pointer contract.'}
    }
    $seed=[long]$reader.U32([long]$Binding.pointer+[int]$seedSlot.Value)
    $lut=[long]$reader.U32([long]$Binding.pointer+[int]$lutSlot.Value)
    $definitionSeed=[long][BitConverter]::ToUInt32($Binding.definition,[int]$profile.layouts.definition.seed)
    $definitionLut=[long][BitConverter]::ToUInt32($Binding.definition,[int]$profile.layouts.definition.lut)
    if(($seed-ne 0-and $seed-ne $definitionSeed)-or ($lut-ne 0-and $lut-ne $definitionLut)){throw 'Runtime response override differs from definition; v2 control write is not admitted.'}
    if($context.current_response_seed_pointer-ne $seed-or $context.current_response_lut_pointer-ne $lut-or
        $context.definition_response_seed_pointer-ne $definitionSeed-or $context.definition_response_lut_pointer-ne $definitionLut-or
        $context.effective_response_seed_pointer-ne $definitionSeed-or $context.effective_response_lut_pointer-ne $definitionLut-or $context.overrides_admitted-isnot [bool]-or $context.overrides_admitted){throw 'Current response pointer binding changed after planning; take a fresh inventory.'}
}
function Assert-NativeControlWriteAllowlist($Binding,$Step){
    Assert-NativeBuildFeature 'control_write'
    Assert-NativeBuildContract $Step
    Assert-NativeBuildContract $Step.control_contract
    $profile=Get-NativeBuildProfile
    $policy=$profile.PSObject.Properties['control_write_allowlist']
    if(($null-eq $policy-or $null-eq $policy.Value)-and $profile.implementation-ceq 'quantum1-v2126-layout-v1'){return}
    if($null-eq $policy-or $null-eq $policy.Value-or @($policy.Value).Count-eq 0){throw 'Control is not admitted by the selected build control_write_allowlist.'}
    if(-not (Test-NativeBuildInteger $Binding.raw_type)-or $Binding.raw_type-lt 0-or $Binding.raw_type-gt 4){throw 'Invalid native control allowlist datatype.'}
    foreach($field in @('raw_type','definition_rva','module_vmt_rva','process_output_rva')){
        if(-not (Test-NativeBuildInteger $Step.control_contract.$field)){throw 'Invalid native allowlist compiled binding.'}
    }
    $id=ConvertFrom-NativeControlHex $Step.id 5;$prefix=([BitConverter]::ToString($id,0,3)).Replace('-','').ToLowerInvariant()
    $definitionRva=[long]$Binding.definition_pointer-[long]$base
    $vmt=[long]$reader.U32([long]$Binding.owner_pointer);$vmtRva=$vmt-[long]$base
    $callbackRva=[long]$reader.U32($vmt+$profile.layouts.module.process_output_vmt_slot)-[long]$base
    if($definitionRva-le 0-or $definitionRva-ge $profile.executable.image_size-or $null-eq $Step.control_contract.definition_rva-or $Step.control_contract.definition_rva-ne $definitionRva){throw 'Control definition differs from the admitted native allowlist binding.'}
    if($Step.control_contract.module_vmt_rva-ne $vmtRva-or $Step.control_contract.process_output_rva-ne $callbackRva-or $Step.control_contract.raw_type-ne $Binding.raw_type){throw 'Control callback/type differs from the fresh native allowlist binding.'}
    $matched=0
    if($null-ne $policy-and $null-ne $policy.Value){foreach($rule in $policy.Value){
        if($rule.id_prefix_hex-ceq $prefix-and $rule.raw_type-eq $Binding.raw_type-and
            $rule.definition_rva-eq $definitionRva-and $rule.module_vmt_rva-eq $vmtRva-and $rule.process_output_rva-eq $callbackRva){$matched++}
    }}
    if($matched-ne 1){throw 'Control is not admitted by the selected build control_write_allowlist.'}
    Assert-NativeDefinitionResponseBinding -Binding $Binding -Step $Step
}
function Assert-NativeControlValue($Binding,$Step){
    Assert-NativeControlWriteAllowlist -Binding $Binding -Step $Step
    if($Binding.raw_type-notin @(0,1)){throw 'This generic setter only accepts native float32 numeric controls; packed routes need a semantic operation.'}
    $id=ConvertFrom-NativeControlHex $Step.id 5
    $domain=Assert-NativeControlContext -Binding $Binding -Step $Step
    if($null-eq $domain){$domain=Get-NativeControlDomain $Binding $id}
    $bytes=ConvertFrom-NativeControlHex $Step.control_contract.payload_hex 4;$value=[BitConverter]::ToSingle($bytes,0)
    if([single]::IsNaN($value)-or [single]::IsInfinity($value)){throw 'Non-finite control value.'}
    if($Binding.raw_type-eq 0-and $value-ne [math]::Truncate($value)){throw 'Discrete control requires an integer-valued float.'}
    if($domain.allowed){if($domain.allowed-notcontains $value){throw 'Value is outside the native numeric enum.'}}
    elseif($value-lt $domain.minimum-or $value-gt $domain.maximum){throw 'Value is outside native response bounds.'}
    if([double]$Step.control_contract.value-ne [double]$value){throw 'Compiled value and payload disagree.'}
    return ,$bytes
}
function New-NativeControlFrame([string]$Id,[byte[]]$Body){
    $idBytes=ConvertFrom-NativeControlHex $Id 5
    $m=[IO.MemoryStream]::new();$w=[IO.BinaryWriter]::new($m)
    try{
        $w.Write([uint32](18+$Body.Length));$w.Write([uint16]255);$w.Write([byte]8);$w.Write($idBytes);$w.Write([int32]0);$w.Write($Body);$w.Flush()
        $sum=0;foreach($b in $m.ToArray()){$sum=($sum+$b)-band 65535};$w.Write([uint16]$sum);$w.Flush();return ,$m.ToArray()
    }finally{$w.Dispose();$m.Dispose()}
}
function Wait-NativeControlWrite($Step){
    $timer=[Diagnostics.Stopwatch]::StartNew()
    while($timer.ElapsedMilliseconds-lt 10000){
        Pump 75;$bound=Read-NativeControlBinding $Step
        Assert-NativeDefinitionResponseBinding -Binding $bound -Step $Step
        $wanted=[BitConverter]::ToSingle((ConvertFrom-NativeControlHex $Step.control_contract.payload_hex 4),0)
        $tolerance=[math]::Max([math]::Abs([double]$wanted)*[math]::Pow(2,-23),[math]::Pow(2,-149))
        $value=[BitConverter]::ToSingle($bound.value_raw,0);$output=[BitConverter]::ToSingle($bound.output_raw,0)
        if(-not [single]::IsNaN($value)-and -not [single]::IsNaN($output)-and [math]::Abs([double]$value-$wanted)-le $tolerance-and [math]::Abs([double]$output-$wanted)-le $tolerance){return $bound}
    }
    throw 'Native control did not reach the requested value/output; inspect before retry.'
}

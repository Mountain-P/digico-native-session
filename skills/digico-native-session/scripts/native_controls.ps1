# Typed native Cat8 writes. All process access below is read-only.
function ConvertFrom-NativeControlHex([string]$Text,[int]$Length){
    if($Text-notmatch ('\A[0-9a-fA-F]{'+($Length*2)+'}\z')){throw 'Invalid native control hex length.'}
    $b=New-Object byte[] $Length;for($i=0;$i-lt $Length;$i++){$b[$i]=[Convert]::ToByte($Text.Substring(2*$i,2),16)};return ,$b
}
function New-NativeControlContext{
    return @{Reader=$reader;Base=[long]$base;End=([long]$base+0x4440000);Ascii=$ascii;Encoding=$nameEncoding;Types=@{};Definitions=@{};IncludeResponseTables=$true;CurrentObject=0L;CurrentId='';ListWitnesses=[Collections.Generic.List[object]]::new();Identities=[Collections.Generic.List[object]]::new()}
}
function Assert-NativeChild([long]$Parent,[long]$Child){
    $n=$reader.I32($Parent+8);$a=[long]$reader.U32($Parent+4)
    if($n-lt 0-or $n-gt 65536-or ($n-gt 0-and $a-eq 0)){throw 'Invalid control-owner list.'}
    $b=$reader.Read($a,4*$n);$found=$false
    for($j=0;$j-lt $n;$j++){if([BitConverter]::ToUInt32($b,4*$j)-eq $Child){$found=$true;break}}
    if(-not $found){throw 'The control owner is no longer a member of the live console tree.'}
}
function Read-NativeControlBinding($Step){
    $contract=$Step.control_contract;$id=ConvertFrom-NativeControlHex $Step.id 5
    if($id[1]-eq 10){throw 'Cue/Automation controls are excluded.'}
    if($id[0]-eq 252){throw 'Channel label colour is a derived status control, not a palette setter.'}
    if($id[1]-eq 9-and $id[0]-eq 0x18){throw 'Total delay is a derived main-plus-fine value.'}
    $pointer=[long]$contract.control_pointer;$owner=[long]$contract.owner_module_pointer
    if($pointer-le 0-or $pointer-gt 0xffffffffL-or $owner-le 0-or $owner-gt 0xffffffffL){throw 'Invalid native control binding pointers.'}
    $ctx=New-NativeControlContext;$type=Get-NativeInventoryType $ctx $pointer
    if($type.kind-ne 'controller'){throw 'Bound object is not a native controller.'}
    $raw=$reader.Read($pointer,0x5C)
    if((Hex ([byte[]]$raw[4..8]))-cne $Step.id-or [BitConverter]::ToUInt32($raw,0x44)-ne $owner){throw 'Native control identity/owner changed.'}
    $ownerType=Get-NativeInventoryType $ctx $owner
    if($ownerType.kind-ne 'module'-or $ownerType.vmt_rva-ne $contract.module_vmt_rva-or $reader.U32($ownerType.vmt+0x40)-ne ($base+[long]$contract.process_output_rva)){throw 'Native owner VMT/callback binding changed.'}
    $ownerID=$reader.Read($owner+0x30,5)
    if($ownerID[1]-ne $id[1]-or $ownerID[3]-ne $id[3]-or $ownerID[4]-ne $id[4]){throw 'Native owner module ID differs.'}
    Assert-NativeChild $owner $pointer
    $node=$owner
    for($depth=0;$depth-lt 8;$depth++){
        $parent=[long]$reader.U32($node+0x24)
        if($parent-eq 0){throw 'Native control has no console owner chain.'}
        Assert-NativeChild $parent $node
        $parentType=Get-NativeInventoryType $ctx $parent
        if($parentType.kind-eq 'console'){
            if($parent-ne $reader.U32($base+0x1279CFC)){throw 'Control belongs to another console.'};break
        }
        $node=$parent
    }
    if($depth-ge 8){throw 'Native control owner depth exceeded.'}
    $definitionPointer=[long][BitConverter]::ToUInt32($raw,9);$definition=$reader.Read($definitionPointer,58)
    if((Hex $definition)-cne $contract.definition_raw_hex-or [int]$definition[0]-ne $contract.raw_type-or $definition[6]-ne $id[0]){throw 'Native controller definition changed.'}
    if(($definition[5]-ne 0-or $id[1]-eq 5)-and $definition[5]-ne $id[2]){throw 'Native band binding differs.'}
    return [pscustomobject]@{pointer=$pointer;id=$Step.id;raw_type=[int]$definition[0];definition=$definition;definition_pointer=$definitionPointer;owner_pointer=$owner;value_raw=([byte[]]$raw[0x18..0x1B]);next_raw=([byte[]]$raw[0x1C..0x1F]);output_raw=([byte[]]$raw[0x2C..0x2F]);flags=[BitConverter]::ToUInt16($raw,0x12)}
}
function Get-NativeControlDomain($Binding,[byte[]]$Id){
    $def=$Binding.definition;$seed=[long][BitConverter]::ToUInt32($def,50)
    if($seed-ne 0){
        $s=$reader.Read($seed,38);$n=[int][BitConverter]::ToUInt16($s,32);$p=[long][BitConverter]::ToUInt32($s,34)
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
    $profile=Get-Content -LiteralPath (Join-Path $SkillRoot 'assets\control-enums.json') -Raw -Encoding UTF8|ConvertFrom-Json
    if($profile.executable_sha256-cne $expectedHash){throw 'Native enum profile differs.'}
    $idlo=[BitConverter]::ToUInt16($Id,0);$key=$idlo.ToString('x4');$allowed=@(0,1)
    if($idlo-eq 0x0404){
        $band=[int]$Id[2]
        if($band-eq 0){$allowed=@(1,4)}elseif($band-eq 3){$allowed=@(1,3)}else{
            if($band-notin @(1,2,4,5,6,7)){throw 'Unknown EQ shape band.'}
            $consoleType=$reader.U8([long]$reader.U32($base+0x8E92A8))
            if($band-in @(1,2)){
                if($consoleType-gt 14-and $consoleType-notin @(19,20,21,24)){$allowed=@(1)}elseif($band-eq 1){$allowed=@(1,4)}else{$allowed=@(1,3)}
            }elseif($consoleType-notin @(14,17,19,20,21,24)){$allowed=@(1)}elseif($band-in @(4,5)){$allowed=@(0,1)}else{$allowed=@(1,2)}
        }
    }elseif($idlo-eq 0x1C04){
        switch([int]$Id[2]){0{$allowed=@(1,4)};1{$allowed=@(1,9)};2{$allowed=@(1,9)};3{$allowed=@(1,3)};default{throw 'Unknown EQ shape band.'}}
    }elseif($profile.values_by_idlo.PSObject.Properties[$key]){$allowed=@($profile.values_by_idlo.$key)}
    if($allowed-notcontains [BitConverter]::ToSingle($def,1)){throw 'Native enum default contradicts the known domain.'}
    return [pscustomobject]@{minimum=$null;maximum=$null;allowed=$allowed}
}
function Assert-NativeControlValue($Binding,$Step){
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
        $wanted=[BitConverter]::ToSingle((ConvertFrom-NativeControlHex $Step.control_contract.payload_hex 4),0)
        $tolerance=[math]::Max([math]::Abs([double]$wanted)*[math]::Pow(2,-23),[math]::Pow(2,-149))
        $value=[BitConverter]::ToSingle($bound.value_raw,0);$output=[BitConverter]::ToSingle($bound.output_raw,0)
        if(-not [single]::IsNaN($value)-and -not [single]::IsNaN($output)-and [math]::Abs([double]$value-$wanted)-le $tolerance-and [math]::Abs([double]$output-$wanted)-le $tolerance){return $bound}
    }
    throw 'Native control did not reach the requested value/output; inspect before retry.'
}

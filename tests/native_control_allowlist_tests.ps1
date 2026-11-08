# Synthetic profile and fake RPM only. No editor, network or process access.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ProfileModulePath,
      [Parameter(Mandatory=$true)][string]$ControlModulePath)
$ErrorActionPreference='Stop'
$SkillRoot=Split-Path (Split-Path $ProfileModulePath -Parent) -Parent
. ([scriptblock]::Create([IO.File]::ReadAllText($ProfileModulePath)))
. ([scriptblock]::Create([IO.File]::ReadAllText($ControlModulePath)))
$hash='535097f38bb459e6e299b5da50b62fa811511d92af6d7215a13d703a416f177d'
$profile=Set-NativeBuildProfile -ExecutableSha256 $hash
$base=0x400000;$script:owner=0x10000000
$script:observedVmt=0x81e510;$script:observedCallback=0x832414
$script:control=0x20000000;$script:currentSeed=0;$script:currentLut=0
$reader=[pscustomobject]@{}
$reader|Add-Member -MemberType ScriptMethod -Name U32 -Value {
    param([long]$Address)
    if($Address-eq $script:owner){return [uint32]($base+$script:observedVmt)}
    if($Address-eq ($base+$script:observedVmt+0x40)){return [uint32]($base+$script:observedCallback)}
    if($Address-eq ($script:control+0x43)){return [uint32]$script:currentSeed}
    if($Address-eq ($script:control+0x47)){return [uint32]$script:currentLut}
    throw 'Unexpected synthetic allowlist RPM.'
}
$rule=[pscustomobject]@{id_prefix_hex='210c00';raw_type=1;definition_rva=0x10100;module_vmt_rva=0x81e510;process_output_rva=0x832414}
$definition=New-Object byte[] 58;$definition[0]=1
[Array]::Copy([BitConverter]::GetBytes([uint32]0x500000),0,$definition,50,4)
$binding=[pscustomobject]@{pointer=$script:control;raw_type=1;definition=$definition;definition_pointer=($base+0x10100);owner_pointer=$script:owner}
$contract=[pscustomobject]@{build_profile=$profile.id;executable_sha256=$hash;raw_type=1;definition_rva=0x10100;module_vmt_rva=0x81e510;process_output_rva=0x832414}
$contract|Add-Member -NotePropertyName response_pointer_binding -NotePropertyValue ([pscustomobject]@{
    current_response_seed_pointer=0;current_response_lut_pointer=0;definition_response_seed_pointer=0x500000;
    definition_response_lut_pointer=0;effective_response_seed_pointer=0x500000;effective_response_lut_pointer=0;overrides_admitted=$false})
$step=[pscustomobject]@{build_profile=$profile.id;executable_sha256=$hash;id='210c000001';control_contract=$contract}
function Reject-Allowlist([scriptblock]$Action){
    $message='';try{& $Action}catch{$message=$_.Exception.Message}
    if($message-notmatch 'allowlist|response'){throw ('Expected an allowlist/response rejection, got: '+$message)}
}
# Legacy keeps its existing typed-domain behavior; changing implementation alone
# never admits the same control without an explicit v2 rule.
Assert-NativeControlWriteAllowlist -Binding $binding -Step $step
$profile.implementation='quantum-layout-v2'
$profile.layouts.controller|Add-Member -NotePropertyName current_response_seed -NotePropertyValue 0x43 -Force
$profile.layouts.controller|Add-Member -NotePropertyName current_response_lut -NotePropertyValue 0x47 -Force
Reject-Allowlist {Assert-NativeControlWriteAllowlist -Binding $binding -Step $step}
$profile|Add-Member -NotePropertyName control_write_allowlist -NotePropertyValue ([object[]]@($rule)) -Force
Assert-NativeControlWritePolicy $profile
Assert-NativeControlWriteAllowlist -Binding $binding -Step $step
foreach($field in @('id_prefix_hex','raw_type','definition_rva','module_vmt_rva','process_output_rva')){
    $changed=$rule.PSObject.Copy()
    if($field-eq 'id_prefix_hex'){$changed.$field='220c00'}elseif($field-eq 'raw_type'){$changed.$field=4}else{$changed.$field=[long]$changed.$field+4}
    $profile.control_write_allowlist=[object[]]@($changed)
    Reject-Allowlist {Assert-NativeControlWriteAllowlist -Binding $binding -Step $step}
}
$profile.control_write_allowlist=[object[]]@($rule)
$script:observedVmt+=4;$step.control_contract.module_vmt_rva=$script:observedVmt
Reject-Allowlist {Assert-NativeControlWriteAllowlist -Binding $binding -Step $step}
$script:observedVmt=0x81e510;$step.control_contract.module_vmt_rva=0x81e510
$script:observedCallback+=4;$step.control_contract.process_output_rva=$script:observedCallback
Reject-Allowlist {Assert-NativeControlWriteAllowlist -Binding $binding -Step $step}
$script:observedCallback=0x832414;$step.control_contract.process_output_rva=0x832414
$binding.definition_pointer+=4;$step.control_contract.definition_rva+=4
Reject-Allowlist {Assert-NativeControlWriteAllowlist -Binding $binding -Step $step}
$binding.definition_pointer=$base+0x10100;$step.control_contract.definition_rva=0x10100
$step.id='fd0c000001';$binding.raw_type=4;$step.control_contract.raw_type=4
Reject-Allowlist {Assert-NativeControlWriteAllowlist -Binding $binding -Step $step}
$step.id='210c000001';$binding.raw_type=1;$step.control_contract.raw_type=$true
Reject-Allowlist {Assert-NativeControlWriteAllowlist -Binding $binding -Step $step}
$step.control_contract.raw_type=1
$script:currentSeed=0x500004
Reject-Allowlist {Assert-NativeControlWriteAllowlist -Binding $binding -Step $step}
$script:currentSeed=0x500000
Reject-Allowlist {Assert-NativeControlWriteAllowlist -Binding $binding -Step $step}
$script:currentSeed=0;$script:currentLut=0x600000
Reject-Allowlist {Assert-NativeControlWriteAllowlist -Binding $binding -Step $step}
$script:currentLut=0
Assert-NativeControlWriteAllowlist -Binding $binding -Step $step
$profile.control_write_allowlist=[object[]]@()
Reject-Allowlist {Assert-NativeControlWriteAllowlist -Binding $binding -Step $step}
$profile.control_write_allowlist=$null
Reject-Allowlist {Assert-NativeControlWriteAllowlist -Binding $binding -Step $step}
$booleanDefinition=New-Object byte[] 58;$booleanDefinition[6]=0x22
$booleanBinding=[pscustomobject]@{raw_type=0;definition=$booleanDefinition}
$rejected=$false
try{Get-NativeControlDomain $booleanBinding ([byte[]](0x22,0x0c,0,0,1))|Out-Null}catch{
    if($_.Exception.Message-notmatch 'no explicit v2 profile restriction'){throw};$rejected=$true
}
if(-not $rejected){throw 'v2 boolean inherited an unproven default enum.'}
$profile.control_enums|Add-Member -NotePropertyName ([string]0x0c22) -NotePropertyValue ([object[]]@(0,1)) -Force
$domain=Get-NativeControlDomain $booleanBinding ([byte[]](0x22,0x0c,0,0,1))
if($domain.allowed.Count-ne 2-or $domain.allowed-notcontains 0-or $domain.allowed-notcontains 1-or $domain.allowed-contains 2){throw 'v2 explicit boolean subset was not retained.'}
'Native exact control allowlist and tamper tests passed.'

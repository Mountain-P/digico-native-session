# Synthetic reader only. No process, sockets, Session files or UI.
[CmdletBinding()]
param([string]$StructureModulePath='')
$ErrorActionPreference='Stop'
if(-not $StructureModulePath){$StructureModulePath=Join-Path $PSScriptRoot '..\skills\digico-native-session\scripts\native_structure.ps1'}
$SkillRoot=Split-Path (Split-Path $StructureModulePath -Parent) -Parent
. (Join-Path $SkillRoot 'scripts\native_profile.ps1')
$null=Set-NativeBuildProfile -ExecutableSha256 '535097f38bb459e6e299b5da50b62fa811511d92af6d7215a13d703a416f177d'
. $StructureModulePath
function Assert-StructureTest([bool]$Condition,[string]$Message){if(-not $Condition){throw $Message}}
function Assert-StructureThrows([scriptblock]$Action,[string]$Message){$failed=$false;try{& $Action|Out-Null}catch{$failed=$true};Assert-StructureTest $failed $Message}
$base=0x400000
$script:aliasAddress=$base+0x8E9980;$script:recordAddress=$base+0x127A17C
$script:aliasValue=$script:recordAddress;$script:aliasReads=0;$script:recordReads=0;$script:mode='valid'
$script:raw=New-Object byte[] 123
for($i=0;$i-lt 123;$i++){$script:raw[$i]=[byte]$i}
$reader=[pscustomobject]@{}
$reader|Add-Member -MemberType ScriptMethod -Name U32 -Value {
    param([long]$Address)
    if($Address-ne $script:aliasAddress){throw 'Unexpected synthetic pointer read.'}
    $script:aliasReads++
    if($script:mode-eq 'alias-change'-and $script:aliasReads-gt 1){return [uint32]($script:recordAddress+4)}
    return [uint32]$script:aliasValue
}
$reader|Add-Member -MemberType ScriptMethod -Name Read -Value {
    param([long]$Address,[int]$Length)
    if($Address-ne $script:recordAddress-or $Length-ne 123){throw 'Unexpected synthetic raw record read.'}
    $script:recordReads++
    if($script:mode-eq 'short'){return ,(New-Object byte[] 122)}
    $copy=[byte[]]$script:raw.Clone()
    if($script:mode-eq 'record-change'-and $script:recordReads-gt 1){$copy[0]=$copy[0]-bxor 1}
    return ,$copy
}
$result=Read-NativeSessionStructure
Assert-StructureTest ($result.raw_hex-eq (ConvertTo-NativeStructureHex $script:raw)-and $result.record_bytes-eq 123) 'Structure copied bytes/length mismatch.'
Assert-StructureTest ($result.alias_verified-and $result.consistent_double_read-and $result.memory_read_only-and -not $result.ui_used-and -not $result.network_used-and -not $result.headless_structure_setter_established) 'Structure read-only provenance mismatch.'
Assert-StructureTest ($script:aliasReads-eq 2-and $script:recordReads-eq 2) 'Structure did not recheck alias and record.'
$counts=@($result.offset_layout|Where-Object {$_.name-eq 'input_count_slots'})
Assert-StructureTest ($counts.Count-eq 1-and $counts[0].offset-eq 2-and $counts[0].bytes-eq 16-and -not $counts[0].labels_verified) 'Count slot provenance/labels mismatch.'
foreach($mode in @('short','alias-change','record-change')) {
    $script:mode=$mode;$script:aliasReads=0;$script:recordReads=0
    Assert-StructureThrows {Read-NativeSessionStructure} "Structure accepted $mode."
}
$script:mode='valid';$script:aliasReads=0;$script:recordReads=0;$script:aliasValue=$script:recordAddress+4
Assert-StructureThrows {Read-NativeSessionStructure} 'Structure accepted wrong same-build alias target.'
Assert-StructureTest ($script:recordReads-eq 0) 'Structure read a record before alias identity passed.'
Write-Output 'Native structure synthetic readback tests passed.'

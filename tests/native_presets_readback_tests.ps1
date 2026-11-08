# Synthetic sparse-address reader tests. No process, socket, Session file or UI.
[CmdletBinding()]
param([string]$PresetModulePath='')
$ErrorActionPreference='Stop'
if(-not $PresetModulePath){$PresetModulePath=Join-Path $PSScriptRoot '..\skill\digico-native-session\scripts\native_presets.ps1'}
. $PresetModulePath
function Assert-PresetTest([bool]$Condition,[string]$Message){if(-not $Condition){throw $Message}}
function Assert-PresetThrows([scriptblock]$Action,[string]$Message){$failed=$false;try{& $Action|Out-Null}catch{$failed=$true};Assert-PresetTest $failed $Message}
Add-Type -TypeDefinition @'
using System;
public class SyntheticPresetReader {
  public byte[] Bytes = new byte[0x1280000];
  public byte[] Read(long at,int count) { var b=new byte[count];Array.Copy(Bytes,at,b,0,count);return b; }
  public byte U8(long at) { return Bytes[at]; }
  public ushort U16(long at) { return BitConverter.ToUInt16(Bytes,(int)at); }
  public uint U32(long at) { return BitConverter.ToUInt32(Bytes,(int)at); }
  public int I32(long at) { return BitConverter.ToInt32(Bytes,(int)at); }
}
'@
$reader=[SyntheticPresetReader]::new();$base=0
$native=[pscustomobject]@{Client=$null}
function Send-Bytes {throw 'A readback helper unexpectedly tried to send.'}
function Pump {throw 'Synthetic readback unexpectedly tried to pump a network connection.'}
function Set-PresetBytes([int]$At,[byte[]]$Bytes){[Array]::Copy($Bytes,0,$reader.Bytes,$At,$Bytes.Length)}
function Set-PresetU32([int]$At,[uint32]$Value){Set-PresetBytes $At ([BitConverter]::GetBytes($Value))}
function Set-PresetU16([int]$At,[uint16]$Value){Set-PresetBytes $At ([BitConverter]::GetBytes($Value))}
function Set-PresetShort([int]$At,[string]$Text){$b=[Text.Encoding]::ASCII.GetBytes($Text);$reader.Bytes[$At]=[byte]$b.Length;Set-PresetBytes ($At+1) $b}
function Set-PresetList([int]$At,[uint32]$VMT,[int]$Items,[int[]]$Pointers){
    Set-PresetU32 $At $VMT;Set-PresetU32 ($At+4) $Items
    Set-PresetU32 ($At+8) $Pointers.Length;Set-PresetU32 ($At+12) $Pointers.Length
    for($i=0;$i-lt $Pointers.Length;$i++){Set-PresetU32 ($Items+4*$i) $Pointers[$i]}
}
function Set-SyntheticPreset([int]$At,[string]$Name){
    Set-PresetList $At 0x77315C 0x5000 @(0x6000)
    Set-PresetShort ($At+0x24) $Name;Set-PresetShort ($At+0x44) 'Synthetic Lab'
    Set-PresetShort ($At+0x64) 'Synthetic note'
    Set-PresetBytes ($At+0x164) ([BitConverter]::GetBytes([double]1))
    Set-PresetU16 ($At+0x16F) 1
}
Set-PresetU32 0x126E3E8 0x2000
Set-PresetList 0x2000 0x7735B8 0x2100 @(0x3000)
Set-SyntheticPreset 0x3000 'Initial'
Set-PresetList 0x6000 0x7F90A0 0x7000 @(0x8000)
Set-PresetU16 0x6024 0x100;Set-PresetShort 0x6026 'Input One'
Set-PresetU32 0x8000 0x7F7938;Set-PresetU32 0x8004 12
Set-PresetU32 0x8030 1;Set-PresetU32 0x803E 0x9000
Set-PresetBytes 0x9000 ([byte[]](0x21,0x0C,0,0,0,0,0x40,0x3F))
Set-PresetShort 0x127B4FC 'C:\Synthetic\Fixture.ses'
# Live ordinary input-channel binding.
Set-PresetU32 0x1279CFC 0x10000
Set-PresetList 0x10000 0x864808 0x11000 @(0x12000)
Set-PresetList 0x12000 0x86600C 0x13000 @(0x14000);$reader.Bytes[0x12030]=1
Set-PresetU32 0x14000 0x86660C;Set-PresetU16 0x14030 0x100;Set-PresetShort 0x14034 'Input One'
# Runtime contains tNodalProcessors (a derived tGroup) before input group1.
# Its inherited layout is valid; exact base-class equality must not reject it.
Set-PresetList 0x1A000 0x7F5730 0x1B000 @();$reader.Bytes[0x1A030]=20
Set-PresetU32 (0x7F5730-0x30) 0x1C000;Set-PresetU32 0x1C000 0x86600C
Set-PresetList 0x10000 0x864808 0x11000 @(0x1A000,0x12000)
Assert-PresetTest ((Read-DiGiCoPresetInputChannel 0x100).channel_id-eq 0x100) 'Derived non-input group blocked ordinary input lookup.'
# One synthetic Cue with no preset reference initially.
Set-PresetU32 0x127305C 0x15000
Set-PresetList 0x15000 0x7F9BB0 0x16000 @(0x17000)
Set-PresetList 0x17000 0x7F8188 0x18000 @(0x19000)
Set-PresetU32 0x19000 0x7F90A0

$before=Read-DiGiCoNativePresets -ContentIndices @(0)
Assert-PresetTest ($before.preset_count-eq 1-and $before.presets[0].name-eq 'Initial') 'Preset metadata decoding mismatch.'
Assert-PresetTest ($before.memory_read_only-and -not $before.library_file_path_established-and $null-eq $before.library_file_path) 'Read-only/library provenance mismatch.'
$content=$before.presets[0].content
Assert-PresetTest ($content.channel_snapshot_count-eq 1-and $content.controller_count-eq 1-and $content.channels[0].modules[0].controllers[0].control_id_hex-eq '210c000001') 'Stored controller layout mismatch.'
Assert-PresetTest (Test-DiGiCoPresetControllerSubset $content @([pscustomobject]@{control_id_hex='210c000001';value_raw_hex='0000403f'})) 'Controller subset rejected exact synthetic value.'
Set-PresetU32 0x3000 0
Assert-PresetThrows {Read-DiGiCoNativePresets} 'Wrong preset VMT accepted.'
Set-PresetU32 0x3000 0x77315C

$rename=[pscustomobject]@{op='rename_preset';category=3;control_id_hex='ce0000b601';list_index=0;body_hex='010000004e';name_hex='4e'}
$expected=Assert-DiGiCoPresetEdit $before $rename
Set-PresetShort 0x3024 'N'
$verified=Wait-DiGiCoPresetEdit $before $rename $expected -TimeoutMs 500
Assert-PresetTest ($verified.metadata_verified-and -not $verified.processing_verified) 'Rename readback flags mismatch.'
$wrong=$rename.PSObject.Copy();$wrong.body_hex='020000004e'
Assert-PresetThrows {Assert-DiGiCoPresetEdit $before $wrong} 'Inconsistent string length accepted.'
$wrong=$rename.PSObject.Copy();$wrong.category=1
Assert-PresetThrows {Assert-DiGiCoPresetEdit $before $wrong} 'Wrong category accepted.'
$reader.Bytes[0x316C]=2
Assert-PresetThrows {Assert-DiGiCoPresetEdit (Read-DiGiCoNativePresets) $rename} 'Factory preset mutation accepted.'
$reader.Bytes[0x316C]=0

$before=Read-DiGiCoNativePresets
$lock=[pscustomobject]@{op='lock_preset';category=1;control_id_hex='d20000b601';list_index=0;body_hex='01000000';expected_lock_flag=1}
$expected=Assert-DiGiCoPresetEdit $before $lock;$reader.Bytes[0x316C]=1
$verified=Wait-DiGiCoPresetEdit $before $lock $expected -TimeoutMs 500
Assert-PresetTest ($verified.state.presets[0].lock_flag-eq 1) 'Lock change was not verified.'
$update=[pscustomobject]@{op='update_input_preset';category=1;control_id_hex='cc0000b601';list_index=0;body_hex='00010000';expected_controller_values=@([pscustomobject]@{control_id_hex='210c000001';value_raw_hex='0000003f'})}
Assert-PresetThrows {Assert-DiGiCoPresetEdit (Read-DiGiCoNativePresets) $update} 'Locked preset update accepted.'
$reader.Bytes[0x316C]=0;$before=Read-DiGiCoNativePresets
$expected=Assert-DiGiCoPresetEdit $before $update
Set-PresetBytes 0x3164 ([BitConverter]::GetBytes([double]2));Set-PresetBytes 0x9004 ([BitConverter]::GetBytes([single]0.5))
$verified=Wait-DiGiCoPresetEdit $before $update $expected -TimeoutMs 500
Assert-PresetTest $verified.stored_controller_subset_verified 'Preset update did not verify stored raw processing values.'

$before=Read-DiGiCoNativePresets
$scopeHex='10'+('00'*31)
$scope=[pscustomobject]@{op='set_preset_recall_scope';category=2;control_id_hex='d30000b601';list_index=0;body_hex=('20000000'+$scopeHex);expected_scope_hex=$scopeHex}
$expected=Assert-DiGiCoPresetEdit $before $scope;$reader.Bytes[0x126E3EC]=0x10
$verified=Wait-DiGiCoPresetEdit $before $scope $expected -TimeoutMs 500
Assert-PresetTest ($verified.state.scope_hex-eq $scopeHex) 'Exact32-byte scope was not verified.'
$before=Read-DiGiCoNativePresets
$recall=[pscustomobject]@{op='recall_input_preset';category=1;control_id_hex='c80000b601';list_index=0;body_hex='00010000';target_channel_id=0x100}
Assert-PresetThrows {Assert-DiGiCoPresetEdit $before $recall} 'Recall accepted without actual processing verifier.'
$check={param($Old,$Now,$Step,$Expected) return $Now.presets[0].content.channels[0].modules[0].controllers[0].value_raw_hex-eq '0000003f'}
$expected=Assert-DiGiCoPresetEdit $before $recall -ProcessingCheck $check
$verified=Wait-DiGiCoPresetEdit $before $recall $expected -ProcessingCheck $check -TimeoutMs 500
Assert-PresetTest $verified.processing_verified 'Recall processing callback was not required/run.'

$delete=[pscustomobject]@{op='delete_preset';category=1;control_id_hex='d10000b601';list_index=0;body_hex='00000000';expected_group_name_hex=([Text.Encoding]::ASCII.GetBytes('Synthetic Lab')|ForEach-Object {$_.ToString('x2')})-join ''}
$null=Assert-DiGiCoPresetEdit $before $delete
Set-PresetU32 0x19046 0x3000
Assert-PresetThrows {Assert-DiGiCoPresetEdit $before $delete} 'Cue-dependent preset delete accepted.'
Set-PresetU32 0x19046 0
$wrong=$delete.PSObject.Copy();$wrong.expected_group_name_hex=''
Assert-PresetThrows {Assert-DiGiCoPresetEdit $before $wrong} 'Delete without explicit lab group accepted.'

$store=[pscustomobject]@{op='store_input_preset';category=3;control_id_hex='c90000b601';list_index=0x100;body_hex='0d00000053796e746865746963204c6162';group_name_hex='53796e746865746963204c6162'}
$before=Read-DiGiCoNativePresets;$expected=Assert-DiGiCoPresetEdit $before $store
Set-SyntheticPreset 0x4000 'Input One';Set-PresetList 0x2000 0x7735B8 0x2100 @(0x3000,0x4000)
$verified=Wait-DiGiCoPresetEdit $before $store $expected -TimeoutMs 500
Assert-PresetTest ($verified.state.preset_count-eq 2-and $verified.state.presets[1].content.controller_count-eq 1) 'Store append/content verification mismatch.'
Write-Output 'Native preset synthetic readback/contract tests passed.'

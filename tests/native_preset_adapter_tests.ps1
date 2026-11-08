# Pure synthetic adapter checks. No sockets, process access, Session files or UI.
[CmdletBinding()]
param([string]$AdapterModulePath='',[string]$PresetModulePath='')
$ErrorActionPreference='Stop'
if(-not $AdapterModulePath){$AdapterModulePath=Join-Path $PSScriptRoot '..\skill\digico-native-session\scripts\native_preset_adapter.ps1'}
if(-not $PresetModulePath){$PresetModulePath=Join-Path $PSScriptRoot '..\skill\digico-native-session\scripts\native_presets.ps1'}
. $PresetModulePath
. $AdapterModulePath
function Assert-PresetAdapterTest([bool]$Condition,[string]$Message){if(-not $Condition){throw $Message}}
function Assert-PresetAdapterThrows([scriptblock]$Action,[string]$Message){$failed=$false;try{& $Action|Out-Null}catch{$failed=$true};Assert-PresetAdapterTest $failed $Message}
function Int($Value,[int]$Minimum,[int]$Maximum,[string]$Label){if($Value-is [bool]-or $Value-lt $Minimum-or $Value-gt $Maximum){throw "Invalid $Label"};return [int]$Value}
function Frame([int]$Category,[int]$Group,[int]$Control,[int]$Index,[byte[]]$Body){
    $raw=New-Object byte[] (18+$Body.Length)
    [Array]::Copy([BitConverter]::GetBytes([uint32]$raw.Length),0,$raw,0,4)
    [Array]::Copy([BitConverter]::GetBytes([uint16]255),0,$raw,4,2);$raw[6]=[byte]$Category
    [Array]::Copy([BitConverter]::GetBytes([uint16]$Control),0,$raw,7,2)
    [Array]::Copy([BitConverter]::GetBytes([uint16]$Group),0,$raw,10,2)
    [Array]::Copy([BitConverter]::GetBytes([int32]$Index),0,$raw,12,4)
    [Array]::Copy($Body,0,$raw,16,$Body.Length)
    $sum=0;for($i=0;$i-lt $raw.Length-2;$i++){$sum=($sum+[int]$raw[$i])-band 65535}
    [Array]::Copy([BitConverter]::GetBytes([uint16]$sum),0,$raw,$raw.Length-2,2)
    return ,$raw
}
function New-AdapterStep([string]$Op,[int]$Category,[int]$Control,[int]$Index,[byte[]]$Body){
    return [pscustomobject]@{op=$Op;category=$Category;control_id_hex=('{0:x2}0000b601'-f $Control);list_index=$Index;body_hex=(ConvertTo-DiGiCoPresetHex $Body);frames=@((ConvertTo-DiGiCoPresetHex (Frame $Category 438 $Control $Index $Body)));expected_count_after=0}
}
function Read-DiGiCoPresetInputChannel([int]$ChannelId){if($ChannelId-ne 0x100){throw 'Unknown mock input'};return [pscustomobject]@{channel_id=$ChannelId}}
$state=[pscustomobject]@{preset_count=1;list_pointer=0x2000;metadata_sha256=('ab'*32);scope_hex=('00'*32);current_session_path_hex='433a5c53796e7468657469635c466978747572652e736573';text_encoding='ascii';presets=@([pscustomobject]@{index=0;pointer=0x3000;group_name_hex='53796e746865746963204c6162'})}
$binding=[pscustomobject]@{preset_count=1;list_pointer=0x2000;metadata_sha256=$state.metadata_sha256;scope_hex=$state.scope_hex;current_session_path_hex=$state.current_session_path_hex;selected_index=0;selected_pointer=0x3000}
$step=New-AdapterStep 'rename_preset' 3 0xCE 0 ([byte[]](1,0,0,0,65));$step|Add-Member name_hex '41'
$request=[pscustomobject]@{preset_operation='preset_rename';preset_inventory_binding=$binding;steps=@($step);expected_count=0;final_expected_preset_count=1;final_expected_scope_hex=$state.scope_hex}
Assert-DiGiCoPresetAdapterBinding $request $state
Assert-DiGiCoPresetAdapterPlan $request $state
$bad=$step.PSObject.Copy();$bad.frames=@('00')
Assert-PresetAdapterThrows {Get-DiGiCoPresetAdapterFrame $bad} 'Adapter accepted a supplied frame not derived from typed fields.'
$badBinding=$binding.PSObject.Copy();$badBinding.scope_hex='ff'*32
$badRequest=$request.PSObject.Copy();$badRequest.preset_inventory_binding=$badBinding
Assert-PresetAdapterThrows {Assert-DiGiCoPresetAdapterBinding $badRequest $state} 'Adapter accepted stale scope.'

$scopeHex='0830'+('00'*30)
$scope=New-AdapterStep 'set_preset_recall_scope' 2 0xD3 0 ([byte[]]([BitConverter]::GetBytes([uint32]32)+(ConvertFrom-DiGiCoPresetAdapterHex $scopeHex 32)))
$scope|Add-Member expected_scope_hex $scopeHex
$scopeRequest=[pscustomobject]@{preset_operation='preset_set_scope';preset_inventory_binding=$binding;steps=@($scope);expected_count=0;final_expected_preset_count=1;final_expected_scope_hex=$scopeHex}
Assert-DiGiCoPresetAdapterPlan $scopeRequest $state
Assert-PresetAdapterTest ($scopeRequest.steps.Count-eq 1-and $scopeRequest.final_expected_scope_hex-eq $scopeHex) 'Set scope must be a real retained setter.'

$script:mockRaw='00000000';$script:mockOutput='00000000';$script:mockStored='0000803f';$script:mockContentHash='cc'*32
function Read-NativeControlBinding($Step){return [pscustomobject]@{raw_type=0;value_raw=(ConvertFrom-DiGiCoPresetAdapterHex $script:mockRaw 4);output_raw=(ConvertFrom-DiGiCoPresetAdapterHex $script:mockOutput 4)}}
function Read-DiGiCoPresetContent([long]$Pointer,[string]$TextEncoding){return [pscustomobject]@{controller_content_sha256=$script:mockContentHash;channel_snapshot_count=1;channels=@([pscustomobject]@{channel_id=0x101;modules=@([pscustomobject]@{type_id_raw=12;controllers=@([pscustomobject]@{control_id_hex='220c000101';value_raw_hex=$script:mockStored})})})}}
$recall=New-AdapterStep 'recall_input_preset' 1 0xC8 0 ([BitConverter]::GetBytes([int32]0x100))
$recall|Add-Member target_channel_id 0x100
$check=[pscustomobject]@{id='220c000001';control_contract=[pscustomobject]@{};before_raw_hex='00000000';expected_raw_hex='0000803f';source_control_id_hex='220c000101'}
$recall|Add-Member processing_checks @($check)
Assert-DiGiCoPresetAdapterChecks $recall $state $scopeHex
Assert-PresetAdapterTest (-not (Test-DiGiCoPresetAdapterProcessing $recall)) 'Recall was verified while live processing differed.'
$script:mockRaw='0000803f'
Assert-PresetAdapterTest (-not (Test-DiGiCoPresetAdapterProcessing $recall)) 'Recall ignored differing output processing.'
$script:mockOutput='0000803f'
Assert-PresetAdapterTest (Test-DiGiCoPresetAdapterProcessing $recall) 'Recall rejected equal stored/current/output values.'
$script:mockRaw='00000000';$script:mockOutput='00000000'
Assert-PresetAdapterThrows {Assert-DiGiCoPresetAdapterChecks $recall $state ('00'*32)} 'Recall accepted verification outside selected scope.'
$script:mockStored='0000003f'
Assert-PresetAdapterThrows {Assert-DiGiCoPresetAdapterChecks $recall $state $scopeHex} 'Recall accepted changed stored processing bytes.'
$script:mockStored='0000803f'

$protected=Read-DiGiCoPresetAdapterProtectedContent $state
Assert-DiGiCoPresetAdapterProtectedContent $protected $state
$script:mockContentHash='dd'*32
Assert-PresetAdapterThrows {Assert-DiGiCoPresetAdapterProtectedContent $protected $state} 'Adapter accepted changed original Preset content.'
Write-Output 'Native preset adapter synthetic contract tests passed.'

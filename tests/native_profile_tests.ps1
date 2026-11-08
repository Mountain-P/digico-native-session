# Pure profile selection/contract checks. No target process or network access.
[CmdletBinding()]
param([string]$ProfileModulePath='')
$ErrorActionPreference='Stop'
if(-not $ProfileModulePath){$ProfileModulePath=Join-Path (Split-Path $PSScriptRoot -Parent) 'skills/digico-native-session/scripts/native_profile.ps1'}
$SkillRoot=Split-Path (Split-Path $ProfileModulePath -Parent) -Parent
. ([scriptblock]::Create([IO.File]::ReadAllText($ProfileModulePath)))
$known='535097f38bb459e6e299b5da50b62fa811511d92af6d7215a13d703a416f177d'
function Reject([scriptblock]$Action){$rejected=$false;try{& $Action}catch{$rejected=$true};if(-not $rejected){throw 'Expected profile rejection.'}}
$null=Set-NativeBuildProfile -ExecutableSha256 $known
Assert-NativeBuildFeature 'inventory'
Assert-NativeBuildContract ([pscustomobject]@{build_profile='quantum1-v2126';executable_sha256=$known})
Assert-NativeBuildContract @{build_profile='quantum1-v2126';executable_sha256=$known}
Reject {Assert-NativeBuildContract ([pscustomobject]@{build_profile='quantum5-unverified';executable_sha256=$known})}
Reject {Assert-NativeBuildContract ([pscustomobject]@{executable_sha256=$known})}
Reject {Set-NativeBuildProfile -ExecutableSha256 ('0'*64)}
Reject {Get-NativeBuildProfile}
$null=Set-NativeBuildProfile -ExecutableSha256 $known
if((Get-NativeBuildProfile).layouts.controller.parent-ne 0x44){throw 'Wrong V2126 controller parent.'}
$candidate=Get-Content -LiteralPath (Join-Path $SkillRoot 'assets/build-profiles/quantum1-v2126.json') -Raw -Encoding UTF8|ConvertFrom-Json
$candidate.id='synthetic-readonly-probe';$candidate.executable.sha256='1'*64
$candidate.validation.stage='static_candidate';$candidate.validation.offline_runtime_verified=$false
$badLayout=($candidate|ConvertTo-Json -Depth 30|ConvertFrom-Json)
$badLayout.layouts.definition.seed=$null
Reject {Assert-NativeBuildLayouts $badLayout}
$badLayout=($candidate|ConvertTo-Json -Depth 30|ConvertFrom-Json)
$badLayout.layouts.definition.seed='46'
Reject {Assert-NativeBuildLayouts $badLayout}
$badLayout=($candidate|ConvertTo-Json -Depth 30|ConvertFrom-Json)
$badLayout.layouts.controller.parent=$true
Reject {Assert-NativeBuildLayouts $badLayout}
$path=Join-Path ([IO.Path]::GetTempPath()) ('native-profile-test-'+[Guid]::NewGuid().ToString('N')+'.json')
try {
    $candidate|ConvertTo-Json -Depth 30|Set-Content -LiteralPath $path -Encoding UTF8
    Reject {Set-NativeBuildProbeProfile -Path $path -ExecutableSha256 $known}
    $probe=Set-NativeBuildProbeProfile -Path $path -ExecutableSha256 ('1'*64)
    if($probe.access_mode-cne 'readonly_probe'){throw 'Probe access mode was not explicit.'}
    Assert-NativeBuildFeature 'inventory'
    Reject {Assert-NativeBuildFeature 'control_write'}
    Reject {Assert-NativeBuildFeature 'session_files'}
    Reject {Assert-NativeBuildContract ([pscustomobject]@{build_profile=$probe.id;executable_sha256=('1'*64)})}
    . ([scriptblock]::Create([IO.File]::ReadAllText((Join-Path $SkillRoot 'scripts/native_controls.ps1'))))
    $reader=[pscustomobject]@{};$base=0x400000;$ascii=[Text.Encoding]::ASCII;$nameEncoding=$ascii
    $readContext=New-NativeControlContext
    if($readContext.Base-ne $base){throw 'Inventory context incorrectly requires write admission.'}
    Reject {Assert-NativeControlValue $null $null}
    Reject {Set-NativeBuildProfile -ExecutableSha256 ('1'*64)}
}finally{Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue}
$null=Set-NativeBuildProfile -ExecutableSha256 $known
Write-Output 'Native profile selection and rejection tests passed.'

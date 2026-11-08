# Copied-byte definition vectors; fake reader only, no process/network/Session.
[CmdletBinding()]
param([string]$SkillRoot='',[string]$ProfileModulePath='',[string]$InventoryModulePath='')
$ErrorActionPreference='Stop'
if(-not $SkillRoot){$SkillRoot=Join-Path (Split-Path $PSScriptRoot -Parent) 'skills/digico-native-session'}
if(-not $ProfileModulePath){$ProfileModulePath=Join-Path $SkillRoot 'scripts/native_profile.ps1'}
if(-not $InventoryModulePath){$InventoryModulePath=Join-Path $SkillRoot 'scripts/native_inventory.ps1'}
. ([scriptblock]::Create([IO.File]::ReadAllText($ProfileModulePath)))
. ([scriptblock]::Create([IO.File]::ReadAllText($InventoryModulePath)))
$source=Get-Content -LiteralPath (Join-Path $SkillRoot 'assets/build-profiles/quantum1-v2126.json') -Raw -Encoding UTF8
$scratch=Join-Path ([IO.Path]::GetTempPath()) ('native-definition-test-'+[Guid]::NewGuid().ToString('N')+'.json')
try {
    foreach($size in @(55,58,59)){
        $record=$source|ConvertFrom-Json
        $record.id='synthetic-definition-probe';$record.executable.sha256='4'*64
        $record.validation.offline_runtime_verified=$false;$record.validation.stage='static_candidate'
        $layout=$record.layouts.definition;$layout.size=$size
        if($size-eq 55){$layout.colour=$null;$layout.seed=46;$layout.lut=50;$layout.data_flags=54}
        elseif($size-eq 59){$layout.data_flags=58}
        $record|ConvertTo-Json -Depth 30|Set-Content -LiteralPath $scratch -Encoding UTF8
        $null=Set-NativeBuildProbeProfile -Path $scratch -ExecutableSha256 ('4'*64)
        $raw=New-Object byte[] $size;$raw[0]=1
        [Array]::Copy([BitConverter]::GetBytes([single]3.5),0,$raw,1,4)
        $raw[5]=2;$raw[6]=5;$raw[7]=3;$raw[8]=115;$raw[9]=121;$raw[10]=110
        $raw[39]=2;$raw[40]=72;$raw[41]=122
        if($null-ne $layout.colour){[Array]::Copy([BitConverter]::GetBytes([int32]-12),0,$raw,[int]$layout.colour,4)}
        [Array]::Copy([BitConverter]::GetBytes([uint32]0x11223344),0,$raw,[int]$layout.seed,4)
        [Array]::Copy([BitConverter]::GetBytes([uint32]0x55667788),0,$raw,[int]$layout.lut,4)
        if($null-ne $layout.data_flags){$raw[[int]$layout.data_flags]=0xa5}
        $fake=[pscustomobject]@{Data=$raw}
        $fake|Add-Member -MemberType ScriptMethod -Name Read -Value {
            param([long]$Address,[int]$Length)
            if($Address-ne 0x1000-or $Length-ne $this.Data.Length){throw 'Unexpected fake definition read.'}
            return ,([byte[]]$this.Data.Clone())
        }
        $encoding=[Text.Encoding]::ASCII
        $context=@{Reader=$fake;Base=0x400000L;End=0x500000L;Ascii=$encoding;Encoding=$encoding;Definitions=@{};IncludeResponseTables=$false;CurrentObject=0L;CurrentId='';Identities=[Collections.Generic.List[object]]::new()}
        $entry=Get-NativeInventoryDefinition $context 0x1000
        if($size-eq 55){if($null-ne $entry.colour){throw 'Absent Colour was decoded at offset zero.'}}
        elseif($entry.colour-ne -12){throw 'Present Colour was not preserved.'}
        if($entry.response_seed_pointer-ne 0x11223344-or $entry.response_lut_pointer-ne 0x55667788){throw 'Shifted seed/LUT pointers were decoded incorrectly.'}
        if($null-ne $layout.data_flags-and $entry.data_flags-ne 0xa5){throw 'Trailing DataFlags was not preserved.'}
        if($entry.raw_hex-cne (ConvertTo-NativeInventoryHex $raw)-or $context.Identities[0].length-ne $size){throw 'Definition record was truncated or witnessed at the wrong size.'}
    }
}finally{Remove-Item -LiteralPath $scratch -Force -ErrorAction SilentlyContinue}
Write-Output 'Native 55/58/59-byte definition vectors passed.'

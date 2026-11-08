# Build identity and reviewed constants. Dot-source before native RPM modules;
# the runner supplies the SHA-256 it computed from the actual executable file.
$nativeProfileSkillRoot=Get-Variable -Name SkillRoot -ValueOnly -ErrorAction SilentlyContinue
if($nativeProfileSkillRoot){$script:NativeProfileDirectory=Join-Path $nativeProfileSkillRoot 'assets/build-profiles'}
elseif($PSScriptRoot){$script:NativeProfileDirectory=Join-Path (Split-Path $PSScriptRoot -Parent) 'assets/build-profiles'}
else{throw 'Build profile loading requires the verified SkillRoot.'}

function Test-NativeBuildInteger($Value){
    return ($Value-is [byte]-or $Value-is [sbyte]-or $Value-is [int16]-or $Value-is [uint16]-or
        $Value-is [int32]-or $Value-is [uint32]-or $Value-is [int64]-or $Value-is [uint64])
}
function Read-NativeBuildProfileRecord {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$Path)
    $record=Get-Content -LiteralPath $Path -Raw -Encoding UTF8|ConvertFrom-Json
    if(-not (Test-NativeBuildInteger $record.schema_version)-or $record.schema_version-ne 1-or [string]$record.id-cnotmatch '^[a-z0-9][a-z0-9-]{0,63}$'-or [string]$record.executable.sha256-cnotmatch '^[0-9a-f]{64}$'-or
        -not (Test-NativeBuildInteger $record.executable.pointer_size)-or $record.executable.pointer_size-ne 4-or
        -not (Test-NativeBuildInteger $record.executable.image_size)-or $record.executable.image_size-le 0-or $record.executable.image_size-gt 0xffffffffL){throw 'Invalid native build profile record.'}
    # Reading static metadata does not select/admit it for RPM or commands.
    return $record
}
function Set-NativeBuildProfile {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$ExecutableSha256,[string]$BuildProfile='')
    $script:NativeBuildProfile=$null
    if($ExecutableSha256-cnotmatch '^[0-9a-f]{64}$'){throw 'Executable SHA-256 must be exact lowercase hexadecimal.'}
    $nativeProfileMatches=@()
    foreach($path in Get-ChildItem -LiteralPath $script:NativeProfileDirectory -Filter '*.json' -File){
        $record=Read-NativeBuildProfileRecord $path.FullName
        if($record.executable.sha256-ceq $ExecutableSha256){$nativeProfileMatches+=,$record}
    }
    if($nativeProfileMatches.Count-ne 1){throw 'Unknown or ambiguous native executable SHA-256; no build profile selected.'}
    $profile=$nativeProfileMatches[0]
    if($BuildProfile-and $profile.id-cne $BuildProfile){throw 'Requested build profile differs from executable SHA-256.'}
    if($profile.implementation-cnotin @('quantum1-v2126-layout-v1','quantum-layout-v2')-or $profile.validation.offline_runtime_verified-isnot [bool]-or -not $profile.validation.offline_runtime_verified){throw 'Build profile is record-only; native execution is not admitted.'}
    Assert-NativeBuildLayouts $profile
    Assert-NativeControlWritePolicy $profile
    $profile|Add-Member -NotePropertyName access_mode -NotePropertyValue 'verified' -Force
    $script:NativeBuildProfile=$profile
    return $profile
}
function Assert-NativeControlWritePolicy($Profile){
    $property=$Profile.PSObject.Properties['control_write_allowlist']
    if($null-eq $property-or $null-eq $property.Value){return}
    if($property.Value-isnot [Array]-or $property.Value.Count-gt 4096){throw 'Native control write allowlist must be a bounded array or null.'}
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $fields=@('id_prefix_hex','raw_type','definition_rva','module_vmt_rva','process_output_rva')
    foreach($rule in $property.Value){
        foreach($field in $fields){if($null-eq $rule.PSObject.Properties[$field]){throw 'Missing native control allowlist field.'}}
        foreach($field in $rule.PSObject.Properties.Name){if($field-cnotin ($fields+@('evidence'))){throw 'Unknown native control allowlist field.'}}
        if($rule.id_prefix_hex-isnot [string]-or $rule.id_prefix_hex-cnotmatch '^[0-9a-f]{6}$'-or -not (Test-NativeBuildInteger $rule.raw_type)-or $rule.raw_type-lt 0-or $rule.raw_type-gt 4){throw 'Invalid native control allowlist identity/datatype.'}
        foreach($field in @('definition_rva','module_vmt_rva','process_output_rva')){
            if(-not (Test-NativeBuildInteger $rule.$field)-or $rule.$field-le 0-or $rule.$field-ge $Profile.executable.image_size){throw 'Invalid native control allowlist RVA.'}
        }
        $key=(@($fields|ForEach-Object {[string]$rule.$_})-join ':')
        if(-not $seen.Add($key)){throw 'Duplicate native control allowlist rule.'}
    }
}
function Assert-NativeBuildLayouts($Profile){
    $required=@{
        controller=@{id=5;definition=4;parent=4;value=4;target=4;output=4;audio_id=8;restore=4}
        console_list=@{parent=4};module=@{id=5;definition=4;n_signals=4}
        channel=@{id=2;logical=2;name=32;stereo_mode=1;input_route=4;alt_input_route=4;key_route=4;buss_routes=32;output_sockets=4}
        definition=@{data_type=1;default=4;band=1;parameter=1;name=32;units=3;seed=4;lut=4}
    }
    foreach($name in $required.Keys){
        $layoutProperty=$Profile.layouts.PSObject.Properties[$name]
        if($null-eq $layoutProperty){throw ('Missing native layout '+$name)}
        $layout=$layoutProperty.Value
        if(-not (Test-NativeBuildInteger $layout.size)-or $layout.size-lt 4-or $layout.size-gt 1048576){throw ('Invalid native layout size '+$name)}
        foreach($field in $required[$name].Keys){
            $property=$layout.PSObject.Properties[$field]
            if($null-eq $property-or -not (Test-NativeBuildInteger $property.Value)-or $property.Value-lt 0-or $property.Value-gt $layout.size-or ($property.Value+$required[$name][$field])-gt $layout.size){throw ('Invalid native layout field '+$name+'.'+$field)}
        }
    }
    $colour=$Profile.layouts.definition.PSObject.Properties['colour']
    if($null-ne $colour-and $null-ne $colour.Value-and (-not (Test-NativeBuildInteger $colour.Value)-or $colour.Value-lt 0-or $colour.Value-gt $Profile.layouts.definition.size-or ($colour.Value+4)-gt $Profile.layouts.definition.size)){throw 'Invalid native definition Colour window.'}
    $dataFlags=$Profile.layouts.definition.PSObject.Properties['data_flags']
    if($null-ne $dataFlags-and $null-ne $dataFlags.Value-and (-not (Test-NativeBuildInteger $dataFlags.Value)-or $dataFlags.Value-lt 0-or $dataFlags.Value-ge $Profile.layouts.definition.size)){throw 'Invalid native definition DataFlags window.'}
    foreach($name in @('current_response_seed','current_response_lut')){
        $slot=$Profile.layouts.controller.PSObject.Properties[$name]
        if($null-ne $slot-and $null-ne $slot.Value-and (-not (Test-NativeBuildInteger $slot.Value)-or $slot.Value-lt 0-or $slot.Value-gt $Profile.layouts.controller.size-or ($slot.Value+4)-gt $Profile.layouts.controller.size)){throw ('Invalid native controller response pointer window: '+$name)}
    }
}
function Set-NativeBuildProbeProfile {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$Path,[Parameter(Mandatory=$true)][string]$ExecutableSha256)
    $script:NativeBuildProfile=$null
    $record=Read-NativeBuildProfileRecord $Path
    if($ExecutableSha256-cnotmatch '^[0-9a-f]{64}$'-or $record.executable.sha256-cne $ExecutableSha256){throw 'Read-only probe profile differs from actual executable SHA-256.'}
    Assert-NativeBuildLayouts $record
    # Private laboratory admission is deliberately inventory-only. Preserve the
    # evidence stage; this cannot compile/write controls, routes, Cue or files.
    $record.features=[pscustomobject]@{inventory=$true;control_write=$false;routes=$false;meters=$false;presets=$false;structure=$false;transfer=$false;snapshots=$false;template=$false;session_files=$false;inspect=$false;status=$false}
    $record|Add-Member -NotePropertyName access_mode -NotePropertyValue 'readonly_probe' -Force
    $script:NativeBuildProfile=$record
    return $record
}
function Get-NativeBuildProfile {
    $selected=Get-Variable -Name NativeBuildProfile -Scope Script -ErrorAction SilentlyContinue
    if($null-eq $selected-or $null-eq $selected.Value){throw 'Select an exact executable build profile before native access.'}
    return $selected.Value
}
function Assert-NativeBuildFeature([string]$Feature){
    $profile=Get-NativeBuildProfile
    $value=$profile.features.PSObject.Properties[$Feature]
    if($null-eq $value-or $value.Value-isnot [bool]-or -not $value.Value){throw ('Selected build does not support '+$Feature)}
}
function Get-NativeBuildIdentity {
    $profile=Get-NativeBuildProfile
    return [pscustomobject]@{build_profile=$profile.id;executable_sha256=$profile.executable.sha256}
}
function Assert-NativeBuildContract($Contract){
    $profile=Get-NativeBuildProfile
    if($profile.access_mode-cne 'verified'){throw 'Record-only probe profiles cannot execute native contracts.'}
    if($null-eq $Contract){throw 'Native contract has no build identity.'}
    if($Contract-is [Collections.IDictionary]){
        if(-not $Contract.Contains('build_profile')-or -not $Contract.Contains('executable_sha256')){throw 'Native contract has no build identity.'}
    }elseif($null-eq $Contract.PSObject.Properties['build_profile']-or $null-eq $Contract.PSObject.Properties['executable_sha256']){throw 'Native contract has no build identity.'}
    if($Contract.build_profile-cne $profile.id-or $Contract.executable_sha256-cne $profile.executable.sha256){throw 'Native contract build profile/hash differs from selected executable.'}
}

# Preset request adapter. Dot-source functions only; the existing runner supplies
# hash-gated RPM, Frame, Read-State/Fingerprint and native connection helpers.
function ConvertFrom-DiGiCoPresetAdapterHex([string]$Text,[int]$Bytes=-1) {
    if($Text-notmatch '^(?:[0-9a-fA-F]{2})*$'-or ($Bytes-ge 0-and $Text.Length-ne 2*$Bytes)){throw 'Invalid Preset adapter raw bytes.'}
    $raw=New-Object byte[] ($Text.Length/2)
    for($i=0;$i-lt $raw.Length;$i++){$raw[$i]=[Convert]::ToByte($Text.Substring(2*$i,2),16)}
    return ,$raw
}
function Get-DiGiCoPresetAdapterFrame($Step) {
    Assert-DiGiCoPresetStepContract $Step
    $id=ConvertFrom-DiGiCoPresetAdapterHex $Step.control_id_hex 5
    $body=ConvertFrom-DiGiCoPresetAdapterHex $Step.body_hex
    $frame=Frame ([int]$Step.category) ([BitConverter]::ToUInt16($id,3)) ([BitConverter]::ToUInt16($id,0)) ([int]$Step.list_index) $body
    $given=@($Step.frames)
    if($given.Count-ne 1-or (ConvertTo-DiGiCoPresetHex $frame)-cne $given[0]){throw 'Compiled Preset frame differs from independently validated native fields.'}
    return ,$frame
}
function Assert-DiGiCoPresetAdapterBinding($Request,$State) {
    $binding=$Request.preset_inventory_binding
    if($null-eq $binding-or $State.preset_count-ne $binding.preset_count-or $State.list_pointer-ne $binding.list_pointer-or $State.metadata_sha256-cne $binding.metadata_sha256-or $State.scope_hex-cne $binding.scope_hex-or $State.current_session_path_hex-cne $binding.current_session_path_hex){throw 'Fresh Preset inventory/path/scope differs from compiled --presets; no operation was sent.'}
    if($null-ne $binding.selected_index){
        $index=Int $binding.selected_index 0 ($State.preset_count-1) 'selected preset index'
        if($State.presets[$index].pointer-ne $binding.selected_pointer){throw 'Selected Preset pointer changed since inventory.'}
    }
}
function Assert-DiGiCoPresetAdapterPlan($Request,$State) {
    $signatures=@{
        preset_capture=@('store_input_preset','store_input_preset,rename_preset')
        preset_rename=@('rename_preset');preset_update=@('update_input_preset')
        preset_recall=@('set_preset_recall_scope,recall_input_preset,restore_preset_recall_scope')
        preset_set_scope=@('set_preset_recall_scope');preset_lock=@('lock_preset');preset_delete=@('delete_preset')
    }
    $op=[string]$Request.preset_operation;$steps=@($Request.steps)
    if(-not $signatures.ContainsKey($op)-or $signatures[$op]-notcontains (@($steps|ForEach-Object {$_.op})-join ',')){throw 'Compiled Preset high-level operation/step sequence differs.'}
    $count=[int]$State.preset_count
    foreach($step in $steps){
        [void](Get-DiGiCoPresetAdapterFrame $step)
        if($step.expected_count_after-ne $Request.expected_count){throw 'Preset plan must preserve Cue count.'}
        if($step.op-eq 'store_input_preset'){
            if($step.group_name_hex-notmatch '^(?:[0-9a-fA-F]{2}){1,31}$'-or @($State.presets|Where-Object {$_.group_name_hex-eq $step.group_name_hex}).Count-ne 0){throw 'Capture requires a new nonempty unique Preset group.'}
            $null=Read-DiGiCoPresetInputChannel ([int]$step.list_index-band 65535);$count++
        } elseif($step.op-eq 'delete_preset'){$count--}
        if($step.op-eq 'rename_preset'-and $op-eq 'preset_capture'-and $step.list_index-ne $State.preset_count){throw 'Capture rename must select its appended Preset.'}
        if($op-ne 'preset_capture'-and $step.op-notin @('set_preset_recall_scope','restore_preset_recall_scope')){
            if($step.list_index-ne $Request.preset_inventory_binding.selected_index){throw 'Preset step index differs from selected binding.'}
        }
    }
    if($count-lt 0-or $count-gt 10000-or $count-ne $Request.final_expected_preset_count){throw 'Compiled Preset count transition differs.'}
    if($op-eq 'preset_recall'-and $steps[-1].expected_scope_hex-cne $State.scope_hex){throw 'Recall must restore the exact original scope.'}
    $expectedScope=$State.scope_hex
    if($op-eq 'preset_set_scope'){$expectedScope=$steps[-1].expected_scope_hex}
    if($Request.final_expected_scope_hex-cne $expectedScope){throw 'Compiled final Preset scope differs.'}
}
function Read-DiGiCoPresetAdapterProtectedContent($State,[int]$ExcludeIndex=-1) {
    $rows=[Collections.Generic.List[object]]::new()
    foreach($entry in $State.presets){
        if($entry.index-eq $ExcludeIndex){continue}
        $content=Read-DiGiCoPresetContent $entry.pointer $State.text_encoding
        $rows.Add([pscustomobject]@{pointer=$entry.pointer;controller_content_sha256=$content.controller_content_sha256})
    }
    return ,$rows.ToArray()
}
function Assert-DiGiCoPresetAdapterProtectedContent($Protected,$State) {
    foreach($original in $Protected){
        $matches=@($State.presets|Where-Object {$_.pointer-eq $original.pointer})
        if($matches.Count-ne 1){throw 'An unrelated original Preset disappeared or changed identity.'}
        $content=Read-DiGiCoPresetContent $matches[0].pointer $State.text_encoding
        if($content.controller_content_sha256-cne $original.controller_content_sha256){throw 'An unrelated original Preset processing payload changed.'}
    }
}
function Assert-DiGiCoPresetAdapterChecks($Step,$State,[string]$ScopeHex='') {
    $checks=@();if(Has-DiGiCoPresetProperty $Step 'processing_checks'){$checks=@($Step.processing_checks)}
    if($Step.op-eq 'recall_input_preset'-and $checks.Count-eq 0){throw 'Recall requires an explicit known stored/live processing subset.'}
    if($checks.Count-gt 64){throw 'Processing verification subset exceeds local bound.'}
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $content=$null;$scope=$null
    if($Step.op-eq 'recall_input_preset'){
        $content=Read-DiGiCoPresetContent $State.presets[[int]$Step.list_index].pointer $State.text_encoding
        $scope=ConvertFrom-DiGiCoPresetAdapterHex $ScopeHex 32
    }
    foreach($check in $checks){
        $id=ConvertFrom-DiGiCoPresetAdapterHex $check.id 5
        if(-not $seen.Add([string]$check.id)-or [BitConverter]::ToUInt16($id,3)-lt 0x100-or [BitConverter]::ToUInt16($id,3)-gt 0x1FF){throw 'Invalid/duplicated ordinary-input processing verification ID.'}
        $expected=ConvertFrom-DiGiCoPresetAdapterHex $check.expected_raw_hex 4
        $before=ConvertFrom-DiGiCoPresetAdapterHex $check.before_raw_hex 4
        $value=[BitConverter]::ToSingle($expected,0)
        if([single]::IsNaN($value)-or [single]::IsInfinity($value)){throw 'Nonfinite Preset processing expectation.'}
        $bound=Read-NativeControlBinding $check
        if($bound.raw_type-notin @(0,1)-or (ConvertTo-DiGiCoPresetHex $bound.value_raw)-cne $check.before_raw_hex){throw 'Processing identity/type/current value differs from --inventory; no Preset operation was sent.'}
        $channelId=[int]$Step.list_index-band 65535
        if($Step.op-eq 'update_input_preset'){$channelId=[BitConverter]::ToUInt16((ConvertFrom-DiGiCoPresetAdapterHex $Step.body_hex 4),0)}
        if($Step.op-eq 'recall_input_preset'){$channelId=[int]$Step.target_channel_id}
        if([BitConverter]::ToUInt16($id,3)-ne $channelId){throw 'Processing verifier belongs to another input channel.'}
        if($Step.op-eq 'recall_input_preset'){
            if($content.channel_snapshot_count-ne 1){throw 'Recall requires one stored input channel.'}
            $source=[byte[]]($id[0],$id[1],$id[2],[byte]($content.channels[0].channel_id-band 255),[byte](($content.channels[0].channel_id-shr 8)-band 255))
            $sourceHex=ConvertTo-DiGiCoPresetHex $source
            if($check.source_control_id_hex-cne $sourceHex){throw 'Recall source-to-target control mapping differs.'}
            $found=[Collections.Generic.List[object]]::new()
            foreach($module in $content.channels[0].modules){
                $type=[long]$module.type_id_raw
                if($type-lt 0-or $type-gt 255-or ($scope[[int]($type-shr 3)]-band (1-shl ([int]$type-band 7)))-eq 0){continue}
                foreach($controller in $module.controllers){if($controller.control_id_hex-ceq $sourceHex){$found.Add($controller)}}
            }
            if($found.Count-ne 1-or $found[0].value_raw_hex-cne $check.expected_raw_hex){throw 'Stored Preset processing expectation is unknown, outside scope or changed.'}
        } else {
            if($check.expected_raw_hex-cne $check.before_raw_hex){throw 'Capture/update must verify the fresh source input value.'}
            $wanted=@($Step.expected_controller_values|Where-Object {$_.control_id_hex-ceq $check.id-and $_.value_raw_hex-ceq $check.expected_raw_hex})
            if($wanted.Count-ne 1){throw 'Stored and live processing verifier subsets differ.'}
        }
    }
    if($Step.op-in @('store_input_preset','update_input_preset')-and $checks.Count-gt 0-and @($Step.expected_controller_values).Count-ne $checks.Count){throw 'Stored/live processing verifier counts differ.'}
}
function Test-DiGiCoPresetAdapterProcessing($Step) {
    foreach($check in $Step.processing_checks){
        $bound=Read-NativeControlBinding $check
        $wanted=[BitConverter]::ToSingle((ConvertFrom-DiGiCoPresetAdapterHex $check.expected_raw_hex 4),0)
        $tolerance=[math]::Max([math]::Abs([double]$wanted)*[math]::Pow(2,-23),[math]::Pow(2,-149))
        foreach($raw in @($bound.value_raw,$bound.output_raw)){
            $value=[BitConverter]::ToSingle([byte[]]$raw,0)
            if([single]::IsNaN($value)-or [single]::IsInfinity($value)-or [math]::Abs([double]$value-$wanted)-gt $tolerance){return $false}
        }
    }
    return $true
}
function Invoke-DiGiCoPresetRequest {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)]$Request,[Parameter(Mandatory=$true)][Collections.IDictionary]$Result,
          [Parameter(Mandatory=$true)][AllowEmptyCollection()]$Verified)
    . ([scriptblock]::Create([IO.File]::ReadAllText((Join-Path $SkillRoot 'scripts\native_presets.ps1'))))
    $cueBefore=Read-State
    if($Request.mode-eq 'presets'){
        $indices=@();if(Has $Request 'content_indices'){$indices=@($Request.content_indices|ForEach-Object {Int $_ 0 9999 'Preset content index'})}
        if($indices.Count-gt 64){throw 'Preset content selection exceeds the local64-index bound.'}
        $Result['presets']=Read-DiGiCoNativePresets -TextEncoding $Request.encoding -ContentIndices $indices
        $Result['after']=Read-State
        if((Fingerprint $Result['after'])-cne (Fingerprint $cueBefore)){throw 'Cue fields changed during read-only Preset inventory.'}
        return
    }
    if($Request.mode-ne 'preset_execute'){throw 'Unknown Preset adapter mode.'}
    if(-not (Test-Path -LiteralPath (Join-Path ([IO.Path]::GetDirectoryName($ExecutablePath)) 'OffLine.ini'))){throw 'Verified offline-editor marker is missing.'}
    if($cueBefore.fps-ne 25-or $cueBefore.snapshot_count-ne (Int $Request.expected_count 0 2000 'expected_count')){throw 'Preset expected Cue count/runtime FPS differs; no operation was sent.'}
    $worker=Read-SaveWorker;if($worker-and $worker.finished-eq 0){throw 'Native save worker is busy; no Preset operation was sent.'}
    Only-Loopback-Peers
    . ([scriptblock]::Create([IO.File]::ReadAllText((Join-Path $SkillRoot 'scripts\native_inventory.ps1'))))
    . ([scriptblock]::Create([IO.File]::ReadAllText((Join-Path $SkillRoot 'scripts\native_controls.ps1'))))
    $state=Read-DiGiCoNativePresets -TextEncoding $Request.encoding
    $Result['presets_before']=$state
    Assert-DiGiCoPresetAdapterBinding $Request $state
    Assert-DiGiCoPresetAdapterPlan $Request $state
    $exclude=-1
    if($Request.preset_operation-in @('preset_update','preset_delete')){$exclude=[int]$Request.preset_inventory_binding.selected_index}
    $protected=Read-DiGiCoPresetAdapterProtectedContent $state $exclude
    $checks={param($Old,$Now,$Step,$Expected) return (Test-DiGiCoPresetAdapterProcessing $Step)}
    foreach($step in $Request.steps){
        if($step.op-in @('store_input_preset','update_input_preset','recall_input_preset')){
            $selectedScope='';if($Request.preset_operation-eq 'preset_recall'){$selectedScope=$Request.steps[0].expected_scope_hex}
            Assert-DiGiCoPresetAdapterChecks $step $state $selectedScope
        }
        if($step.op-ne 'rename_preset'-or $Request.preset_operation-ne 'preset_capture'){
            $callback=$null;if($step.op-eq 'recall_input_preset'){$callback=$checks}
            $null=Assert-DiGiCoPresetEdit -Before $state -Step $step -ProcessingCheck $callback
        }
    }
    # Native connection is opened only after every static frame and presently
    # bindable object/value has passed preflight. Newly captured rename binds later.
    try {
        Connect-Native
        Assert-DiGiCoPresetAdapterBinding $Request (Read-DiGiCoNativePresets -TextEncoding $Request.encoding)
        foreach($step in $Request.steps){
            Only-Loopback-Peers
            if((Fingerprint (Read-State))-cne (Fingerprint $cueBefore)){throw 'Unexpected concurrent Cue changes before Preset operation.'}
            if($native.Age.ElapsedMilliseconds-ge 2000){Disconnect-Native;Connect-Native}
            $state=Read-DiGiCoNativePresets -TextEncoding $Request.encoding
            if($step.op-in @('store_input_preset','update_input_preset','recall_input_preset')){
                $selectedScope='';if($Request.preset_operation-eq 'preset_recall'){$selectedScope=$Request.steps[0].expected_scope_hex}
                Assert-DiGiCoPresetAdapterChecks $step $state $selectedScope
            }
            $callback=$null;if(Has-DiGiCoPresetProperty $step 'processing_checks'){$callback=$checks}
            $expected=Assert-DiGiCoPresetEdit -Before $state -Step $step -ProcessingCheck $callback
            $Result['indeterminate_operation']=$step
            Send-Bytes (Get-DiGiCoPresetAdapterFrame $step)
            $accepted=Wait-DiGiCoPresetEdit -Before $state -Step $step -Expected $expected -ProcessingCheck $callback
            $state=$accepted.state
            if((Fingerprint (Read-State))-cne (Fingerprint $cueBefore)){throw 'Cue fields changed during Preset operation.'}
            Assert-DiGiCoPresetAdapterProtectedContent $protected $state
            $Verified.Add([pscustomobject]@{op=$step.op;verified=$true;preset_count=$state.preset_count;scope_hex=$state.scope_hex;metadata_verified=$accepted.metadata_verified;stored_controller_subset_verified=$accepted.stored_controller_subset_verified;processing_verified=$accepted.processing_verified;audio_delivery_verified=$false})
            $Result.Remove('indeterminate_operation')
        }
        if($state.preset_count-ne $Request.final_expected_preset_count-or $state.scope_hex-cne $Request.final_expected_scope_hex){throw 'Preset request final count/scope differs.'}
        $Result['presets_after']=$state;$Result['after']=Read-State
        $Result['original_preset_contents_verified']=$true
        $Result['original_scope_restored']=($state.scope_hex-ceq $Result['presets_before'].scope_hex)
    } catch {
        $Result['recovery']='Read fresh Preset/Cue/control state before retry. Capture/delete and partial recall must not be replayed blindly; inspect global scope explicitly.'
        try{$Result['presets_after']=Read-DiGiCoNativePresets -TextEncoding $Request.encoding}catch{}
        throw
    } finally {
        if($native.Client){try{Disconnect-Native}catch{}}
    }
}

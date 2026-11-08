# Real Windows named-mutex/process tests, using only synthetic paths/bytes.
# No editor, target process, network, UI, ACL or privilege change is involved.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$LeaseModulePath)
$ErrorActionPreference='Stop'
if([Environment]::OSVersion.Platform-ne [PlatformID]::Win32NT){throw 'Run native lease tests on Windows.'}
$LeaseModulePath=[IO.Path]::GetFullPath($LeaseModulePath)
. ([scriptblock]::Create([IO.File]::ReadAllText($LeaseModulePath)))
$folder=Join-Path ([IO.Path]::GetTempPath()) ('native-lease-test-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($folder)
$target=Join-Path $folder 'SyntheticEditor.exe'
$otherTarget=Join-Path $folder 'OtherSyntheticEditor.exe'
[IO.File]::WriteAllBytes($target,[byte[]](1,2,3,4))
[IO.File]::WriteAllBytes($otherTarget,[byte[]](1,2,3,4))
$digest=(Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
$shellPath=(Get-Process -Id $PID).Path
$children=[Collections.Generic.List[object]]::new()
$keepers=[Collections.Generic.List[object]]::new()

function Assert-LeaseTest([bool]$Condition,[string]$Message){if(-not $Condition){throw $Message}}
function Reject-LeaseTest([scriptblock]$Action,[string]$Message){
    $rejected=$false;try{& $Action|Out-Null}catch{$rejected=$true}
    Assert-LeaseTest $rejected $Message
}
function Assert-LeaseTestBusy($Child,[string]$Message){
    $result=Complete-LeaseTestChild $Child
    Assert-LeaseTest (-not $result.acquired-and [string]$result.error-match 'lease busy') $Message
}
function Quote-LeaseTest([string]$Value){return "'"+$Value.Replace("'","''")+"'"}
function Start-LeaseTestChild([string]$Mode,[string]$TargetPath,[string]$Hash=$digest){
    $identity=[Guid]::NewGuid().ToString('N')
    $receipt=Join-Path $folder ($identity+'.json');$signal=Join-Path $folder ($identity+'.release')
    $body=@'
$ErrorActionPreference='Stop'
$module=__MODULE__;$target=__TARGET__;$hash=__HASH__;$mode=__MODE__
$receipt=__RECEIPT__;$signal=__SIGNAL__
. ([scriptblock]::Create([IO.File]::ReadAllText($module)))
function Receipt($Value){[IO.File]::WriteAllText($receipt,($Value|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))}
$lease=$null
try {
    $lease=Enter-NativeWriteLease -ExecutableSha256 $hash -ExecutablePath $target
    Receipt @{acquired=$true;abandoned=$lease.Abandoned;name=$lease.Name;phase='held';process_id=$PID}
    if($mode-ne 'probe'){
        $timer=[Diagnostics.Stopwatch]::StartNew()
        while(-not (Test-Path -LiteralPath $signal)){
            if($timer.ElapsedMilliseconds-gt 20000){throw 'Synthetic child release signal timed out.'}
            [Threading.Thread]::Sleep(20)
        }
        if($mode-eq 'throw'){throw 'Synthetic failure after acquiring lease.'}
    }
    Receipt @{acquired=$true;abandoned=$lease.Abandoned;name=$lease.Name;phase='completed';process_id=$PID}
}catch{
    Receipt @{acquired=($null-ne $lease);phase='caught';error=$_.Exception.Message;process_id=$PID}
}finally{
    if($null-ne $lease){Exit-NativeWriteLease -Lease $lease}
}
'@
    $replacements=@{
        '__MODULE__'=$LeaseModulePath;'__TARGET__'=$TargetPath;'__HASH__'=$Hash;
        '__MODE__'=$Mode;'__RECEIPT__'=$receipt;'__SIGNAL__'=$signal
    }
    foreach($entry in $replacements.GetEnumerator()){$body=$body.Replace($entry.Key,(Quote-LeaseTest $entry.Value))}
    $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($body))
    $info=[Diagnostics.ProcessStartInfo]::new()
    $info.FileName=$shellPath;$info.Arguments='-NoLogo -NoProfile -NonInteractive -EncodedCommand '+$encoded
    $info.UseShellExecute=$false;$info.CreateNoWindow=$true
    $info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
    $process=[Diagnostics.Process]::Start($info);$children.Add($process)
    return [pscustomobject]@{Process=$process;Receipt=$receipt;Signal=$signal}
}
function Wait-LeaseTestReceipt($Child){
    $timer=[Diagnostics.Stopwatch]::StartNew()
    while($timer.ElapsedMilliseconds-lt 12000){
        if(Test-Path -LiteralPath $Child.Receipt){
            try{return ([IO.File]::ReadAllText($Child.Receipt)|ConvertFrom-Json)}catch{}
        }
        if($Child.Process.HasExited){throw ('Synthetic child exited without a valid receipt: '+$Child.Process.StandardError.ReadToEnd())}
        [Threading.Thread]::Sleep(20)
    }
    throw 'Synthetic child did not return a bounded lease receipt.'
}
function Complete-LeaseTestChild($Child){
    if(-not $Child.Process.WaitForExit(12000)){throw 'Synthetic lease child exceeded its bounded test timeout.'}
    $stderr=$Child.Process.StandardError.ReadToEnd();$null=$Child.Process.StandardOutput.ReadToEnd()
    if($Child.Process.ExitCode-ne 0){throw ('Synthetic lease child failed: '+$stderr)}
    return ([IO.File]::ReadAllText($Child.Receipt)|ConvertFrom-Json)
}
function Retain-LeaseTestMutex([string]$Name){
    $created=$false;$keeper=[Threading.Mutex]::new($false,$Name,[ref]$created)
    Assert-LeaseTest (-not $created) 'Keeper unexpectedly created a different Global mutex.'
    $keepers.Add($keeper)
    return $keeper
}
function Invoke-LeaseTestRunspace([string]$Body,[object[]]$Arguments,[string]$ExpectedFailure){
    $runspace=[Management.Automation.Runspaces.RunspaceFactory]::CreateRunspace()
    $runspace.ThreadOptions=[Management.Automation.Runspaces.PSThreadOptions]::UseNewThread
    $runspace.Open();$pipeline=[Management.Automation.PowerShell]::Create();$pipeline.Runspace=$runspace
    try{
        [void]$pipeline.AddScript($Body)
        foreach($argument in $Arguments){[void]$pipeline.AddArgument($argument)}
        $pending=$pipeline.BeginInvoke()
        if(-not $pending.AsyncWaitHandle.WaitOne(12000)){$pipeline.Stop();throw 'Synthetic runspace lease call did not return.'}
        $result=@($pipeline.EndInvoke($pending))
        $details=@($pipeline.Streams.Error|ForEach-Object {
                [pscustomobject]@{type=$_.Exception.GetType().FullName;message=$_.Exception.Message;
                    inner_type=if($_.Exception.InnerException){$_.Exception.InnerException.GetType().FullName}else{$null};
                    inner_message=if($_.Exception.InnerException){$_.Exception.InnerException.Message}else{$null};
                    fqid=$_.FullyQualifiedErrorId;position=$_.InvocationInfo.PositionMessage}
        })
        $known=$false
        if($details.Count-eq 0-and $result.Count-eq 1-and $result[0].rejected-is [bool]-and $result[0].rejected-and
            $result[0].error_type-ceq 'System.Management.Automation.MethodInvocationException'-and
            $result[0].inner_type-ceq 'System.InvalidOperationException'){
            if($ExpectedFailure-ceq 'reentry'){
                $known=($result[0].fqid-ceq 'InvalidOperationException,Enter-NativeWriteLease'-and
                    $result[0].inner_message-ceq 'Native write lease busy: this process already holds or is acquiring this executable; reentry is refused.')
            }elseif($ExpectedFailure-ceq 'wrong_thread'){
                $known=($result[0].fqid-ceq 'InvalidOperationException,Exit-NativeWriteLease'-and
                    $result[0].inner_message-ceq 'Native write lease must be released on its acquiring PowerShell pipeline thread.')
            }
        }
        # Windows PowerShell retains HadErrors after a caught method exception,
        # even with an empty error stream. Accept only the exact expected typed
        # failure; unknown streams, types, messages and missing results still fail.
        if(-not $known){
            $diagnostic=[pscustomobject]@{expected_failure=$ExpectedFailure;had_errors=$pipeline.HadErrors;error_count=$details.Count;errors=$details;results=$result}
            throw ('Synthetic runspace returned unexpected errors: '+($diagnostic|ConvertTo-Json -Depth 8 -Compress))
        }
        return $result[0]
    }finally{$pipeline.Dispose();$runspace.Dispose()}
}

try {
    Reject-LeaseTest {Enter-NativeWriteLease -ExecutableSha256 'unknown' -ExecutablePath $target} 'Unknown SHA format was accepted.'
    Reject-LeaseTest {Enter-NativeWriteLease -ExecutableSha256 $digest -ExecutablePath 'relative.exe'} 'Relative executable identity was accepted.'
    $lease=Enter-NativeWriteLease -ExecutableSha256 $digest -ExecutablePath $target
    Assert-LeaseTest ($lease.Name-cmatch '^Global\\DiGiCo\.NativeWrite\.[0-9a-f]{64}$') 'Mutex name is not Global and hash-only.'
    Assert-LeaseTest (-not $lease.Name.Contains($folder)-and -not $lease.ReplayAuthorized) 'Lease leaked its path or authorized replay.'
    Reject-LeaseTest {Enter-NativeWriteLease -ExecutableSha256 $digest -ExecutablePath $target} 'Same-script/thread reentry was accepted.'
    Reject-LeaseTest {[DiGiCo.NativeWriteLeaseRegistryV1]::Acquire($digest,$target)} 'Process registry allowed direct same-thread Mutex reentry.'
    $reentryBody=@'
param($Module,$Hash,$Target)
. ([scriptblock]::Create([IO.File]::ReadAllText($Module)))
try{$l=Enter-NativeWriteLease -ExecutableSha256 $Hash -ExecutablePath $Target;try{[pscustomobject]@{rejected=$false;thread=[Threading.Thread]::CurrentThread.ManagedThreadId}}finally{Exit-NativeWriteLease -Lease $l}}
catch{[pscustomobject]@{rejected=$true;thread=[Threading.Thread]::CurrentThread.ManagedThreadId;error=$_.Exception.Message;error_type=$_.Exception.GetType().FullName;inner_type=if($_.Exception.InnerException){$_.Exception.InnerException.GetType().FullName}else{$null};inner_message=if($_.Exception.InnerException){$_.Exception.InnerException.Message}else{$null};fqid=$_.FullyQualifiedErrorId}}
'@
    $runspaceResult=Invoke-LeaseTestRunspace $reentryBody @($LeaseModulePath,$digest,$target) 'reentry'
    Assert-LeaseTest ($runspaceResult.rejected-and $runspaceResult.thread-ne $lease.OwnerThreadId-and $runspaceResult.error-match 'lease busy') 'Another runspace/thread bypassed process reentry protection.'
    $releaseBody=@'
param($Module,$Lease)
. ([scriptblock]::Create([IO.File]::ReadAllText($Module)))
try{Exit-NativeWriteLease -Lease $Lease;[pscustomobject]@{rejected=$false}}
catch{[pscustomobject]@{rejected=$true;error=$_.Exception.Message;error_type=$_.Exception.GetType().FullName;inner_type=if($_.Exception.InnerException){$_.Exception.InnerException.GetType().FullName}else{$null};inner_message=if($_.Exception.InnerException){$_.Exception.InnerException.Message}else{$null};fqid=$_.FullyQualifiedErrorId}}
'@
    $wrongThread=Invoke-LeaseTestRunspace $releaseBody @($LeaseModulePath,$lease) 'wrong_thread'
    Assert-LeaseTest ($wrongThread.rejected-and -not $lease.IsReleased-and $wrongThread.error-match 'pipeline thread') 'Wrong-thread release disposed the live owner lease.'
    $contender=Start-LeaseTestChild 'probe' $target
    Assert-LeaseTestBusy $contender 'A real second PowerShell process acquired a held writer lease.'
    $variant=Start-LeaseTestChild 'probe' ($target.ToUpperInvariant().Replace('\','/')) ('ab'*32)
    Assert-LeaseTestBusy $variant 'Path spelling or a different build hash bypassed the same executable lock.'
    $extended=Start-LeaseTestChild 'probe' ('\\?\'+$target)
    Assert-LeaseTestBusy $extended 'Extended Windows path spelling bypassed the same executable lock.'
    $independent=Start-LeaseTestChild 'probe' $otherTarget
    Assert-LeaseTest (Complete-LeaseTestChild $independent).acquired 'Unrelated executable paths were globally serialized.'
    Exit-NativeWriteLease -Lease $lease
    Exit-NativeWriteLease -Lease $lease
    $afterRelease=Start-LeaseTestChild 'probe' $target
    Assert-LeaseTest (Complete-LeaseTestChild $afterRelease).acquired 'Normal finally-style release did not unlock the executable.'

    # A child owns the lock independently of this caller/host-side lock. A new
    # writer cannot overlap it while the existing guest worker is still alive.
    $throwing=Start-LeaseTestChild 'throw' $target
    $held=Wait-LeaseTestReceipt $throwing
    Assert-LeaseTest $held.acquired 'Exception-cleanup child could not acquire its lease.'
    $null=Retain-LeaseTestMutex $held.name
    $whileAlive=Start-LeaseTestChild 'probe' $target
    Assert-LeaseTestBusy $whileAlive 'A second writer overlapped a surviving guest worker.'
    [IO.File]::WriteAllText($throwing.Signal,'release')
    $failed=Complete-LeaseTestChild $throwing
    Assert-LeaseTest ($failed.acquired-and $failed.phase-eq 'caught') 'Synthetic exception path did not run.'
    $afterException=Enter-NativeWriteLease -ExecutableSha256 $digest -ExecutablePath $target
    Assert-LeaseTest (-not $afterException.Abandoned) 'Exception/finally cleanup left an abandoned mutex.'
    Exit-NativeWriteLease

    # Keep a non-owner handle open so Windows preserves the abandoned-object
    # signal after terminating the owner. Killing only this synthetic child is
    # intentional; no editor or unrelated process is selected or stopped.
    $killed=Start-LeaseTestChild 'hold' $target
    $held=Wait-LeaseTestReceipt $killed
    Assert-LeaseTest $held.acquired 'Kill-cleanup child could not acquire its lease.'
    $null=Retain-LeaseTestMutex $held.name
    $killed.Process.Kill()
    Assert-LeaseTest ($killed.Process.WaitForExit(12000)) 'Synthetic owner process did not terminate.'
    $recovered=Enter-NativeWriteLease -ExecutableSha256 $digest -ExecutablePath $target
    Assert-LeaseTest ($recovered.Abandoned-and -not $recovered.ReplayAuthorized) 'Abandoned ownership was not reported without replay authorization.'
    Exit-NativeWriteLease
    $afterKill=Start-LeaseTestChild 'probe' $target
    $after=Complete-LeaseTestChild $afterKill
    Assert-LeaseTest ($after.acquired-and -not $after.abandoned) 'Recovered lease was not cleanly released.'
    'Native guest writer lease process/thread/abandonment tests passed.'
}finally{
    try{Exit-NativeWriteLease}catch{}
    foreach($child in $children){try{if(-not $child.HasExited){$child.Kill();$null=$child.WaitForExit(3000)}}catch{};try{$child.Dispose()}catch{}}
    foreach($keeper in $keepers){try{$keeper.Dispose()}catch{}}
    Remove-Item -LiteralPath $folder -Recurse -Force -ErrorAction SilentlyContinue
}

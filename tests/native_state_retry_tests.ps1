[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$RunnerPath)
$ErrorActionPreference='Stop'
$tokens=$null;$parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($RunnerPath,[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count){throw ($parseErrors|Out-String)}
foreach($functionName in @('Read-State','Fingerprint','Assert-SameSession','Same-Snapshot','Matches','Wait-Edit','Wait-NativeSaveIdle')){
 $node=$ast.Find({param($n) $n-is [Management.Automation.Language.FunctionDefinitionAst]-and $n.Name-eq $functionName},$true)
 if(-not $node){throw "Runner function missing: $functionName"}
 . ([scriptblock]::Create($node.Extent.Text))
}
$script:reads=0;$script:sends=0
function Send-Step{ $script:sends++;throw 'A read retry must never send.' }
function Read-StateOnce{
 $script:reads++
 if($script:reads-lt 3){throw 'Incomplete native snapshot insertion.'}
 return [pscustomobject]@{snapshot_count=1;stable=$true}
}
$sample=Read-State
if($script:reads-ne 3-or -not $sample.stable-or $script:sends-ne 0){throw 'Transient read retry did not return a stable read-only sample.'}
function Read-StateOnce{throw 'Permanent identity failure.'}
$t=[Diagnostics.Stopwatch]::StartNew();$rejected=$false
try{Read-State|Out-Null}catch{$rejected=$_.Exception.Message-eq 'Permanent identity failure.'}
if(-not $rejected-or $t.ElapsedMilliseconds-lt 1900-or $t.ElapsedMilliseconds-gt 10000-or $script:sends-ne 0){throw 'Persistent read failure was not bounded and fail closed.'}

# Two empty sessions have identical snapshot fingerprints. Their paths and FPS
# must still bind the readback to the session selected before the command.
$empty=[pscustomobject]@{snapshot_count=0;snapshots=@();fps=25;current_session_path='C:\Synthetic\A.ses';console_pointer=4096}
$changedPath=[pscustomobject]@{snapshot_count=0;snapshots=@();fps=25;current_session_path='C:\Synthetic\B.ses';console_pointer=4096}
if((Fingerprint $empty)-cne (Fingerprint $changedPath)){throw 'Invalid identical-empty-session test fixture.'}
$rejected=$false
try{Assert-SameSession $empty $changedPath -Snapshots}catch{$rejected=$_.Exception.Message-like '*Session path or FPS changed*'}
if(-not $rejected){throw 'A different empty session passed the write boundary guard.'}
$changedFps=[pscustomobject]@{snapshot_count=0;snapshots=@();fps=30;current_session_path=$empty.current_session_path;console_pointer=4096}
$rejected=$false
try{Assert-SameSession $empty $changedFps -Snapshots}catch{$rejected=$_.Exception.Message-like '*Session path or FPS changed*'}
if(-not $rejected){throw 'A changed FPS passed the write boundary guard.'}
$samePath=[pscustomobject]@{snapshot_count=0;snapshots=@();fps=25;current_session_path='c:\synthetic\a.SES';console_pointer=4096}
Assert-SameSession $empty $samePath -Snapshots
$newConsole=[pscustomobject]@{snapshot_count=0;snapshots=@();fps=25;current_session_path=$empty.current_session_path;console_pointer=8192}
$rejected=$false
try{Assert-SameSession $empty $newConsole -Snapshots}catch{$rejected=$_.Exception.Message-like '*active console was replaced*'}
if(-not $rejected){throw 'A replaced console passed the write boundary guard.'}

# Wait-Edit must survive an incomplete read and a fully formed but stale value,
# then verify the actual requested transition without invoking Send-Step.
function Has($value,[string]$key){return $null-ne $value.PSObject.Properties[$key]}
$script:reads=0;$script:pumps=0
function Pump([int]$milliseconds){$script:pumps++}
$old=[pscustomobject]@{snapshot_count=1;fps=25;current_session_path=$empty.current_session_path;console_pointer=4096;snapshots=@([pscustomobject]@{name_hex='41';time_quarterframes=0;flags=0})}
$intended=[pscustomobject]@{snapshot_count=1;fps=25;current_session_path=$empty.current_session_path;console_pointer=4096;snapshots=@([pscustomobject]@{name_hex='42';time_quarterframes=0;flags=0})}
$step=[pscustomobject]@{op='rename';index=0;name_hex='42'}
function Read-State{
 $script:reads++
 if($script:reads-eq 1){throw 'Incomplete snapshot pointer.'}
 if($script:reads-eq 2){return $old}
 return $intended
}
$sample=Wait-Edit $old $step
if($sample.snapshots[0].name_hex-ne '42'-or $script:reads-ne 3-or $script:pumps-ne 3-or $script:sends-ne 0){throw 'Wait-Edit accepted stale state or replayed a native edit.'}
$intended.current_session_path=$changedPath.current_session_path
function Read-State{$script:reads++;return $intended}
$script:reads=0;$script:pumps=0;$rejected=$false
try{Wait-Edit $old $step|Out-Null}catch{$rejected=$_.Exception.Message-like '*Session path or FPS changed*'}
if(-not $rejected-or $script:reads-ne 1-or $script:pumps-ne 1-or $script:sends-ne 0){throw 'Wait-Edit accepted the intended snapshot from a different session or retried after the identity failure.'}
$intended.current_session_path=$old.current_session_path
function Pump([int]$milliseconds){throw 'Synthetic closed native connection.'}
$script:reads=0;$rejected=$false
try{Wait-Edit $old $step|Out-Null}catch{$rejected=$_.Exception.Message-eq 'Synthetic closed native connection.'}
if(-not $rejected-or $script:reads-ne 0-or $script:sends-ne 0){throw 'Wait-Edit swallowed a connection failure or replayed the edit.'}

# Existing automatic saves must finish before a filing operation is allowed.
# These mocks read no process memory and never create sockets or session files.
$native=[pscustomobject]@{Stream=$null}
$script:workerReads=0
function Read-SaveWorker{
 $script:workerReads++
 return [pscustomobject]@{pointer=123;finished=[int]($script:workerReads-ge 3)}
}
$worker=Wait-NativeSaveIdle
if($worker.finished-ne 1-or $script:workerReads-ne 3-or $script:sends-ne 0){throw 'Save-idle wait did not observe worker completion without sending.'}
$native.Stream=[pscustomobject]@{synthetic=$true}
$script:workerReads=0;$script:pumps=0
function Pump([int]$milliseconds){$script:pumps++}
$worker=Wait-NativeSaveIdle
if($worker.finished-ne 1-or $script:workerReads-ne 3-or $script:pumps-ne 2-or $script:sends-ne 0){throw 'Save-idle wait did not keep an existing native connection pumped without sending a command.'}
$script:workerReads=0
function Pump([int]$milliseconds){throw 'Synthetic closed connection during save wait.'}
$rejected=$false
try{Wait-NativeSaveIdle|Out-Null}catch{$rejected=$_.Exception.Message-eq 'Synthetic closed connection during save wait.'}
if(-not $rejected-or $script:workerReads-ne 1-or $script:sends-ne 0){throw 'Save-idle wait swallowed a connection failure or sent a command.'}
$native.Stream=$null
function Read-SaveWorker{return $null}
if($null-ne (Wait-NativeSaveIdle)){throw 'Absent save worker should already be idle.'}
function Read-SaveWorker{throw 'Synthetic invalid save-worker VMT.'}
$rejected=$false
try{Wait-NativeSaveIdle|Out-Null}catch{$rejected=$_.Exception.Message-eq 'Synthetic invalid save-worker VMT.'}
if(-not $rejected-or $script:sends-ne 0){throw 'Save-idle wait swallowed an identity failure.'}
function Read-SaveWorker{return [pscustomobject]@{pointer=123;finished=0}}
$t=[Diagnostics.Stopwatch]::StartNew();$rejected=$false
try{Wait-NativeSaveIdle|Out-Null}catch{$rejected=$_.Exception.Message-like '*save worker is still running*'}
if(-not $rejected-or $t.ElapsedMilliseconds-lt 9900-or $t.ElapsedMilliseconds-gt 20000-or $script:sends-ne 0){throw 'Save-idle wait was not bounded to ten seconds or sent an operation.'}
'Native state retry, edit verification and save-idle tests passed.'

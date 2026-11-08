# Native Quantum1 V2126 operations. No UI automation and no process-memory writes.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RequestPath,
    [Parameter(Mandatory=$true)][string]$ResultPath,
    [Parameter(Mandatory=$true)][string]$ExecutablePath,
    [int]$ProcessId=0,
    [string]$SkillRoot=''
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$expectedHash='535097f38bb459e6e299b5da50b62fa811511d92af6d7215a13d703a416f177d'
$reader=$null;$tempTemplate=$null
$native=[pscustomobject]@{Client=$null;Stream=$null;Age=$null}
$tx=[IO.MemoryStream]::new();$rx=[IO.MemoryStream]::new()
$verified=[Collections.Generic.List[object]]::new()
$result=[ordered]@{schema_version=1;status='error';request_sha256=$null;transport='native-tcp-loopback';ui_used=$false;memory_read_only=$true;verified_steps=@();error=$null}
$indeterminate=$null
function Has($value,[string]$key){return $null-ne $value.PSObject.Properties[$key]}
function Hex([byte[]]$value){return ([BitConverter]::ToString($value)).Replace('-','').ToLowerInvariant()}
function Int([object]$value,[int]$min,[int]$max,[string]$label){
    if($value-is [bool]-or $null-eq $value-or $value-isnot [ValueType]-or [decimal]$value-ne [math]::Truncate([decimal]$value)-or [decimal]$value-lt $min-or [decimal]$value-gt $max){throw "Invalid $label"};return [int]$value
}
function Strict-Encoding([string]$name){
    $code=switch($name){'ascii'{20127};'cp950'{950};'utf-8'{65001};default{throw 'Unsupported name encoding.'}}
    return [Text.Encoding]::GetEncoding($code,[Text.EncoderExceptionFallback]::new(),[Text.DecoderExceptionFallback]::new())
}
function Assert-Path([string]$path){
    if($path-notmatch '^[A-Za-z]:\\'-or $path-match '[\x00-\x1f*?"<>|]'-or $path.Substring(2).Contains(':')-or @($path.Split('\')|Where-Object {$_-eq '..'-or $_-eq '.'}).Count-gt 0-or [IO.Path]::GetExtension($path)-ine '.ses'){throw 'Require an absolute local-drive .ses path without traversal or wildcard characters.'}
    if($ascii.GetBytes($path).Length-gt 255){throw 'Native path exceeds 255 encoded bytes.'}
}
function Frame([int]$category,[int]$group,[int]$control,[int]$index,[byte[]]$body){
    if($body.Length-lt 2){$body=$body+(New-Object byte[] (2-$body.Length))}
    $m=[IO.MemoryStream]::new();$w=[IO.BinaryWriter]::new($m)
    try{
        $w.Write([uint32](18+$body.Length));$w.Write([uint16]255);$w.Write([byte]$category)
        $w.Write([uint16]$control);$w.Write([byte]0);$w.Write([uint16]$group);$w.Write([int32]$index);$w.Write($body);$w.Flush()
        $sum=0;foreach($b in $m.ToArray()){$sum=($sum+[int]$b)-band 65535};$w.Write([uint16]$sum);$w.Flush();return ,$m.ToArray()
    }finally{$w.Dispose();$m.Dispose()}
}
function String-Body([byte[]]$bytes){return ,([byte[]]([BitConverter]::GetBytes([uint32]$bytes.Length)+$bytes))}
function Expected-Frames($step){
    $frames=[Collections.Generic.List[string]]::new();$idx=0;if(Has $step 'index'){$idx=[int]$step.index}
    switch($step.op){
        'create'{
            $frames.Add((Hex (Frame 1 1800 201 $idx ([BitConverter]::GetBytes([int32]-1)))))
            $frames.Add((Hex (Frame 3 1800 205 $idx (String-Body $nameEncoding.GetBytes([string]$step.name)))))
            if(Has $step 'time_quarterframes'){$frames.Add((Hex (Frame 1 1813 181 $idx ([BitConverter]::GetBytes([int32]$step.time_quarterframes)))))}
        }
        'delete'{$frames.Add((Hex (Frame 1 1800 206 $idx ([BitConverter]::GetBytes([int32]0)))))}
        'rename'{$frames.Add((Hex (Frame 3 1800 205 $idx (String-Body $nameEncoding.GetBytes([string]$step.name)))))}
        'timecode'{$frames.Add((Hex (Frame 1 1813 181 $idx ([BitConverter]::GetBytes([int32]$step.time_quarterframes)))))}
        'save_as'{$frames.Add((Hex (Frame 3 421 0 0 (String-Body $ascii.GetBytes([string]$step.path)))))}
        'save'{$frames.Add((Hex (Frame 1 422 0 0 ([BitConverter]::GetBytes([int32]0)))))}
        'load'{$frames.Add((Hex (Frame 3 411 0 0 (String-Body $ascii.GetBytes([string]$step.path)))))}
        'new_session'{}
        default{throw 'Unknown native operation.'}
    };return ,$frames.ToArray()
}
function Read-Short([long]$address){$length=$reader.U8($address);return ,$reader.Read($address+1,$length)}
function Read-State{
    $list=[long]$reader.U32($base+0x127305C)
    if($list-eq 0-or $reader.U32($list)-ne ($base+0x7F9BB0)){throw 'Unexpected/uninitialized snapshot list.'}
    $items=[long]$reader.U32($list+4);$count=$reader.I32($list+8);$capacity=$reader.I32($list+12)
    if($count-lt 0-or $count-gt 2000-or $capacity-lt $count){throw 'Invalid snapshot list bounds.'}
    $snapshots=@()
    for($i=0;$i-lt $count;$i++){
        $p=[long]$reader.U32($items+4*$i);$b=$reader.Read($p,0x7D4)
        if([BitConverter]::ToUInt32($b,0)-ne ($base+0x7F8188)){throw 'Unexpected snapshot type.'}
        $length=[int]$b[0x28];if($length-gt 31){throw 'Invalid snapshot name length.'}
        $name=New-Object byte[] $length;if($length-gt 0){[Array]::Copy($b,0x29,$name,0,$length)}
        $flags=[BitConverter]::ToUInt16($b,0x430);$q=[BitConverter]::ToInt32($b,0x76F)
        $decoded=$null;try{$decoded=$nameEncoding.GetString($name)}catch{}
        $snapshots+= [pscustomobject][ordered]@{index=$i;name_hex=(Hex $name);name=$decoded;name_encoding=$request.encoding;time_quarterframes=$q;recall_at_active=(($flags-band 128)-ne 0);flags=$flags}
    }
    if($reader.U32($base+0x127305C)-ne $list-or $reader.U32($list+4)-ne $items-or $reader.I32($list+8)-ne $count){throw 'Snapshot list changed during readback.'}
    return [pscustomobject][ordered]@{snapshot_count=$count;fps=$reader.U8($base+0x127AD30);current_session_path=$ascii.GetString((Read-Short ($base+0x127B4FC)));windows_acp=[DiGiCoReadOnly]::GetACP();any_connected_peer=$reader.U8($base+0x127AD6E);snapshots=@($snapshots)}
}
function Fingerprint($state){return (@($state.snapshots|ForEach-Object {"$($_.name_hex):$($_.time_quarterframes):$($_.flags)"})-join '|')}
function Only-Loopback-Peers{
    $manager=[long]$reader.U32($base+0x126E670);if($manager-eq 0){throw 'Native networking is not initialized.'}
    $n=$reader.I32($manager+8);$items=[long]$reader.U32($manager+4)
    if($n-lt 0-or $n-gt 256){throw 'Invalid native peer table.'}
    for($i=0;$i-lt $n;$i++){$peer=[long]$reader.U32($items+4*$i);if($reader.U8($peer+0x12B)-ne 0){$ip=$reader.Read($peer,4);if($ip[0]-ne 127){throw 'A non-loopback peer is connected; refusing this offline-editor operation.'}}}
}
function Disconnect-Native{
    if($native.Client){$native.Client.Dispose()}
    $native.Client=$null;$native.Stream=$null
    $timer=[Diagnostics.Stopwatch]::StartNew()
    while($timer.ElapsedMilliseconds-lt 5000){
        $pending=$false
        try{
            $manager=[long]$reader.U32($base+0x126E670);$n=$reader.I32($manager+8);$items=[long]$reader.U32($manager+4)
            if($n-lt 0-or $n-gt 256){throw 'Invalid peer table during disconnect.'}
            for($i=0;$i-lt $n;$i++){$peer=[long]$reader.U32($items+4*$i);$ip=$reader.Read($peer,4);if($ip[0]-eq 127-and ($reader.U8($peer+0x12B)-ne 0-or $reader.U32($peer+0x10D)-ne 0)){$pending=$true}}
            if(-not $pending){return}
        }catch{}
        [Threading.Thread]::Sleep(25)
    }
    throw 'Old native peer has not finished disconnecting; no new connection was opened.'
}
function Send-Bytes([byte[]]$bytes){
    if($null-eq $native.Stream){throw 'No native connection.'}
    $native.Stream.Write($bytes,0,$bytes.Length);$tx.Write($bytes,0,$bytes.Length);$clock.Restart()
}
function Pump([int]$milliseconds){
    $timer=[Diagnostics.Stopwatch]::StartNew();$buffer=New-Object byte[] 65536
    while($timer.ElapsedMilliseconds-lt $milliseconds){
        if($clock.ElapsedMilliseconds-ge 2000){Send-Bytes (Frame 0 0 0 0 ([byte[]](127,0,0,1)))}
        if($native.Client.Client.Poll(0,[Net.Sockets.SelectMode]::SelectRead)-and $native.Client.Available-eq 0){throw 'Native TCP peer closed the connection.'}
        if($native.Stream.DataAvailable){$n=$native.Stream.Read($buffer,0,$buffer.Length);if($n-le 0){throw 'Native TCP EOF.'};$rx.Write($buffer,0,$n)}else{[Threading.Thread]::Sleep(15)}
    }
}
function Connect-Native{
    Only-Loopback-Peers
    $connection=[Net.Sockets.TcpClient]::new();$connection.Connect('127.0.0.1',2028);$connection.NoDelay=$true
    $native.Client=$connection;$native.Stream=$connection.GetStream()
    $native.Age=[Diagnostics.Stopwatch]::StartNew()
    $prefix=Read-Short ($base+0x127AD9C);$name=[byte[]]($prefix+$ascii.GetBytes(' Codex'))
    if($name.Length-gt 30){throw 'Native identity name exceeds supported length.'}
    $version=$reader.U16($base+0x127A158);if($version-ne 2126){throw 'Runtime protocol version mismatch.'}
    $m=[IO.MemoryStream]::new();$w=[IO.BinaryWriter]::new($m)
    try{$w.Write([uint32](23+$name.Length));$w.Write([uint16]255);$w.Write([byte]0);$w.Write([uint16]1);$w.Write([byte]0);$w.Write([uint16]$version);$w.Write([int32]0);$w.Write([byte[]](1,2,255,0,[byte]$name.Length));$w.Write($name);$w.Flush();$sum=0;foreach($b in $m.ToArray()){$sum=($sum+$b)-band 65535};$w.Write([uint16]$sum);$w.Flush();Send-Bytes $m.ToArray()}finally{$w.Dispose();$m.Dispose()}
    $timer=[Diagnostics.Stopwatch]::StartNew();while($timer.ElapsedMilliseconds-lt 5000){Pump 75;if($reader.U8($base+0x127AD6E)-ne 0){Only-Loopback-Peers;return}}
    throw 'Original native identity handshake was not accepted.'
}
function Send-Step($step){foreach($hex in (Expected-Frames $step)){$bytes=New-Object byte[] ($hex.Length/2);for($i=0;$i-lt $bytes.Length;$i++){$bytes[$i]=[Convert]::ToByte($hex.Substring($i*2,2),16)};Send-Bytes $bytes}}
function Same-Snapshot($a,$b){return $a.name_hex-eq $b.name_hex-and $a.time_quarterframes-eq $b.time_quarterframes-and $a.flags-eq $b.flags}
function Matches($old,$actual,$step){
    $op=$step.op;$count=$old.snapshot_count;if($op-eq 'create'){$count++};if($op-eq 'delete'){$count--}
    if($actual.snapshot_count-ne $count){return $false}
    for($i=0;$i-lt $count;$i++){
        $now=$actual.snapshots[$i]
        if($op-eq 'create'-and $i-eq $step.index){if($now.name_hex-ne $step.name_hex){return $false};if(Has $step 'time_quarterframes'){if($now.time_quarterframes-ne $step.time_quarterframes-or -not $now.recall_at_active){return $false}};continue}
        $oldIndex=$i;if($op-eq 'create'-and $i-gt $step.index){$oldIndex--};if($op-eq 'delete'-and $i-ge $step.index){$oldIndex++}
        $was=$old.snapshots[$oldIndex]
        if($op-eq 'rename'-and $i-eq $step.index){if($now.name_hex-ne $step.name_hex-or $now.time_quarterframes-ne $was.time_quarterframes-or $now.flags-ne $was.flags){return $false}}
        elseif($op-eq 'timecode'-and $i-eq $step.index){if($now.name_hex-ne $was.name_hex-or $now.time_quarterframes-ne $step.time_quarterframes-or $now.flags-ne ($was.flags-bor 128)){return $false}}
        elseif(-not (Same-Snapshot $was $now)){return $false}
    };return $true
}
function Wait-Edit($old,$step){$timer=[Diagnostics.Stopwatch]::StartNew();while($timer.ElapsedMilliseconds-lt 10000){Pump 75;$actual=Read-State;if(Matches $old $actual $step){return $actual}};throw 'Native edit did not match readback; do not retry blindly.'}
function Check-SessionFile([string]$path){
    Assert-Path $path;if(-not (Test-Path -LiteralPath $path -PathType Leaf)){throw 'Session file does not exist.'}
    $handle=[IO.File]::OpenRead($path)
    try{$bytes=New-Object byte[] 36;if($handle.Read($bytes,0,36)-ne 36){throw 'Truncated session header.'}}finally{$handle.Dispose()}
    if($ascii.GetString($bytes,0,26)-ne "DiGiCo     Q1  .SES vN   `r"-or [BitConverter]::ToUInt16($bytes,34)-ne 2126){throw 'Session model/version header is not the verified Q1 V2126 profile.'}
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Native-LoadOnce([string]$path,[int]$expectedCount=-1){
    $hash=Check-SessionFile $path
    $previous=Read-State
    if($previous.current_session_path-ieq $path){throw 'A same-path load requires the observable staged-load route.'}
    Send-Bytes (Frame 3 411 0 0 (String-Body $ascii.GetBytes($path)))
    $timer=[Diagnostics.Stopwatch]::StartNew();$last='';$stable=0
    while($timer.ElapsedMilliseconds-lt 30000){
        [Threading.Thread]::Sleep(150)
        try{$state=Read-State;$sig=Fingerprint $state;if($state.current_session_path-ieq $path-and $reader.I32($base+0xA28560)-eq 0-and ($expectedCount-lt 0-or $state.snapshot_count-eq $expectedCount)){if($sig-eq $last){$stable++}else{$stable=0};$last=$sig;if($stable-ge 3){if((Check-SessionFile $path)-ne $hash){throw 'Loaded file changed during verification.'};return $state}}}catch{if($_.Exception.Message-like '*file changed*'){throw}}
    };throw 'Native load did not produce the expected stable session state.'
}
function Native-Load([string]$path,[int]$expectedCount=-1){
    $sourceHash=Check-SessionFile $path
    $previous=Read-State
    if($previous.current_session_path-ine $path){return (Native-LoadOnce $path $expectedCount)}
    # Same-path loads lack an observable filename transition. A byte-identical
    # temporary alias gives both loads that transition; an old
    # successful result word and unchanged fields cannot satisfy the gate.
    $alias=Join-Path ([IO.Path]::GetTempPath()) ('digico-reload-'+[Guid]::NewGuid().ToString('N')+'.ses')
    Assert-Path $alias
    try{
        [IO.File]::Copy($path,$alias,$false)
        if((Check-SessionFile $alias)-ne $sourceHash){throw 'Reload alias copy differs from source.'}
        $aliased=Native-LoadOnce $alias $expectedCount
        Disconnect-Native;Connect-Native
        $actual=Native-LoadOnce $path $expectedCount
        if((Fingerprint $actual)-ne (Fingerprint $aliased)){throw 'Same-file reload fields differ between the byte-identical alias and original.'}
        if((Check-SessionFile $path)-ne $sourceHash){throw 'Reload source file changed.'}
        return $actual
    }finally{
        if(Test-Path -LiteralPath $alias){
            $stillCurrent=$true;try{$stillCurrent=(Read-State).current_session_path-ieq $alias}catch{}
            if($stillCurrent){$result.recovery_stage_path=$alias}else{Remove-Item -LiteralPath $alias -Force}
        }
    }
}
function Read-SaveWorker{
    $worker=[long]$reader.U32($base+0xA28490)
    if($worker-eq 0){return $null}
    if($reader.U32($worker)-ne ($base+0x535038)){throw 'Unexpected native save-worker VMT.'}
    return [pscustomobject]@{pointer=$worker;path=$ascii.GetString((Read-Short ($worker+0x30)));finished=$reader.U8($worker+0x11);terminated=$reader.U8($worker+0x0E);fatal_exception=$reader.U32($worker+0x28);error=$reader.U8($worker+0x131);copy_error=$reader.U8($worker+0x132)}
}
function Native-Save($step,$old){
    $path=[string]$step.path;if($step.op-eq 'save'){$path=$old.current_session_path}
    Assert-Path $path
    $priorWorker=Read-SaveWorker
    if($priorWorker-and $priorWorker.finished-eq 0){throw 'A previous native save worker is still running; no save was sent.'}
    $exists=Test-Path -LiteralPath $path -PathType Leaf;$backup=$null;$oldTicks=-1;$oldHash=$null
    if($exists){$oldTicks=(Get-Item -LiteralPath $path).LastWriteTimeUtc.Ticks;$oldHash=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        if($step.op-eq 'save_as'){
            if(-not $step.overwrite){throw 'Refusing to overwrite an existing session.'}
            $backup=Join-Path ([IO.Path]::GetDirectoryName($path)) ('codex-backup-'+[Guid]::NewGuid().ToString('N')+'.ses')
            [IO.File]::Copy($path,$backup,$false)
        }
    }
    Send-Step $step
    $timer=[Diagnostics.Stopwatch]::StartNew();$stable=0;$last=''
    while($timer.ElapsedMilliseconds-lt 30000){
        Pump 300
        # Never hash/open a destination while the original save worker may be
        # acquiring its write handle. A read handle can make that acquisition
        # fail. Worker completion is stronger than a temporarily stable file.
        $worker=Read-SaveWorker
        if($null-eq $worker-or $worker.path-ine $path-or $worker.finished-eq 0){continue}
        if($worker.fatal_exception-ne 0-or $worker.error-ne 0-or $worker.copy_error-ne 0){throw 'Original native save worker reported an error.'}
        if(Test-Path -LiteralPath $path -PathType Leaf){
            try{$info=Get-Item -LiteralPath $path;$hash=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()}catch{continue}
            $changed=(-not $exists)-or $info.LastWriteTimeUtc.Ticks-ne $oldTicks-or $hash-ine $oldHash
            $sig="$($info.Length):$hash";if($changed-and $sig-eq $last){$stable++}else{$stable=0};$last=$sig
            $actual=Read-State
            if($stable-ge 3-and $actual.current_session_path-ieq $path-and $reader.I32($base+0xA28560)-eq 0){
                if((Fingerprint $actual)-ne (Fingerprint $old)){throw 'Snapshot fields changed during native save.'}
                return [pscustomobject]@{path=$path;sha256=$hash;bytes=$info.Length;backup_path=$backup;state=$actual;worker=$worker}
            }
        }
    };throw 'Native save did not produce a changed, stable, successful file.'
}
try{
    if(Test-Path -LiteralPath $ResultPath){throw 'Result path already exists; choose a fresh file.'}
    $result.request_sha256=(Get-FileHash -LiteralPath $RequestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $request=Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8|ConvertFrom-Json
    if($request.schema_version-ne 1-or $request.mode-notin @('inspect','execute')){throw 'Invalid compiled request schema/mode.'}
    $ascii=Strict-Encoding 'ascii';$nameEncoding=Strict-Encoding $request.encoding
    if($request.fps-ne 25){throw 'Only the runtime-verified 25fps profile is supported.'}
    if(-not $SkillRoot){if(-not $PSScriptRoot){throw 'SkillRoot is required for script-block invocation.'};$SkillRoot=Split-Path -Parent $PSScriptRoot}
    if((Get-FileHash -LiteralPath $ExecutablePath -Algorithm SHA256).Hash.ToLowerInvariant()-ne $expectedHash){throw 'Executable is not the exact verified Quantum1 V2126 build.'}
    if($ProcessId){$process=Get-Process -Id $ProcessId}else{$p=@(Get-Process -Name Quantum1 -ErrorAction SilentlyContinue|Where-Object {$_.Path-ieq $ExecutablePath});if($p.Count-ne 1){throw 'Expected exactly one existing target editor process; start it by CLI or specify ProcessId.'};$process=$p[0]}
    if($process.Path-ine $ExecutablePath){throw 'PID/executable binding mismatch.'}
    $base=$process.MainModule.BaseAddress.ToInt64()
    if(-not ('DiGiCoReadOnly'-as [type])){Add-Type -TypeDefinition @'
using System; using System.ComponentModel; using System.Runtime.InteropServices;
public sealed class DiGiCoReadOnly:IDisposable {
 [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint a,bool i,int p);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool ReadProcessMemory(IntPtr p,IntPtr a,byte[] b,UIntPtr n,out UIntPtr r);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
 [DllImport("kernel32.dll")] public static extern uint GetACP();
 readonly IntPtr h; public DiGiCoReadOnly(int p){h=OpenProcess(0x410,false,p);if(h==IntPtr.Zero)throw new Win32Exception();}
 public byte[] Read(long a,int n){if(a<=0||n<0||n>1048576)throw new ArgumentException();byte[] b=new byte[n];if(n==0)return b;UIntPtr r;if(!ReadProcessMemory(h,new IntPtr(a),b,(UIntPtr)(uint)n,out r)||r.ToUInt64()!=(ulong)n)throw new Win32Exception();return b;}
 public uint U32(long a){return BitConverter.ToUInt32(Read(a,4),0);} public int I32(long a){return BitConverter.ToInt32(Read(a,4),0);}
 public ushort U16(long a){return BitConverter.ToUInt16(Read(a,2),0);} public byte U8(long a){return Read(a,1)[0];} public void Dispose(){CloseHandle(h);}
}
'@}
    $reader=[DiGiCoReadOnly]::new($process.Id);$clock=[Diagnostics.Stopwatch]::StartNew()
    $before=Read-State;$result.before=$before;$result.process_id=$process.Id;$result.executable_sha256=$expectedHash
    if($request.mode-eq 'inspect'){$result.after=$before;$result.status='ok'}else{
        if(-not (Test-Path -LiteralPath (Join-Path ([IO.Path]::GetDirectoryName($ExecutablePath)) 'OffLine.ini'))){throw 'Verified offline-editor marker OffLine.ini is missing.'}
        if($before.fps-ne 25){throw 'Runtime editor is not set to 25fps; no FPS mutation is implemented.'}
        $busyWorker=Read-SaveWorker
        if($busyWorker-and $busyWorker.finished-eq 0){throw 'The original editor save worker is still running; no operation was sent.'}
        $expected=Int $request.expected_count 0 2000 'expected_count';if($before.snapshot_count-ne $expected){throw 'Expected snapshot count differs from actual state.'}
        $steps=@($request.steps);if($steps.Count-lt 1-or $steps.Count-gt 1000){throw 'Invalid operation count.'}
        $sim=[Collections.ArrayList]::new();foreach($s in $before.snapshots){[void]$sim.Add($s)}
        # Validate the complete plan, indices, bytes and file prerequisites before connecting.
        foreach($step in $steps){
            if($step.op-notin @('create','delete','rename','timecode','save_as','save','load','new_session')){throw 'Unknown operation.'}
            if($step.op-in @('load','new_session')-and $steps.Count-ne 1){throw 'Load/new_session must be a standalone request; inspect after loading.'}
            if($step.op-in @('save','save_as')-and $step-ne $steps[-1]){throw 'Save must be the final operation in a batch.'}
            if($step.op-in @('create','delete','rename','timecode')){
                $max=$sim.Count-1;if($step.op-eq 'create'){$max=$sim.Count};$idx=Int $step.index 0 $max 'snapshot index'
                if($step.op-ne 'create'-and ($sim[$idx].flags-band 0x2000)-ne 0){throw 'A target snapshot is locked.'}
                if($step.op-in @('create','rename')){$bytes=$nameEncoding.GetBytes([string]$step.name);if($bytes.Length-lt 1-or $bytes.Length-gt 31-or $bytes-contains [byte]0-or (Hex $bytes)-ne $step.name_hex){throw 'Invalid/lossy native snapshot name.'}}
                if(Has $step 'time_quarterframes'){[void](Int $step.time_quarterframes 0 8639996 'quarter-frame value');if(([int]$step.time_quarterframes%4)-ne 0){throw 'Only whole-frame cue boundaries are supported.'}}
                if($step.op-eq 'create'){if($sim.Count-ge 2000){throw 'Local snapshot bound exceeded.'};$sim.Insert($idx,[pscustomobject]@{flags=0})};if($step.op-eq 'delete'){$sim.RemoveAt($idx)}
                if($step.expected_count_after-ne $sim.Count){throw 'Compiled count transition mismatch.'}
            }
            if($step.op-in @('save_as','load','new_session')){Assert-Path $step.path}
            if($step.op-in @('save_as','new_session')){if(-not (Test-Path -LiteralPath ([IO.Path]::GetDirectoryName($step.path)) -PathType Container)){throw 'Output parent directory does not exist.'};if((Test-Path -LiteralPath $step.path)-and (-not $step.overwrite-or $step.op-eq 'new_session')){throw 'Output already exists.'}}
            if($step.op-eq 'save'){Assert-Path $before.current_session_path}
            if($step.op-eq 'load'){[void](Check-SessionFile $step.path)}
            $frames=Expected-Frames $step;$given=@($step.frames)
            if($frames.Count-ne $given.Count){throw 'Compiled frame count mismatch.'};for($i=0;$i-lt $frames.Count;$i++){if($frames[$i]-cne $given[$i]){throw 'Compiled packet bytes do not match validated operation.'}}
        }
        if($steps[0].op-eq 'new_session'){
            $manifest=Get-Content -LiteralPath (Join-Path $SkillRoot 'assets\empty-template.json') -Raw -Encoding UTF8|ConvertFrom-Json
            $template=Join-Path $SkillRoot ('assets\'+$manifest.filename)
            if($manifest.snapshot_count-ne 0-or (Get-FileHash $template -Algorithm SHA256).Hash.ToLowerInvariant()-ne $manifest.sha256){throw 'Bundled empty-template identity mismatch.'}
        }
        Connect-Native;$working=Read-State
        if((Fingerprint $working)-ne (Fingerprint $before)){throw 'State changed between preflight and connection.'}
        foreach($step in $steps){
            Only-Loopback-Peers;$now=Read-State
            if((Fingerprint $now)-ne (Fingerprint $working)){throw 'Unexpected concurrent snapshot changes.'}
            # Local editor connections have previously stopped progressing in
            # a long batch. Renew only at a verified operation boundary; never
            # replay an operation whose acceptance is uncertain.
            if($native.Age.ElapsedMilliseconds-ge 2000){Disconnect-Native;Connect-Native}
            if($reader.U8($base+0x127AD6E)-eq 0){throw 'Native identity connection lost before operation.'}
            $indeterminate=$step
            switch($step.op){
                {$_-in @('create','delete','rename','timecode')}{Send-Step $step;$working=Wait-Edit $working $step;$verified.Add([pscustomobject]@{op=$step.op;index=$step.index;verified=$true;snapshot_count=$working.snapshot_count});break}
                {$_-in @('save_as','save')}{ $saved=Native-Save $step $working;$working=$saved.state;$verified.Add([pscustomobject]@{op=$step.op;verified=$true;path=$saved.path;sha256=$saved.sha256;bytes=$saved.bytes;backup_path=$saved.backup_path;native_worker=$saved.worker});break}
                'load'{$working=Native-Load $step.path;if($working.fps-ne 25){throw 'Loaded session is not 25fps; no further operation was sent.'};$verified.Add([pscustomobject]@{op='load';verified=$true;verification='observed path transition, stable fields and native result; same-path reload uses a byte-identical temporary alias';path=$step.path;snapshot_count=$working.snapshot_count});break}
                'new_session'{
                    $tempTemplate=Join-Path ([IO.Path]::GetTempPath()) ('digico-empty-'+[Guid]::NewGuid().ToString('N')+'.ses');Assert-Path $tempTemplate
                    [IO.File]::Copy($template,$tempTemplate,$false);$working=Native-Load $tempTemplate 0
                    if($working.fps-ne 25){throw 'Loaded template is not 25fps; no SaveAs was sent.'}
                    Disconnect-Native;Connect-Native
                    $saveStep=[pscustomobject]@{op='save_as';path=$step.path;overwrite=$false}
                    $saved=Native-Save $saveStep $working;$working=$saved.state
                    $verified.Add([pscustomobject]@{op='new_session';verified=$true;semantics='new file from verified empty-snapshot template, preserving template structure/routing';path=$saved.path;sha256=$saved.sha256;bytes=$saved.bytes;snapshot_count=0;native_worker=$saved.worker});break
                }
            }
            $indeterminate=$null
        }
        $result.after=Read-State;$result.status='ok'
    }
}catch{
    $result.error=$_.Exception.Message
    if($null-ne $indeterminate){$result.indeterminate_operation=$indeterminate;$result.recovery='Inspect actual state before retry; create/delete are not idempotent.'}
    if($reader){try{$result.after=Read-State}catch{}}
}finally{
    $result.verified_steps=@($verified.ToArray());$result.tx_bytes=$tx.Length;$result.rx_bytes=$rx.Length
    if($native.Client){$native.Client.Dispose()}
    if($tempTemplate-and (Test-Path -LiteralPath $tempTemplate)){
        $stillCurrent=$true;try{$stillCurrent=(Read-State).current_session_path-ieq $tempTemplate}catch{}
        if($stillCurrent){$result.recovery_stage_path=$tempTemplate}else{Remove-Item -LiteralPath $tempTemplate -Force}
    }
    if($reader){$reader.Dispose()}
    $parent=[IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($ResultPath))
    if(-not (Test-Path -LiteralPath $ResultPath)){
        $temporary=Join-Path $parent ('.digico-result-'+[Guid]::NewGuid().ToString('N')+'.json')
        [IO.File]::WriteAllText($temporary,($result|ConvertTo-Json -Depth 30),[Text.UTF8Encoding]::new($false));[IO.File]::Move($temporary,$ResultPath)
        [IO.File]::WriteAllBytes($ResultPath+'.tx.bin',$tx.ToArray());[IO.File]::WriteAllBytes($ResultPath+'.rx.bin',$rx.ToArray())
    }
    $tx.Dispose();$rx.Dispose()
}
if($result.status-ne 'ok'){Write-Error $result.error;exit 1}
Write-Output ('Verified native '+$request.mode+': '+$verified.Count+' operations; '+$result.after.snapshot_count+' snapshots.')

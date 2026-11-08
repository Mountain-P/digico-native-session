param([Parameter(Mandatory=$true)][string]$EndpointModulePath)
$ErrorActionPreference='Stop'
if([Environment]::OSVersion.Platform-ne [PlatformID]::Win32NT){throw 'Native TCP endpoint integration tests require Windows.'}
. $EndpointModulePath

function Reject-EndpointOwner([scriptblock]$Action) {
    $rejected=$false
    try { & $Action } catch { $rejected=$true }
    if(-not $rejected){throw 'Expected endpoint owner rejection.'}
}
function Read-ChildEndpointLine($Process) {
    $task=$Process.StandardOutput.ReadLineAsync()
    if(-not $task.Wait(10000)){throw 'Synthetic listener child did not respond within ten seconds.'}
    $line=$task.Result
    if($null-eq $line){
        $diagnostic='';if($Process.HasExited){$diagnostic=$Process.StandardError.ReadToEnd()}
        throw ('Synthetic listener child closed stdout. '+$diagnostic)
    }
    return $line
}
function Write-ChildEndpointCommand($Process,[string]$Command) {
    # Process.StandardInput's StreamWriter inherits the parent's console
    # encoding and can emit a UTF-8 BOM. Never use/flush that writer here.
    $encoding=[Text.Encoding]::GetEncoding(20127,[Text.EncoderExceptionFallback]::new(),[Text.DecoderExceptionFallback]::new())
    $bytes=$encoding.GetBytes($Command+"`n")
    $stream=$Process.StandardInput.BaseStream
    $stream.Write($bytes,0,$bytes.Length);$stream.Flush()
}
function Send-ChildEndpointCommand($Process,[string]$Command) {
    Write-ChildEndpointCommand $Process $Command
    return (Read-ChildEndpointLine $Process)
}

# The only sockets created here belong to this test and its child. They bind
# IPv4 loopback and ephemeral ports; no DiGiCo editor, production2028, external
# interface, or external host is contacted. The module under test creates none.
$ownListener=$null;$child=$null;$client=$null;$childStarted=$false
try {
    $ownListener=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0)
    $ownListener.ExclusiveAddressUse=$true;$ownListener.Start()
    $ownPort=([Net.IPEndPoint]$ownListener.LocalEndpoint).Port
    $proof=Assert-NativeEndpointOwner -ProcessId $PID -Port $ownPort
    if(-not $proof.verified-or $proof.check-cne 'listener'-or $proof.local_address-cne '127.0.0.1'){throw 'Self-owned loopback listener was not correctly witnessed.'}
    $ownListener.Stop();$ownListener=$null
    Reject-EndpointOwner {Assert-NativeEndpointOwner -ProcessId $PID -Port $ownPort}

    $childCode=@'
$ErrorActionPreference='Stop'
[Console]::InputEncoding=[Text.UTF8Encoding]::new($false,$true)
function Convert-ChildEndpointCommand([string]$Command,[bool]$FirstLine){
    # Accommodate one stream preamble on the first line only. Do not Trim()
    # whitespace, repeated BOMs, trailing BOMs or BOMs on subsequent commands.
    if($FirstLine-and $Command.Length-gt 0-and [int]$Command[0]-eq 0xfeff){return $Command.Substring(1)}
    return $Command
}
function Format-ChildEndpointCommand([string]$Command){
    $points=[Collections.Generic.List[string]]::new()
    for($i=0;$i-lt $Command.Length-and $points.Count-lt 16;$i++){
        $value=[int]$Command[$i]
        if([char]::IsHighSurrogate($Command[$i])-and $i+1-lt $Command.Length-and [char]::IsLowSurrogate($Command[$i+1])){
            $value=[char]::ConvertToUtf32($Command,$i);$i++
        }
        $points.Add(('U+{0:X4}'-f $value))
    }
    return ('length='+$Command.Length+'; prefix codepoints='+($points-join ' '))
}
function Assert-ChildEndpointCommand([string]$Command){
    # Even case-sensitive PowerShell comparisons can ignore Unicode format
    # characters under culture comparison. IPC commands require exact bytes.
    foreach($known in @('accept','close_client','close_listener','quit')){
        if([string]::Equals($Command,$known,[StringComparison]::Ordinal)){return}
    }
    throw ('Unknown synthetic listener command; '+(Format-ChildEndpointCommand $Command))
}
$listener=$null;$accepted=$null
try {
    $listener=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0)
    $listener.ExclusiveAddressUse=$true;$listener.Start()
    $port=([Net.IPEndPoint]$listener.LocalEndpoint).Port
    [Console]::Out.WriteLine((@{process_id=$PID;port=$port}|ConvertTo-Json -Compress));[Console]::Out.Flush()
    $firstLine=$true
    while($true){
        $command=[Console]::ReadLine()
        if($null-eq $command){break}
        $command=Convert-ChildEndpointCommand $command $firstLine;$firstLine=$false
        Assert-ChildEndpointCommand $command
        if([string]::Equals($command,'quit',[StringComparison]::Ordinal)){break}
        switch -CaseSensitive ($command){
            'accept'{$accepted=$listener.AcceptTcpClient();[Console]::Out.WriteLine('accepted');[Console]::Out.Flush()}
            'close_client'{if($accepted){$accepted.Dispose();$accepted=$null};[Console]::Out.WriteLine('client_closed');[Console]::Out.Flush()}
            'close_listener'{$listener.Stop();$listener=$null;[Console]::Out.WriteLine('listener_closed');[Console]::Out.Flush()}
        }
    }
}finally{if($accepted){$accepted.Dispose()};if($listener){$listener.Stop()}}
'@
    # Exercise the exact child parser without a process/socket: the fix must
    # allow a single initial preamble and keep every other unknown command bad.
    $tokens=$null;$parseErrors=$null
    $childAst=[Management.Automation.Language.Parser]::ParseInput($childCode,[ref]$tokens,[ref]$parseErrors)
    if($parseErrors.Count){throw ($parseErrors|Out-String)}
    foreach($functionName in @('Convert-ChildEndpointCommand','Format-ChildEndpointCommand','Assert-ChildEndpointCommand')){
        $node=$childAst.Find({param($n) $n-is [Management.Automation.Language.FunctionDefinitionAst]-and $n.Name-eq $functionName},$true)
        if(-not $node){throw 'Synthetic listener command parser missing.'}
        . ([scriptblock]::Create($node.Extent.Text))
    }
    $bom=[string][char]0xfeff
    foreach($raw in @('accept',($bom+'accept'))){
        $command=Convert-ChildEndpointCommand $raw $true
        if(-not [string]::Equals($command,'accept',[StringComparison]::Ordinal)){throw 'First child command was not decoded exactly.'}
        Assert-ChildEndpointCommand $command
    }
    foreach($sample in @(
        [pscustomobject]@{Text=($bom+$bom+'accept');First=$true},
        [pscustomobject]@{Text=($bom+'accept');First=$false},
        [pscustomobject]@{Text=('accept'+$bom);First=$true},
        [pscustomobject]@{Text=' accept';First=$true},
        [pscustomobject]@{Text='accept ';First=$true},
        [pscustomobject]@{Text='ACCEPT';First=$true}
    )){
        $command=Convert-ChildEndpointCommand $sample.Text $sample.First
        $message=$null;try{Assert-ChildEndpointCommand $command}catch{$message=$_.Exception.Message}
        if($null-eq $message-or $message-notlike '*Unknown synthetic listener command; length=*prefix codepoints=U+*'){
            $details=[ordered]@{first_line=$sample.First;input=(Format-ChildEndpointCommand $sample.Text);normalized=(Format-ChildEndpointCommand $command);error=$message}
            throw ('Unknown child command was normalized or lost its bounded codepoint diagnostic: '+($details|ConvertTo-Json -Compress))
        }
    }
    $diagnostic=Format-ChildEndpointCommand ('x'*10000)
    if(([regex]::Matches($diagnostic,'U\+[0-9A-F]+')).Count-ne 16-or $diagnostic.Length-gt 256){throw 'Unknown command diagnostic is not bounded.'}
    $start=[Diagnostics.ProcessStartInfo]::new()
    $start.FileName=(Get-Process -Id $PID).Path
    $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($childCode))
    $start.Arguments='-NoLogo -NoProfile -NonInteractive -EncodedCommand '+$encoded
    $start.UseShellExecute=$false;$start.CreateNoWindow=$true
    $start.RedirectStandardInput=$true;$start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
    $child=[Diagnostics.Process]::new();$child.StartInfo=$start
    if(-not $child.Start()){throw 'Could not start the synthetic listener child.'}
    $childStarted=$true
    $ready=(Read-ChildEndpointLine $child)|ConvertFrom-Json
    if($ready.process_id-ne $child.Id){throw 'Synthetic listener announced a different process identity.'}
    $proof=Assert-NativeEndpointOwner -ProcessId $child.Id -Port $ready.port
    if($proof.process_id-ne $child.Id){throw 'Native table did not bind child listener ownership.'}
    Reject-EndpointOwner {Assert-NativeEndpointOwner -ProcessId $PID -Port $ready.port}

    $client=[Net.Sockets.TcpClient]::new([Net.Sockets.AddressFamily]::InterNetwork)
    $connect=$client.BeginConnect([Net.IPAddress]::Loopback,[int]$ready.port,$null,$null)
    try {
        if(-not $connect.AsyncWaitHandle.WaitOne(5000)){throw 'Synthetic loopback client connect timed out.'}
        $client.EndConnect($connect)
    }finally{$connect.AsyncWaitHandle.Close()}
    if((Send-ChildEndpointCommand $child 'accept')-cne 'accepted'){throw 'Child did not accept its own test connection.'}
    $clientPort=([Net.IPEndPoint]$client.Client.LocalEndPoint).Port
    $proof=Assert-NativeEndpointOwner -ProcessId $child.Id -Port $ready.port -RemotePort $clientPort
    if($proof.check-cne 'established_server_tuple'-or $proof.remote_port-ne $clientPort-or $proof.tcp_state-ne 5){throw 'Established loopback tuple witness differs.'}
    Reject-EndpointOwner {Assert-NativeEndpointOwner -ProcessId $PID -Port $ready.port -RemotePort $clientPort}
    Reject-EndpointOwner {Assert-NativeEndpointOwner -ProcessId $child.Id -Port $ready.port -RemotePort 1}

    $client.Dispose();$client=$null
    if((Send-ChildEndpointCommand $child 'close_client')-cne 'client_closed'){throw 'Child did not close the test connection.'}
    Reject-EndpointOwner {Assert-NativeEndpointOwner -ProcessId $child.Id -Port $ready.port -RemotePort $clientPort}
    if((Send-ChildEndpointCommand $child 'close_listener')-cne 'listener_closed'){throw 'Child did not close its test listener.'}
    Reject-EndpointOwner {Assert-NativeEndpointOwner -ProcessId $child.Id -Port $ready.port}
    'Native endpoint Windows loopback ownership tests passed.'
} finally {
    if($client){$client.Dispose()}
    if($ownListener){$ownListener.Stop()}
    if($child){
        if($childStarted-and -not $child.HasExited){
            try{Write-ChildEndpointCommand $child 'quit'}catch{}
            if(-not $child.WaitForExit(2000)){$child.Kill();[void]$child.WaitForExit(2000)}
        }
        $child.Dispose()
    }
}

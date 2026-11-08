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
function Send-ChildEndpointCommand($Process,[string]$Command) {
    $Process.StandardInput.WriteLine($Command);$Process.StandardInput.Flush()
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
$listener=$null;$accepted=$null
try {
    $listener=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0)
    $listener.ExclusiveAddressUse=$true;$listener.Start()
    $port=([Net.IPEndPoint]$listener.LocalEndpoint).Port
    [Console]::Out.WriteLine((@{process_id=$PID;port=$port}|ConvertTo-Json -Compress));[Console]::Out.Flush()
    while($true){
        $command=[Console]::ReadLine()
        if($null-eq $command-or $command-eq 'quit'){break}
        switch($command){
            'accept'{$accepted=$listener.AcceptTcpClient();[Console]::Out.WriteLine('accepted');[Console]::Out.Flush()}
            'close_client'{if($accepted){$accepted.Dispose();$accepted=$null};[Console]::Out.WriteLine('client_closed');[Console]::Out.Flush()}
            'close_listener'{$listener.Stop();$listener=$null;[Console]::Out.WriteLine('listener_closed');[Console]::Out.Flush()}
            default{throw 'Unknown synthetic listener command.'}
        }
    }
}finally{if($accepted){$accepted.Dispose()};if($listener){$listener.Stop()}}
'@
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
            try{$child.StandardInput.WriteLine('quit');$child.StandardInput.Flush()}catch{}
            if(-not $child.WaitForExit(2000)){$child.Kill();[void]$child.WaitForExit(2000)}
        }
        $child.Dispose()
    }
}

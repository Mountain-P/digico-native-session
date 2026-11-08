param([Parameter(Mandatory=$true)][string]$EndpointModulePath)
$ErrorActionPreference='Stop'
. $EndpointModulePath
Initialize-NativeEndpointInterop

function Reject-EndpointFixture([scriptblock]$Action) {
    $rejected=$false
    try { & $Action } catch { $rejected=$true }
    if(-not $rejected){throw 'Expected endpoint fixture rejection.'}
}
function Fixture-Bytes([string]$Hex) {
    $text=$Hex.Replace(' ','').Replace("`r",'').Replace("`n",'')
    if($text.Length%2){throw 'Invalid synthetic hex.'}
    $bytes=New-Object byte[] ($text.Length/2)
    for($i=0;$i-lt $bytes.Length;$i++){$bytes[$i]=[Convert]::ToByte($text.Substring(2*$i,2),16)}
    return ,$bytes
}

# Independent SDK-layout vector: count DWORD; two rows of six DWORDs.
# TCP ports are network-order WORDs in the DWORD field (07 ec = 2028),
# addresses retain in_addr byte order, and PID4242 is 92 10 00 00.
$bytes=Fixture-Bytes @'
02000000
02000000 00000000 07ec0000 00000000 ffff0000 92100000
05000000 7f000001 07ec0000 7f000001 c3510000 92100000
'@
if([DiGiCoNativeEndpointOwner]::TableFirstRowOffset-ne 4-or [DiGiCoNativeEndpointOwner]::TableRowStride-ne 24){throw 'MIB table managed ABI differs from the six-DWORD SDK layout.'}
$rows=[DiGiCoNativeEndpointOwner]::DecodeBytes($bytes)
if($rows.Length-ne 2-or $rows[0].LocalPort-ne 2028-or $rows[1].RemotePort-ne 50001-or $rows[1].OwningProcessId-ne 4242){throw 'TCP table stride/port byte order/PID decode differs.'}
if($rows[0].LocalAddress-cne '0.0.0.0'-or $rows[1].LocalAddress-cne '127.0.0.1'-or $rows[1].RemoteAddress-cne '127.0.0.1'){throw 'IPv4 in_addr byte order differs.'}
$upperPortWord=[byte[]]$bytes.Clone();$upperPortWord[14]=0xa5;$upperPortWord[15]=0xb6
if(([DiGiCoNativeEndpointOwner]::DecodeBytes($upperPortWord))[0].LocalPort-ne 2028){throw 'Port decode incorrectly used the upper DWORD word.'}
$listener=[DiGiCoNativeEndpointOwner]::SelectRows($rows,4242,2028,$false,0)
if($listener.State-ne 2){throw 'Listener selector selected a connected row.'}
$server=[DiGiCoNativeEndpointOwner]::SelectRows($rows,4242,2028,$true,50001)
if($server.State-ne 5){throw 'Established selector selected a listener row.'}
Reject-EndpointFixture {[void][DiGiCoNativeEndpointOwner]::SelectRows($rows,4243,2028,$false,0)}
Reject-EndpointFixture {[void][DiGiCoNativeEndpointOwner]::SelectRows($rows,4243,2028,$true,50001)}
Reject-EndpointFixture {[void][DiGiCoNativeEndpointOwner]::SelectRows($rows,4242,2029,$false,0)}
Reject-EndpointFixture {[void][DiGiCoNativeEndpointOwner]::SelectRows($rows,4242,2028,$true,50002)}

# Listener remote address/port are undefined by the API and must not constrain
# listener selection. Its local address must cover exactly IPv4 loopback.
$loopback=[byte[]]$bytes.Clone();[Array]::Copy([byte[]](127,0,0,1),0,$loopback,8,4)
[void][DiGiCoNativeEndpointOwner]::SelectRows([DiGiCoNativeEndpointOwner]::DecodeBytes($loopback),4242,2028,$false,0)
$foreign=[byte[]]$bytes.Clone();[Array]::Copy([byte[]](192,0,2,1),0,$foreign,8,4)
Reject-EndpointFixture {[void][DiGiCoNativeEndpointOwner]::SelectRows([DiGiCoNativeEndpointOwner]::DecodeBytes($foreign),4242,2028,$false,0)}
$foreignPeer=[byte[]]$bytes.Clone();[Array]::Copy([byte[]](192,0,2,2),0,$foreignPeer,40,4)
Reject-EndpointFixture {[void][DiGiCoNativeEndpointOwner]::SelectRows([DiGiCoNativeEndpointOwner]::DecodeBytes($foreignPeer),4242,2028,$true,50001)}

# A client-side inverse tuple is not proof of the server's ownership.
$inverse=Fixture-Bytes '01000000 05000000 7f000001 c3510000 7f000001 07ec0000 92100000'
Reject-EndpointFixture {[void][DiGiCoNativeEndpointOwner]::SelectRows([DiGiCoNativeEndpointOwner]::DecodeBytes($inverse),4242,2028,$true,50001)}
$ambiguous=[DiGiCoTcpOwnerRow[]]@($rows[0],$rows[0])
Reject-EndpointFixture {[void][DiGiCoNativeEndpointOwner]::SelectRows($ambiguous,4242,2028,$false,0)}
$closed=[byte[]]$bytes.Clone();$closed[28]=8
Reject-EndpointFixture {[void][DiGiCoNativeEndpointOwner]::SelectRows([DiGiCoNativeEndpointOwner]::DecodeBytes($closed),4242,2028,$true,50001)}
Reject-EndpointFixture {[void][DiGiCoNativeEndpointOwner]::DecodeBytes([byte[]](0,0,0))}
Reject-EndpointFixture {[void][DiGiCoNativeEndpointOwner]::DecodeBytes([byte[]](255,255,255,255))}
Reject-EndpointFixture {[void][DiGiCoNativeEndpointOwner]::DecodeBytes($bytes[0..27])}
$empty=@([DiGiCoNativeEndpointOwner]::DecodeBytes([byte[]](0,0,0,0)))
if($empty.Length-ne 0){throw 'Empty table acquired phantom rows.'}
'Native endpoint synthetic layout and owner-selection tests passed.'

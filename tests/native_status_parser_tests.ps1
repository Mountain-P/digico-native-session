# Synthetic parser tests only. No sockets, VM, UI or process-memory access.
[CmdletBinding()]
param([string]$StatusModulePath='')
$ErrorActionPreference='Stop'
if (-not $StatusModulePath) { $StatusModulePath=Join-Path $PSScriptRoot '..\skills\digico-native-session\scripts\native_status.ps1' }
. $StatusModulePath

function Assert-StatusTest([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function New-SyntheticStatusFrame([int]$Subcommand,[int]$Version,[byte[]]$Body) {
    $bytes=New-Object byte[] (18+$Body.Length)
    [Array]::Copy([BitConverter]::GetBytes([uint32]$bytes.Length),0,$bytes,0,4)
    $bytes[6]=0
    [Array]::Copy([BitConverter]::GetBytes([uint16]$Subcommand),0,$bytes,7,2)
    [Array]::Copy([BitConverter]::GetBytes([uint16]$Version),0,$bytes,10,2)
    [Array]::Copy($Body,0,$bytes,16,$Body.Length)
    $sum=0;for($i=0;$i-lt $bytes.Length-2;$i++){$sum=($sum+[int]$bytes[$i])-band 65535}
    [Array]::Copy([BitConverter]::GetBytes([uint16]$sum),0,$bytes,$bytes.Length-2,2)
    return ,$bytes
}
function New-SyntheticStatus([int]$Version=2126,[byte[]]$Name=([Text.Encoding]::ASCII.GetBytes('Q1 test')),[byte[]]$Options=([byte[]](0..27))) {
    $identity=[byte[]](0,2,255,0,[byte]$Name.Length)+$Name
    $path=[Text.Encoding]::ASCII.GetBytes('C:\Projects\Synthetic.ses')
    $secondary=[Text.Encoding]::ASCII.GetBytes('Synthetic')
    $session=[BitConverter]::GetBytes([double]45000)+[byte[]]([byte]$path.Length)+$path+[byte[]]([byte]$secondary.Length)+$secondary
    return ,([byte[]]((New-SyntheticStatusFrame 1 $Version $identity)+(New-SyntheticStatusFrame 2 $Version $session)+(New-SyntheticStatusFrame 7 0 $Options)))
}
function Assert-StatusThrows([scriptblock]$Action,[string]$Message) {
    $thrown=$false;try{& $Action|Out-Null}catch{$thrown=$true}
    Assert-StatusTest $thrown $Message
}

$raw=New-SyntheticStatus
$decoded=ConvertFrom-DiGiCoNativeStatusBytes -Bytes $raw
Assert-StatusTest ($decoded.frames.Count-eq 3) 'Expected three valid frames.'
Assert-StatusTest ($decoded.decoded.identity.protocol_version-eq 2126) 'Protocol version mismatch.'
Assert-StatusTest ($decoded.decoded.identity.advertised_name.text-eq 'Q1 test') 'Advertised name mismatch.'
Assert-StatusTest ($decoded.decoded.session.current_session_path.text-eq 'C:\Projects\Synthetic.ses') 'Current path mismatch.'
Assert-StatusTest ($decoded.decoded.session.session_timestamp_tdatetime-eq 45000) 'TDateTime mismatch.'
Assert-StatusTest ($decoded.decoded.options.values_raw.Count-eq 28-and $decoded.decoded.options.values_raw[27]-eq 27) 'Option bytes mismatch.'
Assert-StatusTest ($null-eq $decoded.frames[0].PSObject.Properties['_body']) 'Internal body leaked.'
Assert-StatusTest ($null-eq $decoded.PSObject.Properties['application_bytes_sent']) 'Pure decoder must not assert a network observation.'

$bad=[byte[]]$raw.Clone();$bad[16]=$bad[16]-bxor 1
Assert-StatusThrows {ConvertFrom-DiGiCoNativeStatusBytes -Bytes $bad} 'Checksum corruption accepted.'
Assert-StatusThrows {ConvertFrom-DiGiCoNativeStatusBytes -Bytes ([byte[]]$raw[0..($raw.Length-2)])} 'Truncated stream accepted.'
Assert-StatusThrows {ConvertFrom-DiGiCoNativeStatusBytes -Bytes ([byte[]]@())} 'Empty stream accepted.'
Assert-StatusThrows {ConvertFrom-DiGiCoNativeStatusBytes -Bytes (New-SyntheticStatus -Version 2125)} 'Wrong protocol version accepted.'
$v2242=New-SyntheticStatus -Version 2242
Assert-StatusThrows {ConvertFrom-DiGiCoNativeStatusBytes -Bytes $v2242} '2242 was accepted under default2126.'
$decoded2242=ConvertFrom-DiGiCoNativeStatusBytes -Bytes $v2242 -ExpectedVersion 2242
Assert-StatusTest ($decoded2242.decoded.identity.protocol_version-eq 2242) 'Explicit2242 profile was rejected.'
Assert-StatusThrows {ConvertFrom-DiGiCoNativeStatusBytes -Bytes $raw -ExpectedVersion 2242} '2126 was accepted under explicit2242.'
Assert-StatusThrows {ConvertFrom-DiGiCoNativeStatusBytes -Bytes (New-SyntheticStatus -Options ([byte[]](0..26)))} 'Wrong option extent accepted.'
$firstSize=[BitConverter]::ToInt32($raw,0)
Assert-StatusThrows {ConvertFrom-DiGiCoNativeStatusBytes -Bytes ([byte[]]$raw[0..($firstSize-1)])} 'Missing status frames accepted.'
$longName=[Text.Encoding]::ASCII.GetBytes('1234567890123456789012345678901')
Assert-StatusThrows {ConvertFrom-DiGiCoNativeStatusBytes -Bytes (New-SyntheticStatus -Name $longName)} 'Name beyond native 30-byte bound accepted.'
$nonAscii=ConvertFrom-DiGiCoNativeStatusBytes -Bytes (New-SyntheticStatus -Name ([byte[]](0xA4,0x40)))
Assert-StatusTest ($null-eq $nonAscii.decoded.identity.advertised_name.text-and $nonAscii.decoded.identity.advertised_name.decode_error-and $nonAscii.decoded.identity.advertised_name.raw_hex-eq 'a440') 'Strict text decoder did not preserve raw bytes.'
Write-Output 'Native status synthetic parser tests passed.'

# Synthetic parser/preflight tests only. No sockets, VM, Session I/O or UI.
[CmdletBinding()]
param([string]$TransferModulePath='')
$ErrorActionPreference='Stop'
if (-not $TransferModulePath) { $TransferModulePath=Join-Path $PSScriptRoot '..\skill\digico-native-session\scripts\native_transfer.ps1' }
. $TransferModulePath

function Assert-TransferTest([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-TransferThrows([scriptblock]$Action,[string]$Message) {
    $failed=$false;try{& $Action|Out-Null}catch{$failed=$true}
    Assert-TransferTest $failed $Message
}
function New-TransferTestFrame([int]$Subcommand,[byte[]]$Body,[int]$FileType=3,[int]$CommandId=0,[int]$HeaderByte9=0) {
    if ($Body.Length-lt 2) {$Body=$Body+(New-Object byte[] (2-$Body.Length))}
    $packet=New-Object byte[] (18+$Body.Length)
    [Array]::Copy([BitConverter]::GetBytes([uint32]$packet.Length),0,$packet,0,4)
    [Array]::Copy([BitConverter]::GetBytes([uint16]255),0,$packet,4,2)
    $packet[6]=4;$packet[7]=[byte]$Subcommand;$packet[8]=[byte]$FileType;$packet[9]=[byte]$HeaderByte9
    [Array]::Copy([BitConverter]::GetBytes([uint16]$CommandId),0,$packet,10,2)
    [Array]::Copy($Body,0,$packet,16,$Body.Length)
    $sum=0;for($i=0;$i-lt $packet.Length-2;$i++){$sum=($sum+[int]$packet[$i])-band 65535}
    [Array]::Copy([BitConverter]::GetBytes([uint16]$sum),0,$packet,$packet.Length-2,2)
    return ,$packet
}
function New-TransferReceiveState {
    return [pscustomobject]@{offset=0;initial_offset=0;maximum_rx_bytes=1048576;timer=[Diagnostics.Stopwatch]::StartNew()}
}
function Send-Bytes([byte[]]$Bytes) { throw 'Synthetic parser unexpectedly attempted a send.' }
function Frame([int]$Category,[int]$Group,[int]$Control,[int]$Index,[byte[]]$Body) {
    if ($Category-ne 4-or $Index-ne 0) {throw 'Unexpected synthetic ACK fields.'}
    return ,(New-TransferTestFrame ($Control-band 255) $Body (($Control-shr 8)-band 255) $Group)
}

$raw=New-TransferTestFrame 2 ([byte[]](1,2,3,4))
$rx=[IO.MemoryStream]::new();$rx.Write($raw,0,$raw.Length)
$clock=[Diagnostics.Stopwatch]::StartNew()
$state=New-TransferReceiveState
$frame=Read-DiGiCoTransferFrame $state 1000
Assert-TransferTest ($frame.subcommand-eq 2-and $frame.file_type-eq 3-and $frame.command_id-eq 0-and $frame.body.Length-eq 4) 'Valid native frame mismatch.'
Assert-TransferTest ($state.offset-eq $raw.Length) 'Receive cursor did not advance exactly once.'
$rx.Dispose()

# Real native block builder leaves byte9 unspecified. ACK copies it as raw data
# while preserving all meaningful IDs and the original n_block/prefix.
$record=New-Object byte[] 153
[Array]::Copy([BitConverter]::GetBytes([int32]1),0,$record,0,4)
[Array]::Copy([BitConverter]::GetBytes([int32]2),0,$record,4,4)
[Array]::Copy([BitConverter]::GetBytes([uint16]3),0,$record,8,2)
[Array]::Copy([BitConverter]::GetBytes([uint16]3),0,$record,10,2)
$record[150]=65;$record[151]=66;$record[152]=67
foreach($byte9 in @(0,0x23,0xA5)) {
    $blockBytes=New-TransferTestFrame 2 $record 3 0 $byte9
    $rx=[IO.MemoryStream]::new();$rx.Write($blockBytes,0,$blockBytes.Length);$state=New-TransferReceiveState
    $frame=Read-DiGiCoTransferFrame $state 1000
    $ack=New-DiGiCoNativeFileAck $frame
    $prefix=New-Object byte[] 150;[Array]::Copy($record,0,$prefix,0,150)
    $expected=New-TransferTestFrame 3 $prefix 3 0 $byte9
    Assert-TransferTest ((ConvertTo-DiGiCoTransferHex $ack)-eq (ConvertTo-DiGiCoTransferHex $expected)) 'ACK did not preserve complete incoming ControlID and checksum.'
    Assert-TransferTest ([BitConverter]::ToInt32($ack,16)-eq 1) 'ACK incremented wire block index.'
    $rx.Dispose()
}

# Prefix already logged by Connect-Native, tail arrives from a fake stream.
$rx=[IO.MemoryStream]::new();$rx.Write($raw,0,7)
$tail=New-Object byte[] ($raw.Length-7);[Array]::Copy($raw,7,$tail,0,$tail.Length)
$stream=[pscustomobject]@{DataAvailable=$true;bytes=$tail;read_at=0}
$stream|Add-Member -MemberType ScriptMethod -Name Read -Value {
    param([byte[]]$Buffer,[int]$Offset,[int]$Length)
    $count=[math]::Min($Length,$this.bytes.Length-$this.read_at)
    [Array]::Copy($this.bytes,$this.read_at,$Buffer,$Offset,$count)
    $this.read_at+=$count;$this.DataAvailable=($this.read_at-lt $this.bytes.Length)
    return $count
}
$socket=[pscustomobject]@{}
$socket|Add-Member -MemberType ScriptMethod -Name Poll -Value {param($Timeout,$Mode) return $false}
$native=[pscustomobject]@{Client=[pscustomobject]@{Client=$socket;Available=$tail.Length};Stream=$stream}
$clock.Restart();$state=New-TransferReceiveState
$frame=Read-DiGiCoTransferFrame $state 1000
Assert-TransferTest ((ConvertTo-DiGiCoTransferHex $frame.raw)-eq (ConvertTo-DiGiCoTransferHex $raw)) 'Fragmented frame was not reassembled with logged prefix.'
Assert-TransferTest ($rx.Length-eq $raw.Length) 'New stream bytes were not added exactly once to RX log.'
$rx.Dispose()

$bad=[byte[]]$raw.Clone();$bad[16]=$bad[16]-bxor 1
$rx=[IO.MemoryStream]::new();$rx.Write($bad,0,$bad.Length);$state=New-TransferReceiveState
Assert-TransferThrows {Read-DiGiCoTransferFrame $state 1000} 'Corrupted checksum accepted.'
$rx.Dispose()
$rx=[IO.MemoryStream]::new();$size=[BitConverter]::GetBytes([uint32]16553);$rx.Write($size,0,4);$state=New-TransferReceiveState
Assert-TransferThrows {Read-DiGiCoTransferFrame $state 1000} 'Oversized frame accepted.'
$rx.Dispose()

$verify=New-Object byte[] 131;$verify[0]=1
Assert-DiGiCoExportVerify $verify
foreach($byte11 in @(0,0x23,0xA5)) {
    $verifyRaw=New-TransferTestFrame 6 $verify 0 ($byte11-shl 8)
    $rx=[IO.MemoryStream]::new();$rx.Write($verifyRaw,0,$verifyRaw.Length);$state=New-TransferReceiveState
    $verifyFrame=Read-DiGiCoTransferFrame $state 1000
    Assert-DiGiCoExportVerifyFrame $verifyFrame
    $rx.Dispose()
}
$wrong=[byte[]]$verify.Clone();$wrong[0]=0
Assert-TransferThrows {Assert-DiGiCoExportVerify $wrong} 'Wrong verify direction accepted.'
$wrong=[byte[]]$verify.Clone();$wrong[128]=255
Assert-TransferThrows {Assert-DiGiCoExportVerify $wrong} 'Truncated verify string accepted.'

$path=Assert-DiGiCoTransferPath 'C:\Synthetic\Source.ses' 127
Assert-TransferTest ($path.Length-eq 23) 'ASCII source path extent mismatch.'
Assert-TransferThrows {Assert-DiGiCoTransferPath 'C:\Synthetic\..\Source.ses' 127} 'Traversal path accepted.'
Assert-TransferThrows {Assert-DiGiCoTransferPath '\\Server\Share\Source.ses' 127} 'UNC path accepted.'
Assert-TransferThrows {Assert-DiGiCoTransferPath 'C:\Synthetic\Source.bin' 127} 'Non-Session path accepted.'
Assert-TransferThrows {Assert-DiGiCoTransferPath 'C:\Synthetic\Source.ses' 10} 'Oversized source path accepted.'

# Read-only mock peer-table/save-worker checks; no process is opened.
$base=0;$script:worker=$null;$script:transferPointer=0
function Only-Loopback-Peers {}
function Read-SaveWorker {return $script:worker}
$reader=[pscustomobject]@{}
$reader|Add-Member -MemberType ScriptMethod -Name U32 -Value {
    param([long]$Address)
    switch($Address){0x126E670{return 1000};1004{return 2000};2000{return 3000};0xCDD{return $script:transferPointer};default{throw "Unexpected mock read $Address"}}
}
$reader|Add-Member -MemberType ScriptMethod -Name I32 -Value {param([long]$Address) if($Address-eq 1008){return 1};throw 'Unexpected mock count read.'}
Assert-DiGiCoTransferIdle
$script:worker=[pscustomobject]@{finished=1;terminated=0}
Assert-TransferThrows {Assert-DiGiCoTransferIdle} 'Finished but nonterminated save worker accepted.'
$script:worker=[pscustomobject]@{finished=1;terminated=1}
Assert-DiGiCoTransferIdle
$script:transferPointer=123
Assert-TransferThrows {Assert-DiGiCoTransferIdle} 'Active transfer state accepted.'
Write-Output 'Native transfer synthetic parser/preflight tests passed.'

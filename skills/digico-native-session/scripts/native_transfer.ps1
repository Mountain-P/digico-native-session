# Quantum1 V2126 file-only native export. Dot-source functions only.
# Requires the version-gated runner's connected $native, $reader/$base,
# $tx/$rx, Frame, Send-Bytes, Disconnect-Native and Check-SessionFile helpers.

function ConvertTo-DiGiCoTransferHex([byte[]]$Bytes) {
    return ([BitConverter]::ToString($Bytes)).Replace('-','').ToLowerInvariant()
}

function Assert-DiGiCoTransferPath([string]$Path,[int]$MaxBytes) {
    if ($Path -notmatch '^[A-Za-z]:\\' -or $Path -match '[\x00-\x1f*?"<>|]' -or
        $Path.Substring(2).Contains(':') -or [IO.Path]::GetExtension($Path) -ine '.ses' -or
        @($Path.Split('\') | Where-Object { $_ -eq '.' -or $_ -eq '..' }).Count -gt 0) {
        throw 'Native export requires an absolute local-drive .ses path without traversal or wildcard characters.'
    }
    $encoding=[Text.Encoding]::GetEncoding(20127,[Text.EncoderExceptionFallback]::new(),[Text.DecoderExceptionFallback]::new())
    $bytes=$encoding.GetBytes($Path)
    if ($bytes.Length -gt $MaxBytes) { throw "Native export path exceeds $MaxBytes ASCII bytes." }
    return ,$bytes
}

function Assert-DiGiCoTransferIdle {
    Only-Loopback-Peers
    $worker=Read-SaveWorker
    # BF3684 calls 935A98(1), whose branch uses Terminated+0E rather than
    # Finished+11. Require both when an old worker object still exists.
    if ($worker -and ($worker.terminated -eq 0 -or $worker.finished -eq 0)) {
        throw 'Native export requires no save worker, or a terminated and finished worker; no file request was sent.'
    }
    $manager=[long]$reader.U32($base+0x126E670)
    if ($manager -eq 0) { throw 'Native networking is not initialized.' }
    $count=$reader.I32($manager+8)
    $items=[long]$reader.U32($manager+4)
    if ($count -lt 0 -or $count -gt 256) { throw 'Invalid native peer-table bounds.' }
    for ($i=0; $i -lt $count; $i++) {
        $peer=[long]$reader.U32($items+4*$i)
        if ($peer -eq 0) { throw 'Invalid native peer pointer.' }
        if ($reader.U32($peer+0x125) -ne 0) { throw 'Another native file transfer is active; no file request was sent.' }
    }
}

function Read-DiGiCoTransferFrame($ReceiveState,[int]$TimeoutMs) {
    # Consume the common RX log, including a frame partly read by Connect-Native's
    # Pump. New stream reads append to that same log; no bytes are discarded.
    $size=0
    while ($true) {
        if ($ReceiveState.timer.ElapsedMilliseconds -ge $TimeoutMs) { throw 'Native file export timed out.' }
        $available=$rx.Length-$ReceiveState.offset
        if ($size -eq 0 -and $available -ge 4) {
            $captured=$rx.GetBuffer()
            $size=[BitConverter]::ToUInt32($captured,[int]$ReceiveState.offset)
            if ($size -lt 20 -or $size -gt 16552) { throw 'Invalid/oversized native frame during file export.' }
        }
        if ($size -gt 0 -and $available -ge $size) {
            $captured=$rx.GetBuffer()
            $raw=New-Object byte[] ([int]$size)
            [Array]::Copy($captured,[int]$ReceiveState.offset,$raw,0,[int]$size)
            $ReceiveState.offset += [int]$size
            $sum=0
            for ($j=0; $j -lt $raw.Length-2; $j++) { $sum=($sum+[int]$raw[$j]) -band 65535 }
            if ($sum -ne [BitConverter]::ToUInt16($raw,$raw.Length-2)) { throw 'Invalid native checksum during file export.' }
            $body=New-Object byte[] ($raw.Length-18)
            [Array]::Copy($raw,16,$body,0,$body.Length)
            return [pscustomobject]@{
                raw=$raw;body=$body;size=$raw.Length;category=[int]$raw[6]
                subcommand=[int]$raw[7];file_type=[int]$raw[8]
                command_id=[int][BitConverter]::ToUInt16($raw,10)
            }
        }
        if ($clock.ElapsedMilliseconds -ge 2000) {
            # Ordinary native peer liveness only; not an ACK or transfer retry.
            Send-Bytes (Frame 0 0 0 0 ([byte[]](127,0,0,1)))
        }
        if ($native.Client.Client.Poll(0,[Net.Sockets.SelectMode]::SelectRead) -and $native.Client.Available -eq 0) {
            throw 'Native peer closed before file-only export completed.'
        }
        if ($native.Stream.DataAvailable) {
            $buffer=New-Object byte[] 32768
            $n=$native.Stream.Read($buffer,0,$buffer.Length)
            if ($n -le 0) { throw 'Native file export reached unexpected EOF.' }
            if (($rx.Length+$n-$ReceiveState.initial_offset) -gt $ReceiveState.maximum_rx_bytes) {
                throw 'Native file export exceeded its receive-byte bound.'
            }
            $rx.Write($buffer,0,$n)
        } else { [Threading.Thread]::Sleep(10) }
    }
}

function Assert-DiGiCoExportVerify([byte[]]$Body) {
    if ($Body.Length -lt 131 -or $Body[0] -ne 1) { throw 'Unexpected file-only SessionVerify direction/extent.' }
    $offset=128
    for ($i=0; $i -lt 3; $i++) {
        if ($offset -ge $Body.Length) { throw 'Truncated file-only SessionVerify string.' }
        $offset += 1+[int]$Body[$offset]
        if ($offset -gt $Body.Length) { throw 'Truncated file-only SessionVerify bytes.' }
    }
    if ($offset -ne $Body.Length) { throw 'Unexpected file-only SessionVerify trailing bytes.' }
}

function Assert-DiGiCoExportVerifyFrame($Frame) {
    # BF13D8 initializes DWORD+7 (bytes7..10) then writes subcommand6.
    # Byte11 is not initialized or used by the Category4/sub6 dispatcher.
    if ($Frame.category -ne 4 -or $Frame.subcommand -ne 6 -or
        $Frame.raw[8] -ne 0 -or $Frame.raw[9] -ne 0 -or $Frame.raw[10] -ne 0) {
        throw 'Unexpected file-only SessionVerify initialized identifier bytes.'
    }
    Assert-DiGiCoExportVerify $Frame.body
}

function New-DiGiCoNativeFileAck($Frame) {
    $prefix=New-Object byte[] 150
    [Array]::Copy($Frame.body,0,$prefix,0,150)
    $ack=Frame 4 $Frame.command_id (($Frame.file_type -shl 8) -bor 3) 0 $prefix
    # BF3BFF..BF3C0E copies the incoming complete five-byte ControlID, then
    # changes only its first byte to3. Packet byte9 is not initialized by the
    # native block constructor/allocator and is not a Category4 discriminator.
    # Keep the checksum-validated raw byte rather than inventing a zero/# rule.
    [Array]::Copy($Frame.raw,7,$ack,7,5)
    $ack[7]=3
    $sum=0
    for ($i=0; $i -lt $ack.Length-2; $i++) { $sum=($sum+[int]$ack[$i]) -band 65535 }
    [Array]::Copy([BitConverter]::GetBytes([uint16]$sum),0,$ack,$ack.Length-2,2)
    return ,$ack
}

function Invoke-DiGiCoNativeFileExport {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$SourcePath,
        [Parameter(Mandatory=$true)][string]$DestinationPath,
        [ValidateRange(1000,120000)][int]$TimeoutMs=30000,
        [ValidateRange(0,2147483647)][long]$ReceiveStartOffset=0
    )
    $stage=$null
    $output=$null
    $requestSent=$false
    try {
        $sourceBytes=Assert-DiGiCoTransferPath $SourcePath 127
        $null=Assert-DiGiCoTransferPath $DestinationPath 255
        if ($SourcePath -ieq $DestinationPath) { throw 'Native export source and destination must differ.' }
        if (Test-Path -LiteralPath $DestinationPath) { throw 'Native export destination already exists.' }
        $parent=[IO.Path]::GetDirectoryName($DestinationPath)
        if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw 'Native export destination parent does not exist.' }
        $sourceHash=Check-SessionFile $SourcePath
        $sourceInfo=Get-Item -LiteralPath $SourcePath
        $sourceLength=[long]$sourceInfo.Length
        # This is a client resource bound, not a native console Session limit.
        if ($sourceLength -lt 36 -or $sourceLength -gt 67108864) { throw 'Native export source is outside the local 36-byte..64-MiB bound.' }
        if ($null -eq $native.Client -or $null -eq $native.Stream) { throw 'Native file export requires the runner normal identity connection.' }
        $remote=$native.Client.Client.RemoteEndPoint
        if ($remote.Address.ToString() -ne '127.0.0.1' -or $remote.Port -ne 2028) { throw 'Native file export requires the loopback2028 peer.' }
        if ($ReceiveStartOffset -gt $rx.Length) { throw 'Invalid native export receive-log starting offset.' }
        Assert-DiGiCoTransferIdle
        $expectedBlocks=[int][math]::Ceiling($sourceLength/16384.0)
        $receive=[pscustomobject]@{
            offset=$ReceiveStartOffset;initial_offset=$ReceiveStartOffset
            maximum_rx_bytes=$sourceLength+[long]$expectedBlocks*168+1048576
            timer=[Diagnostics.Stopwatch]::StartNew()
        }
        $stage=$DestinationPath+'.partial-'+[Guid]::NewGuid().ToString('N')
        $output=[IO.FileStream]::new($stage,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        # Explicit nonempty source, FileType3, CommandID0. This avoids the native
        # empty-source Save and the FileType0/4 final Load command branches.
        $requestBody=[byte[]]([byte[]]([byte]$sourceBytes.Length)+$sourceBytes)
        Send-Bytes (Frame 4 0 0x0301 0 $requestBody)
        $requestSent=$true
        $nextBlock=0
        $uniqueId=-1
        $receivedBytes=[long]0
        $ignoredFrames=0
        $receivedVerify=$false
        $blockResults=[Collections.Generic.List[object]]::new()
        while (-not $receivedVerify) {
            $frame=Read-DiGiCoTransferFrame $receive $TimeoutMs
            if ($frame.category -eq 0) {
                # Identity/status/ordinary liveness may precede or interleave.
                $ignoredFrames++
                if ($ignoredFrames -gt 128) { throw 'Too many unrelated native frames during export.' }
                continue
            }
            if ($frame.category -ne 4) { throw 'Unexpected non-status category during file-only export.' }
            if ($frame.subcommand -eq 6) {
                if ($nextBlock -ne $expectedBlocks -or $receivedBytes -ne $sourceLength) { throw 'SessionVerify arrived before all expected file bytes.' }
                Assert-DiGiCoExportVerifyFrame $frame
                $receivedVerify=$true
                continue
            }
            if ($frame.subcommand -ne 2) {
                # In particular do not ACK FileStatus Load/Save or Complete.
                throw "Unexpected native file-only subcommand $($frame.subcommand); no editor-command acknowledgement was sent."
            }
            if ($nextBlock -ge $expectedBlocks) { throw 'Unexpected extra native file block.' }
            if ($frame.file_type -ne 3 -or $frame.command_id -ne 0 -or $frame.body.Length -lt 150) {
                throw 'Native file block does not match FileType3/CommandID0 profile.'
            }
            $body=$frame.body
            $block=[BitConverter]::ToInt32($body,0)
            $blocks=[BitConverter]::ToInt32($body,4)
            $blockSize=[int][BitConverter]::ToUInt16($body,8)
            $recordType=[int][BitConverter]::ToUInt16($body,10)
            $recordCommand=[int][BitConverter]::ToUInt16($body,12)
            $id=[int][BitConverter]::ToUInt16($body,14)
            $errorCode=[int][BitConverter]::ToUInt16($body,16)
            $nameLength=[int]$body[22]
            $wantedSize=[int][math]::Min(16384,$sourceLength-$receivedBytes)
            if ($block -ne $nextBlock -or $blocks -ne $expectedBlocks -or $recordType -ne 3 -or $recordCommand -ne 0 -or
                $errorCode -ne 0 -or $blockSize -ne $wantedSize -or $frame.body.Length -ne 150+$blockSize -or
                $nameLength -ne $sourceBytes.Length -or $nameLength -gt 127) { throw 'Native file block metadata/sequence/extent mismatch.' }
            for ($j=0; $j -lt $sourceBytes.Length; $j++) {
                if ($body[23+$j] -ne $sourceBytes[$j]) { throw 'Native file block filename differs from the explicit source.' }
            }
            if ($uniqueId -lt 0) { $uniqueId=$id } elseif ($id -ne $uniqueId) { throw 'Native file transfer UniqueID changed.' }
            $output.Write($body,150,$blockSize)
            $output.Flush()
            $receivedBytes += $blockSize
            # BF3B30 copies this original block index. Increment only our local
            # expected index after acknowledging bytes actually written.
            Send-Bytes (New-DiGiCoNativeFileAck $frame)
            $blockResults.Add([pscustomobject]@{n_block=$block;n_blocks=$blocks;block_size=$blockSize;unique_id=$id;header_byte9_raw=[int]$frame.raw[9]})
            $nextBlock++
        }
        $output.Dispose();$output=$null
        # File-only close: do not send SessionVerified(true), a fake Load/Save
        # completion, or SessionVerified(false), whose original path shows error UI.
        Disconnect-Native
        $finalSourceHash=Check-SessionFile $SourcePath
        if ($finalSourceHash -ne $sourceHash -or (Get-Item -LiteralPath $SourcePath).Length -ne $sourceLength) { throw 'Native export source changed during transfer.' }
        $receivedHash=(Get-FileHash -LiteralPath $stage -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($receivedHash -ne $sourceHash -or (Get-Item -LiteralPath $stage).Length -ne $sourceLength) { throw 'Received native Session bytes do not match the source.' }
        # Check the transferred native header before publishing the new file.
        # Check-SessionFile requires .ses, so use explicit header bytes here.
        $handle=[IO.File]::OpenRead($stage)
        try {
            $header=New-Object byte[] 36
            if ($handle.Read($header,0,36) -ne 36) { throw 'Truncated transferred native Session header.' }
        } finally { $handle.Dispose() }
        if ([Text.Encoding]::ASCII.GetString($header,0,26) -ne "DiGiCo     Q1  .SES vN   `r" -or [BitConverter]::ToUInt16($header,34) -ne 2126) {
            throw 'Transferred Session header does not match the verified Q1 V2126 profile.'
        }
        if (Test-Path -LiteralPath $DestinationPath) { throw 'Native export destination appeared during transfer; refusing overwrite.' }
        [IO.File]::Move($stage,$DestinationPath)
        $stage=$null
        return [pscustomobject][ordered]@{
            schema_version=1;subsystem='native-file-export';status='ok'
            transport='native-tcp-loopback';file_type=3;command_id=0
            source_path=$SourcePath;destination_path=$DestinationPath
            file_received=$true;source_sha256=$sourceHash;sha256=$receivedHash
            source_unchanged=$true;file_bytes=$sourceLength
            blocks=@($blockResults.ToArray());received_verify=$receivedVerify
            mirror_accepted=$false;session_loaded_on_client=$false
            completion_mode='file-only-disconnect'
        }
    } catch {
        if ($output) { $output.Dispose();$output=$null }
        if ($stage -and (Test-Path -LiteralPath $stage)) {
            $_.Exception.Data['recovery_stage_path']=$stage
            if ($null -ne (Get-Variable -Name result -ErrorAction SilentlyContinue)) { $result.recovery_stage_path=$stage }
        }
        $_.Exception.Data['file_request_sent']=$requestSent
        throw
    } finally {
        if ($output) { $output.Dispose() }
        if ($native.Client) {
            # Preserve the original error if disconnect cleanup also fails.
            try { Disconnect-Native } catch { if ($null -ne (Get-Variable -Name result -ErrorAction SilentlyContinue)) { $result.native_disconnect_error=$_.Exception.Message } }
        }
    }
}

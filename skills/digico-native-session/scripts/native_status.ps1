# Quantum1 V2126 TCP2029 status decoder/query. Dot-source functions only.
# Status response strings may contain private Session names/paths: keep results local.

function ConvertTo-DiGiCoStatusHex([byte[]]$Bytes) {
    return ([BitConverter]::ToString($Bytes)).Replace('-','').ToLowerInvariant()
}

function Get-DiGiCoStatusEncoding([string]$TextEncoding) {
    $code = switch ($TextEncoding) {
        'ascii' { 20127 }
        'cp950' { 950 }
        'utf-8' { 65001 }
        default { throw 'Unsupported status text encoding; use ascii, cp950 or utf-8.' }
    }
    return [Text.Encoding]::GetEncoding($code,[Text.EncoderExceptionFallback]::new(),[Text.DecoderExceptionFallback]::new())
}

function ConvertFrom-DiGiCoStatusText([byte[]]$Bytes,[string]$TextEncoding) {
    $encoding = Get-DiGiCoStatusEncoding $TextEncoding
    $text = $null
    $decodeError = $false
    try { $text = $encoding.GetString($Bytes) } catch { $decodeError = $true }
    return [pscustomobject][ordered]@{
        text = $text
        raw_hex = (ConvertTo-DiGiCoStatusHex $Bytes)
        encoding = $TextEncoding
        decode_error = $decodeError
    }
}

function Read-DiGiCoStatusShortString([byte[]]$Bytes,[int]$Offset,[string]$TextEncoding) {
    if ($Offset -lt 0 -or $Offset -ge $Bytes.Length) { throw 'Truncated native status ShortString length.' }
    $length = [int]$Bytes[$Offset]
    if ($length -gt ($Bytes.Length - $Offset - 1)) { throw 'Truncated native status ShortString bytes.' }
    $value = New-Object byte[] $length
    if ($length -gt 0) { [Array]::Copy($Bytes,$Offset+1,$value,0,$length) }
    return [pscustomobject]@{
        value = (ConvertFrom-DiGiCoStatusText $value $TextEncoding)
        next_offset = $Offset+1+$length
    }
}

function ConvertFrom-DiGiCoNativeStatusBytes {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][AllowEmptyCollection()][byte[]]$Bytes,
        [ValidateSet('ascii','cp950','utf-8')][string]$TextEncoding='ascii',
        [ValidateRange(1,65535)][int]$ExpectedVersion=2126
    )
    # The selected build owns the expected version; matching frame shapes alone
    # never admit another version. Default preserves the legacy pure parser API.
    if ($Bytes.Length -eq 0 -or $Bytes.Length -gt 65536) { throw 'Native status response is empty or exceeds the local 65536-byte bound.' }
    $frames = [Collections.Generic.List[object]]::new()
    $offset = 0
    while ($offset -lt $Bytes.Length) {
        if (($Bytes.Length-$offset) -lt 20) { throw 'Truncated native status frame.' }
        $size = [BitConverter]::ToUInt32($Bytes,$offset)
        if ($size -lt 20 -or $size -gt 65536 -or $size -gt ($Bytes.Length-$offset)) { throw 'Invalid native status frame size.' }
        $sum = 0
        for ($i=0; $i -lt ($size-2); $i++) { $sum = ($sum+[int]$Bytes[$offset+$i]) -band 65535 }
        if ($sum -ne [BitConverter]::ToUInt16($Bytes,$offset+[int]$size-2)) { throw 'Invalid native status checksum.' }
        $body = New-Object byte[] ([int]$size-18)
        [Array]::Copy($Bytes,$offset+16,$body,0,$body.Length)
        $control = New-Object byte[] 5
        [Array]::Copy($Bytes,$offset+7,$control,0,5)
        $frames.Add([pscustomobject][ordered]@{
            stream_offset = $offset
            size = [int]$size
            connection_index = [int][BitConverter]::ToUInt16($Bytes,$offset+4)
            category = [int]$Bytes[$offset+6]
            subcommand = [int][BitConverter]::ToUInt16($Bytes,$offset+7)
            control_id_hex = (ConvertTo-DiGiCoStatusHex $control)
            version_or_group = [int][BitConverter]::ToUInt16($Bytes,$offset+10)
            list_index = [BitConverter]::ToInt32($Bytes,$offset+12)
            body_bytes = $body.Length
            body_hex = (ConvertTo-DiGiCoStatusHex $body)
            checksum_valid = $true
            # Kept internal until the known status shapes have been validated.
            _body = $body
        })
        $offset += [int]$size
    }
    if ($frames.Count -ne 3) { throw 'Expected exactly three native status frames.' }
    for ($i=0; $i -lt 3; $i++) {
        if ($frames[$i].category -ne 0 -or $frames[$i].subcommand -ne (@(1,2,7)[$i])) { throw 'Unexpected native status frame sequence.' }
    }
    if ($frames[0].version_or_group -ne $ExpectedVersion -or $frames[1].version_or_group -ne $ExpectedVersion -or $frames[2].version_or_group -ne 0) { throw 'Native status protocol version/profile mismatch.' }
    $identityBody = $frames[0]._body
    if ($identityBody.Length -lt 5) { throw 'Truncated native identity status.' }
    $advertisedName = Read-DiGiCoStatusShortString $identityBody 4 $TextEncoding
    if ($advertisedName.next_offset -ne $identityBody.Length -or $identityBody[4] -gt 30) { throw 'Invalid native identity name extent.' }
    $sessionBody = $frames[1]._body
    if ($sessionBody.Length -lt 10) { throw 'Truncated native session status.' }
    $path = Read-DiGiCoStatusShortString $sessionBody 8 $TextEncoding
    $secondary = Read-DiGiCoStatusShortString $sessionBody $path.next_offset $TextEncoding
    if ($secondary.next_offset -ne $sessionBody.Length) { throw 'Unexpected trailing bytes in native session status.' }
    $timestamp = New-Object byte[] 8
    [Array]::Copy($sessionBody,0,$timestamp,0,8)
    $timestampValue = [BitConverter]::ToDouble($timestamp,0)
    if ([double]::IsNaN($timestampValue) -or [double]::IsInfinity($timestampValue)) { throw 'Invalid native Session TDateTime value.' }
    if ($frames[2]._body.Length -ne 28) { throw 'Expected 28 native option status bytes.' }
    $decoded = [pscustomobject][ordered]@{
        identity = [pscustomobject][ordered]@{
            protocol_version = $frames[0].version_or_group
            advertised_name = $advertisedName.value
            status_argument_raw = [int]$identityBody[0]
            role_flags_raw = [int]$identityBody[1]
            network_id_raw = [int]$identityBody[2]
            integration_raw = [int]$identityBody[3]
        }
        session = [pscustomobject][ordered]@{
            protocol_version = $frames[1].version_or_group
            current_session_path = $path.value
            session_timestamp_tdatetime = $timestampValue
            session_timestamp_hex = (ConvertTo-DiGiCoStatusHex $timestamp)
            # Source slot CE9650: its business meaning has not been established.
            secondary_shortstring = $secondary.value
        }
        options = [pscustomobject][ordered]@{
            values_raw = @($frames[2]._body | ForEach-Object { [int]$_ })
            raw_hex = $frames[2].body_hex
            named_options_verified = $false
        }
    }
    foreach ($frame in $frames) { $frame.PSObject.Properties.Remove('_body') }
    return [pscustomobject][ordered]@{
        schema_version = 1
        subsystem = 'native-status'
        status = 'ok'
        protocol_profile = 'Quantum1-V2126'
        application_bytes_received = $Bytes.Length
        frames = @($frames.ToArray())
        decoded = $decoded
        contains_private_session_metadata = $true
    }
}

function Invoke-DiGiCoNativeStatus {
    [CmdletBinding()]
    param(
        [ValidateRange(100,30000)][int]$TimeoutMs=3000,
        [ValidateSet('ascii','cp950','utf-8')][string]$TextEncoding='ascii'
    )
    Assert-NativeBuildFeature 'status'
    $client = $null
    $bytes = [IO.MemoryStream]::new()
    $timer = [Diagnostics.Stopwatch]::StartNew()
    try {
        $client = [Net.Sockets.TcpClient]::new()
        $connect = $client.BeginConnect('127.0.0.1',[int]$script:NativeBuildProfile.transport.status_tcp_port,$null,$null)
        try {
            if (-not $connect.AsyncWaitHandle.WaitOne($TimeoutMs)) { throw 'Native status connection timed out.' }
            $client.EndConnect($connect)
        } finally { $connect.AsyncWaitHandle.Close() }
        $stream = $client.GetStream()
        $buffer = New-Object byte[] 8192
        while ($true) {
            $remaining = $TimeoutMs-[int]$timer.ElapsedMilliseconds
            if ($remaining -le 0) { throw 'Native status response timed out before server EOF.' }
            $stream.ReadTimeout = [math]::Max(1,$remaining)
            $n = $stream.Read($buffer,0,$buffer.Length)
            if ($n -le 0) { break }
            if (($bytes.Length+$n) -gt 65536) { throw 'Native status response exceeded the local 65536-byte bound.' }
            $bytes.Write($buffer,0,$n)
        }
        $parsed = ConvertFrom-DiGiCoNativeStatusBytes -Bytes $bytes.ToArray() -TextEncoding $TextEncoding -ExpectedVersion ([int]$script:NativeBuildProfile.transport.protocol_version)
        $parsed | Add-Member -NotePropertyName transport -NotePropertyValue 'native-tcp-loopback'
        $parsed | Add-Member -NotePropertyName host_address -NotePropertyValue '127.0.0.1'
        $parsed | Add-Member -NotePropertyName port -NotePropertyValue $script:NativeBuildProfile.transport.status_tcp_port
        $parsed | Add-Member -NotePropertyName application_bytes_sent -NotePropertyValue 0
        $parsed | Add-Member -NotePropertyName server_closed_connection -NotePropertyValue $true
        return $parsed
    } finally {
        if ($client) { $client.Dispose() }
        $bytes.Dispose()
    }
}

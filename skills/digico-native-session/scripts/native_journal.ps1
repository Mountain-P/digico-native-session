# Append-only, hash-chained operation receipts. No target writes or network.
# Flush(true) requests stable storage; durability of a VM shared filesystem is
# outside this module. Never use a receipt as permission to replay a write.
$script:NativeJournal=$null
function Get-NativeJournalSHA([byte[]]$Bytes){
    $sha=[Security.Cryptography.SHA256]::Create()
    try{return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
}
function Write-NativeJournal([string]$Event,$Data){
    if($null-eq $script:NativeJournal){return}
    $j=$script:NativeJournal
    $payload=[ordered]@{schema_version=1;sequence=$j.Sequence;previous_sha256=$j.Previous;utc=[DateTime]::UtcNow.ToString('o');elapsed_ms=$j.Timer.ElapsedMilliseconds;event=$Event;data=$Data}
    $text=$payload|ConvertTo-Json -Depth 40 -Compress
    $bytes=$j.Encoding.GetBytes($text);$hash=Get-NativeJournalSHA $bytes
    $line=([ordered]@{payload_json=$text;sha256=$hash}|ConvertTo-Json -Compress)+"`n"
    $encoded=$j.Encoding.GetBytes($line);$j.Stream.Write($encoded,0,$encoded.Length);$j.Stream.Flush($true)
    $j.Sequence++;$j.Previous=$hash
}
function Initialize-NativeJournal([string]$Path,[string]$RequestHash,[string]$Mode,$Context){
    if($script:NativeJournal){throw 'A native journal is already open.'}
    if($RequestHash-cnotmatch '^[0-9a-f]{64}$'){throw 'Invalid journal request identity.'}
    $stream=[IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    $script:NativeJournal=@{Stream=$stream;Encoding=[Text.UTF8Encoding]::new($false);Sequence=0;Previous=('0'*64);Timer=[Diagnostics.Stopwatch]::StartNew();StepIndex=-1}
    Write-NativeJournal 'request_started' ([ordered]@{request_sha256=$RequestHash;mode=$Mode;context=$Context;flush_requested=$true})
}
function Start-NativeJournalStep($Step,[int]$Index){
    if($null-eq $script:NativeJournal){return}
    $script:NativeJournal.StepIndex=$Index
    Write-NativeJournal 'step_started' ([ordered]@{operation_index=$Index;operation=$Step})
}
function Complete-NativeJournalStep($Receipt,$State){
    if($null-eq $script:NativeJournal){return}
    $stateSummary=$null
    if($State){$stateSummary=[ordered]@{snapshot_count=$State.snapshot_count;fps=$State.fps;current_session_path=$State.current_session_path}}
    Write-NativeJournal 'step_verified' ([ordered]@{operation_index=$script:NativeJournal.StepIndex;receipt=$Receipt;state=$stateSummary})
    $script:NativeJournal.StepIndex=-1
}
function Write-NativeJournalPacket([string]$Phase,[byte[]]$Bytes,[long]$Offset){
    if($null-eq $script:NativeJournal){return}
    Write-NativeJournal $Phase ([ordered]@{operation_index=$script:NativeJournal.StepIndex;byte_count=$Bytes.Length;frame_sha256=(Get-NativeJournalSHA $Bytes);tx_offset=$Offset;acceptance_verified=$false})
}
function Close-NativeJournal([string]$Status,[string]$ErrorText,[int]$VerifiedCount){
    if($null-eq $script:NativeJournal){return}
    try{Write-NativeJournal 'request_finished' ([ordered]@{status=$Status;error=$ErrorText;verified_count=$VerifiedCount})}
    finally{$script:NativeJournal.Stream.Dispose();$script:NativeJournal=$null}
}

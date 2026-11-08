param([Parameter(Mandatory=$true)][string]$JournalModulePath)
$ErrorActionPreference='Stop'
. $JournalModulePath
$folder=Join-Path ([IO.Path]::GetTempPath()) ('native-journal-test-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($folder)
$path=Join-Path $folder 'synthetic receipts.jsonl'
try{
    Initialize-NativeJournal $path ('ab'*32) 'execute' @{synthetic=$true}
    Start-NativeJournalStep @{op='set_channel_name';name='Synthetic "A"'} 0
    Write-NativeJournalPacket 'packet_send_started' ([byte[]](1,2,3)) 0
    # A reader that shares the existing writer sees flushed data before close.
    $s=[IO.FileStream]::new($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
    try{$r=[IO.StreamReader]::new($s);$prefix=$r.ReadToEnd();$r.Dispose()}finally{$s.Dispose()}
    if(@($prefix.Split("`n")|Where-Object {$_}).Count-ne 3){throw 'Flushed started/packet receipts are not externally visible.'}
    Complete-NativeJournalStep @{op='set_channel_name';verified=$true} @{snapshot_count=0;fps=25;current_session_path='C:\Synthetic\Lab.ses'}
    Close-NativeJournal 'ok' '' 1
    $lines=@([IO.File]::ReadAllLines($path));if($lines.Count-ne 5){throw 'Journal record count differs.'}
    $prior='0'*64
    for($i=0;$i-lt $lines.Count;$i++){
        $e=$lines[$i]|ConvertFrom-Json;$p=$e.payload_json|ConvertFrom-Json
        if((Get-NativeJournalSHA ([Text.UTF8Encoding]::new($false).GetBytes($e.payload_json)))-cne $e.sha256-or $p.previous_sha256-cne $prior-or $p.sequence-ne $i){throw 'Journal receipt chain differs.'}
        $prior=$e.sha256
    }
    $rejected=$false;try{Initialize-NativeJournal $path ('ab'*32) 'execute' @{}}catch{$rejected=$true}
    if(-not $rejected){throw 'Journal overwrote existing receipts.'}
    'Native journal flushed-receipt tests passed.'
}finally{
    if($script:NativeJournal){$script:NativeJournal.Stream.Dispose();$script:NativeJournal=$null}
    Remove-Item -LiteralPath $folder -Recurse -Force
}

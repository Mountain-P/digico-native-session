# Synthetic header/hash tests. No editor, native connection, SMB share or VM.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RunnerPath,
    [Parameter(Mandatory=$true)][string]$OldProfilePath
)
$ErrorActionPreference='Stop'
$tokens=$null;$parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($RunnerPath,[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count){throw ($parseErrors|Out-String)}
foreach($functionName in @('Hex','Assert-Path','Read-SessionFileIdentity','Check-SessionFile')){
    $node=$ast.Find({param($n) $n-is [Management.Automation.Language.FunctionDefinitionAst]-and $n.Name-eq $functionName},$true)
    if(-not $node){throw "Runner function missing: $functionName"}
    . ([scriptblock]::Create($node.Extent.Text))
}
# Only selected metadata is mocked. The file identity and protocol path checks
# above are the actual runner implementations, including their order of use.
$script:testProfile=Get-Content -LiteralPath $OldProfilePath -Raw -Encoding UTF8|ConvertFrom-Json
function Get-NativeBuildProfile{return $script:testProfile}
$ascii=[Text.Encoding]::GetEncoding(20127,[Text.EncoderExceptionFallback]::new(),[Text.DecoderExceptionFallback]::new())
$script:sends=0
function Send-Step{$script:sends++;throw 'File admission must not send a command.'}
function Send-Bytes{$script:sends++;throw 'File admission must not send bytes.'}
function Reject([scriptblock]$Action,[string]$Pattern,[string]$Label){
    $message=$null
    try{& $Action|Out-Null}catch{$message=$_.Exception.Message}
    if($null-eq $message){throw "Expected rejection: $Label"}
    if($message-notlike $Pattern){throw "Unexpected rejection for ${Label}: $message"}
}
function Bytes-SHA([byte[]]$Bytes){
    $sha=[Security.Cryptography.SHA256]::Create()
    try{return (Hex $sha.ComputeHash($Bytes))}finally{$sha.Dispose()}
}
$headerHex=[string]$script:testProfile.session_file.header_hex
if($headerHex-cnotmatch '^[0-9a-f]{52}$'){throw 'Expected a 26-byte selected header.'}
# Extra payload makes the expected digest cover more than the header window.
# These bytes do not form a native Session; only the admission header is tested.
$valid=New-Object byte[] 52
for($i=0;$i-lt 26;$i++){$valid[$i]=[Convert]::ToByte($headerHex.Substring($i*2,2),16)}
[Array]::Copy([BitConverter]::GetBytes([uint16]$script:testProfile.transport.protocol_version),0,$valid,34,2)
for($i=36;$i-lt $valid.Length;$i++){$valid[$i]=[byte]($i-35)}
$root=Join-Path ([IO.Path]::GetTempPath()) ('native-session-identity-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($root)
try{
    $asset=Join-Path $root 'synthetic-template.asset'
    $localSession=Join-Path $root 'synthetic-local.ses'
    [IO.File]::WriteAllBytes($asset,$valid)
    [IO.File]::WriteAllBytes($localSession,$valid)
    $expected=Bytes-SHA $valid
    if((Read-SessionFileIdentity $asset)-cne $expected){throw 'Asset identity did not return the full-file SHA-256.'}
    # Identity intentionally accepts resource paths outside the native protocol
    # path grammar. No real UNC filesystem is needed to verify that separation.
    Reject {Check-SessionFile $asset} '*absolute local-drive .ses path*' 'non-protocol asset extension'
    if((Check-SessionFile $localSession)-cne $expected){throw 'A valid local protocol path lost header/hash validation.'}
    $changed=[byte[]]$valid.Clone();$changed[$changed.Length-1]=$changed[$changed.Length-1]-bxor 1
    [IO.File]::WriteAllBytes($asset,$changed)
    $changedHash=Read-SessionFileIdentity $asset
    if($changedHash-ceq $expected-or $changedHash-cne (Bytes-SHA $changed)){throw 'Asset identity omitted payload bytes from its digest.'}

    # Reject native UNC/relative/traversal paths before trying to open them.
    foreach($invalid in @('\\Mac\Home\Synthetic\template.ses','\\localhost\Synthetic\template.ses',
                         'relative.ses','.\relative.ses','C:relative.ses',
                         'C:\Synthetic\..\template.ses','C:\Synthetic\.\template.ses')){
        Reject {Check-SessionFile $invalid} '*absolute local-drive .ses path*' $invalid
    }
    # Header-only resource admission does not require a .ses suffix or absolute
    # native path. Relative IO uses the .NET cwd, restored even after a failure.
    $oldDirectory=[IO.Directory]::GetCurrentDirectory()
    Push-Location -LiteralPath $root
    try{
        [IO.Directory]::SetCurrentDirectory($root)
        if((Read-SessionFileIdentity 'synthetic-template.asset')-cne $changedHash){throw 'Header-only asset identity accidentally applied protocol path restrictions.'}
    }finally{[IO.Directory]::SetCurrentDirectory($oldDirectory);Pop-Location}

    $badModel=[byte[]]$valid.Clone();$badModel[12]=$badModel[12]-bxor 1
    $badVersion=[byte[]]$valid.Clone();$badVersion[34]=$badVersion[34]-bxor 1
    $badHeader=[byte[]]$valid.Clone();$badHeader[25]=$badHeader[25]-bxor 1
    foreach($sample in @(
        [pscustomobject]@{Name='wrong-model';Bytes=$badModel;Pattern='*Session model/version header differs*'},
        [pscustomobject]@{Name='wrong-version';Bytes=$badVersion;Pattern='*Session model/version header differs*'},
        [pscustomobject]@{Name='wrong-header';Bytes=$badHeader;Pattern='*Session model/version header differs*'},
        [pscustomobject]@{Name='truncated-header';Bytes=([byte[]]$valid[0..34]);Pattern='*Truncated session header*'}
    )){
        $path=Join-Path $root ($sample.Name+'.ses')
        [IO.File]::WriteAllBytes($path,$sample.Bytes)
        Reject {Read-SessionFileIdentity $path} $sample.Pattern $sample.Name
        Reject {Check-SessionFile $path} $sample.Pattern ($sample.Name+' local protocol')
    }
    Reject {Read-SessionFileIdentity (Join-Path $root 'missing.asset')} '*Session file does not exist*' 'missing asset'
    if($script:sends-ne 0){throw 'Header/path checks sent native traffic.'}
}finally{
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
'Native Session asset identity and protocol-path tests passed.'

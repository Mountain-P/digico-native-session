# Synthetic reader fixtures only. No target process, sockets, Session files or UI.
[CmdletBinding()]
param([string]$OracleModulePath='')
$ErrorActionPreference='Stop'
if(-not $OracleModulePath){$OracleModulePath=Join-Path $PSScriptRoot '..\skills\digico-native-session\scripts\native_mirror_oracle.ps1'}
. $OracleModulePath
if(-not ('NativeMirrorOracleSyntheticReader' -as [type])){
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
public sealed class NativeMirrorOracleSyntheticReader {
    private readonly Dictionary<long,byte> memory=new Dictionary<long,byte>();
    private readonly Dictionary<long,int> calls=new Dictionary<long,int>();
    public long ChangeOnSecondRead=-1;
    public void Set(long a,byte[] b){for(int i=0;i<b.Length;i++)memory[a+i]=b[i];}
    public byte[] Read(long a,int n){
        int prior=calls.ContainsKey(a)?calls[a]:0;calls[a]=prior+1;
        byte[] b=new byte[n];for(int i=0;i<n;i++){
            if(!memory.TryGetValue(a+i,out b[i]))throw new InvalidOperationException("Unmapped synthetic source.");
        }
        if(a==ChangeOnSecondRead && prior>0 && n>0)b[0]^=1;
        return b;
    }
}
'@
}
function Assert-Equal($Actual,$Expected,[string]$Message){if($Actual-cne $Expected){throw "$Message (actual=$Actual, expected=$Expected)"}}
function Assert-Fails([scriptblock]$Body,[string]$Message){$failed=$false;try{& $Body|Out-Null}catch{$failed=$true};if(-not $failed){throw "Expected rejection: $Message"}}
function Set-Word([byte[]]$B,[int]$O,[uint16]$V){[BitConverter]::GetBytes($V).CopyTo($B,$O)}
function Set-Dword([byte[]]$B,[int]$O,[uint32]$V){[BitConverter]::GetBytes($V).CopyTo($B,$O)}
function Put-Dword($R,[long]$A,[uint32]$V){$R.Set($A,[BitConverter]::GetBytes($V))}
function Put-Short($R,[long]$A,[string]$S){$sbytes=[Text.Encoding]::ASCII.GetBytes($S);$b=New-Object byte[] ($sbytes.Length+1);$b[0]=$sbytes.Length;$sbytes.CopyTo($b,1);$R.Set($A,$b)}
function New-List($R,[long]$A,[uint32]$Vmt,[uint32[]]$Items,[int]$Size=16){
    $h=New-Object byte[] $Size;Set-Dword $h 0 $Vmt;Set-Dword $h 8 $Items.Length;Set-Dword $h 12 $Items.Length
    if($Items.Length-gt 0){$p=[uint32]($A+0x1000);Set-Dword $h 4 $p;$b=New-Object byte[] ($Items.Length*4);for($i=0;$i-lt $Items.Length;$i++){Set-Dword $b ($i*4) $Items[$i]};$R.Set($p,$b)}
    $R.Set($A,$h)
    return ,$h
}
function New-Fixture([bool]$NewBuild=$false,[bool]$WithSnapshot=$false){
    $r=New-Object NativeMirrorOracleSyntheticReader;$base=0x400000
    if($NewBuild){
        $id='quantum1-v2242-4';$s=@{snapshot=0x1336a04;macros=0x1331aec;encoders=0xaaffc8;channel_macros=0xab0528;setlists=0xab05b8;path=0x133eeac;second=0x133efac;record=0x133db28;ports=0x99911c;cp=0x972980;mode_a=0xaae570;mode_b=0xaae580}
        $v=@{snapshot=0x880070;console=0x87e648;channel=0x87f560;module=0x87ddf8;midi=0x87cfec;macros=0x735434;commands=0x735030;encoders=0x45bb48;encoder_commands=0x45b7a4;setlists=0x59fe98;entries=0x59f874}
    }else{
        $id='quantum1-v2126';$s=@{snapshot=0x127305c;macros=0x126e150;encoders=0xa27f78;channel_macros=0xa284ac;path=0x127b4fc;second=0x127b5fc;record=0x127a17c;ports=0x91111c;cp=0x8ea980;mode_a=0xa26570;mode_b=0xa26580}
        $v=@{snapshot=0x7f9bb0;console=0x7f8188;channel=0x7f90a0;module=0x7f7938;midi=0x7f6b84;macros=0x6b5328;commands=0x6b4f24;encoders=0x44aa5c;encoder_commands=0x44a6b8}
    }
    $snap=0x20000000;$macros=0x21000000;$encoders=0x22000000
    foreach($alias in [DiGiCoNativeMirrorOracle]::RequiredAliases($id).GetEnumerator()){
        Put-Dword $r ($base+$alias.Key) ($base+$alias.Value)
    }
    Put-Dword $r ($base+$s.snapshot) $snap;Put-Dword $r ($base+$s.macros) $macros
    Put-Dword $r ($base+$s.encoders) $encoders;Put-Dword $r ($base+$s.channel_macros) 0
    $items=[uint32[]]@();if($WithSnapshot){$items=[uint32[]]@(0x23000000)}
    $h=New-List $r $snap ($base+$v.snapshot) $items 0x8d
    $selected=@(0x3c,0x3d,0x40,0x41,0x47,0x48);for($i=0;$i-lt 6;$i++){$h[$selected[$i]]=$i+1}
    for($i=1;$i-le 33;$i++){Set-Word $h (0x49+2*$i) $i};$r.Set($snap,$h)
    New-List $r $macros ($base+$v.macros) ([uint32[]]@(0x21100000))|Out-Null
    $m=New-Object byte[] 0x2c;$m[2]=2;$m[3]=72;$m[4]=105;$m[0x22]=7;$m[0x23]=9;Set-Dword $m 0x24 ([uint32]4294967294);Set-Dword $m 0x28 0x21200000;$r.Set(0x21100000,$m)
    New-List $r 0x21200000 ($base+$v.commands) ([uint32[]]@(0x21300000,0x21400000))|Out-Null
    $r.Set(0x21300000,[byte[]]@(4,0,1,2));$r.Set(0x21400000,[byte[]]@(2,0))
    New-List $r $encoders ($base+$v.encoders) ([uint32[]]@(0x22100000))|Out-Null
    $e=New-Object byte[] 0x2a;$e[2]=1;$e[3]=65;Set-Dword $e 0x26 0x22200000;$r.Set(0x22100000,$e)
    New-List $r 0x22200000 ($base+$v.encoder_commands) ([uint32[]]@())|Out-Null
    if($WithSnapshot){
        $snapshot=New-List $r 0x23000000 ($base+$v.console) ([uint32[]]@(0x23100000)) 0x7ba
        $snapshot[0x28]=1;$snapshot[0x29]=90;Set-Word $snapshot 0x430 2;Set-Dword $snapshot 0x432 0x23500000
        Set-Dword $snapshot 0x436 3;Set-Dword $snapshot 0x43a 4;Set-Dword $snapshot 0x765 5;Set-Word $snapshot 0x769 6
        $snapshot[0x76b]=7;$snapshot[0x76c]=8;$offsets=@(0x76f,0x773,0x777,0x77b,0x77f,0x783,0x787)
        for($i=0;$i-lt 7;$i++){Set-Dword $snapshot $offsets[$i] ($i+9)}
        $snapshot[0x78c]=16;$snapshot[0x78d]=17;Set-Dword $snapshot 0x7b6 0x23400000;$r.Set(0x23000000,$snapshot)
        $r.Set(0x23400000,[byte[]]@(111,107,0))
        $ch=New-List $r 0x23100000 ($base+$v.channel) ([uint32[]]@(0x23200000)) 0x46
        Set-Word $ch 0x24 0x123;$ch[0x26]=2;$ch[0x27]=65;$ch[0x28]=66;$r.Set(0x23100000,$ch)
        $mod=New-Object byte[] 0x42;Set-Dword $mod 0 ($base+$v.module);Set-Dword $mod 4 9;Set-Dword $mod 0x30 1;$mod[0x34]=4;Set-Dword $mod 0x35 7;Set-Dword $mod 0x3e 0x23300000;$r.Set(0x23200000,$mod)
        $data=New-Object byte[] 8;$data[3]=3;Set-Dword $data 4 ([uint32]4294967294);$r.Set(0x23300000,$data)
        $midi=New-Object byte[] 8;Set-Dword $midi 0 ($base+$v.midi);Set-Dword $midi 4 0x23600004;$r.Set(0x23500000,$midi)
        $r.Set(0x23600000,[byte[]]@(3,0,0,0,5,6,7))
    }
    if($NewBuild){
        Put-Dword $r ($base+$s.setlists) 0x24000000
        $sets=New-List $r 0x24000000 ($base+$v.setlists) ([uint32[]]@(0x24100000,0x24200000)) 0x28
        Set-Dword $sets 0x24 ([uint32]4294967295);$r.Set(0x24000000,$sets)
        $a=New-Object byte[] 0x24;$a[0]=1;$a[1]=65;Set-Dword $a 0x20 0x24300000;$r.Set(0x24100000,$a)
        $b=New-Object byte[] 0x24;$b[0]=2;$b[1]=66;$b[2]=67;Set-Dword $b 0x20 0x24400000;$r.Set(0x24200000,$b)
        $entries=[uint32[]]@();if($WithSnapshot){$entries=[uint32[]]@(0x24500000)}
        New-List $r 0x24300000 ($base+$v.entries) $entries|Out-Null
        New-List $r 0x24400000 ($base+$v.entries) ([uint32[]]@())|Out-Null
        if($WithSnapshot){$entry=New-Object byte[] 8;Set-Dword $entry 0 17;Set-Dword $entry 4 0x23000000;$r.Set(0x24500000,$entry)}
    }
    $record=New-Object byte[] 123;for($i=0;$i-lt 123;$i++){$record[$i]=$i};$r.Set(($base+$s.record),$record)
    Put-Short $r ($base+$s.path) 'C:\Examples\Demo.ses';Put-Short $r ($base+$s.second) 'X';Put-Dword $r ($base+$s.cp) 1252
    $r.Set(($base+$s.mode_a),[byte[]]@(0));$r.Set(($base+$s.mode_b),[byte[]]@(1))
    for($i=0;$i-lt 50;$i++){$pair=[byte[]]@(0,0);if($i-eq 0){$pair=[byte[]]@(29,3)}elseif($i-eq 1){$pair=[byte[]]@(1,2)};$r.Set(($base+$s.ports+210*$i),$pair)}
    return @{reader=$r;base=$base;slots=$s;profile=[pscustomobject]@{id=$id;executable=[pscustomobject]@{sha256=[DiGiCoNativeMirrorOracle]::RequiredSha256($id)}}}
}

$f=New-Fixture;$o=Read-MirrorVerifyBaseline -Reader $f.reader -ImageBase $f.base -BuildProfile $f.profile
Assert-Equal $o.hash_terms.snapshots ([uint32]582) 'Snapshot settings remain in checksum with zero snapshots'
Assert-Equal $o.hash_terms.macros ([uint32]202) 'Macro name prefix, raw command length extent, and wrapping addition'
Assert-Equal $o.hash_terms.encoders ([uint32]66) 'Encoder differs from Macro record hash'
Assert-Equal $o.hash_sum_uint32 ([uint32]850) 'Independent combined expected sum'
Assert-Equal $o.signature_wire_hex.Substring(0,8) '1d020102' 'Native port-type29 low-bit mask'
Assert-Equal $o.signature_wire_hex.Length 200 'Synthetic CP1252 signature length'
Assert-Equal $o.normalized_session_path_hex '633a5c6578616d706c65735c64656d6f2e736573' 'Copied ASCII Session reference is normalized without constructing a protocol body'
Assert-Equal $o.consistent_witness_reread $true 'Source witnesses are reread'
Assert-Equal $o.native_local_mode_gate_passed $true 'Native mode predicate uses copied flags'
Assert-Equal $o.packet_or_body_constructed $false 'Oracle must not build protocol bytes'
Assert-Equal $o.network_used $false 'Oracle must not use network transport'
Assert-Equal $o.target_function_calls $false 'Oracle must not call target functions'
Assert-Equal $o.target_memory_writes $false 'Oracle must not write target memory'
Assert-Equal $o.runtime_acceptance_verified $false 'A read-only oracle cannot observe current-call peer acceptance'
Assert-Equal $o.initial_sync_complete $false 'Oracle cannot establish complete sync'
Assert-Equal ($o.ContainsKey('verify_body_hex_dir0')) $false 'Oracle must not expose a complete Verify body'
Assert-Equal $o.bounded_offline_compatibility_observed_for_build $true 'Old-build evidence level'


$f=New-Fixture;Put-Dword $f.reader ($f.base+$f.slots.cp) 65001
$f.reader.Set(($f.base+$f.slots.ports+420),[byte[]]@(233,255))
$o=Read-MirrorVerifyBaseline -Reader $f.reader -ImageBase $f.base -BuildProfile $f.profile
Assert-Equal $o.signature_wire_hex.Substring(0,16) '1d020102c3a9c3bf' 'UTF8 roundtrip uses explicit byte/codeunit lengths including embedded NULs'
Assert-Equal $o.signature_wire_hex.Length 204 'Signature wire length follows actual conversion, not fixed100-byte assumption'

$f=New-Fixture;$f.reader.Set(($f.base+$f.slots.mode_a),[byte[]]@(2))
$o=Read-MirrorVerifyBaseline -Reader $f.reader -ImageBase $f.base -BuildProfile $f.profile
Assert-Equal $o.mode_flag_a 1 'Native mode flags canonicalize nonzero Boolean tests and retain raw bytes'
Assert-Equal $o.mode_flag_a_raw ([byte]2) 'Raw mode byte remains available'
Assert-Equal $o.native_local_mode_gate_passed $false 'Both nonzero flags block eligibility; read-only facts remain reportable'

$f=New-Fixture -WithSnapshot $true;$o=Read-MirrorVerifyBaseline -Reader $f.reader -ImageBase $f.base -BuildProfile $f.profile
Assert-Equal $o.hash_terms.snapshots ([uint32]1498) 'Full Snapshot/Channel/Module/MIDI/Notes raw-field recursion'
Assert-Equal $o.hash_sum_uint32 ([uint32]1766) 'Recursive sum includes native MIDI offset boundary'
$f=New-Fixture -NewBuild $true -WithSnapshot $true;$o=Read-MirrorVerifyBaseline -Reader $f.reader -ImageBase $f.base -BuildProfile $f.profile
Assert-Equal $o.hash_terms.setlists ([uint32]1132) 'New fifth Setlist term includes repeated current index and referenced snapshot hash'
Assert-Equal $o.hash_sum_uint32 ([uint32]2898) 'New five-term uint32 sum'
Assert-Equal $o.bounded_offline_compatibility_observed_for_build $false 'New-build five-term compatibility remains static-only'
Assert-Equal $o.evidence_stage 'static-hash-algorithms-only' 'New-build evidence stage must not inherit old runtime proof'


$f=New-Fixture;$f.reader.ChangeOnSecondRead=$f.base+$f.slots.snapshot
Assert-Fails {Read-MirrorVerifyBaseline -Reader $f.reader -ImageBase $f.base -BuildProfile $f.profile} 'Changed source pointer blocks output'
$f=New-Fixture;Put-Dword $f.reader 0x20000000 0x12345678
Assert-Fails {Read-MirrorVerifyBaseline -Reader $f.reader -ImageBase $f.base -BuildProfile $f.profile} 'Wrong exact VMT blocks output'
$f=New-Fixture;Put-Dword $f.reader ($f.base+0x8e9654) 0x12345678
Assert-Fails {Read-MirrorVerifyBaseline -Reader $f.reader -ImageBase $f.base -BuildProfile $f.profile} 'Changed native readonly alias blocks output'
$f=New-Fixture;Put-Dword $f.reader (0x20000000+8) 200001
Assert-Fails {Read-MirrorVerifyBaseline -Reader $f.reader -ImageBase $f.base -BuildProfile $f.profile} 'Excess list count blocks output'
$f=New-Fixture;$f.profile.executable.sha256='0'*64
Assert-Fails {Read-MirrorVerifyBaseline -Reader $f.reader -ImageBase $f.base -BuildProfile $f.profile} 'Wrong supplied executable hash blocks output'
$f=New-Fixture
Assert-Fails {Read-MirrorVerifyBaseline -Reader $f.reader -ImageBase $f.base -BuildProfile 'quantum1-v2126'} 'Profile ID string is insufficient identity'
'Native Mirror oracle synthetic tests passed (no target process or network access).'

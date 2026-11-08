# Read-only native Mirror baseline oracle. Dot-source functions only.
# Caller must independently hash-gate and bind its selected process, actual
# module base and reader exposing byte[] Read(Int64,Int32). This module never
# opens a process/socket, calls target functions, writes target memory, builds a
# protocol body/frame, sends acceptance, or changes a Session.
# Old Q1 V2126: algorithms plus one bounded offline compatibility observation.
# New Q1 V2242.4: five-term algorithms are static evidence only.
if (-not ('DiGiCoNativeMirrorOracle' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

public sealed class DiGiCoNativeMirrorOracle {
    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode, ExactSpelling=true)]
    private static extern int WideCharToMultiByte(uint cp, uint flags,
        [MarshalAs(UnmanagedType.LPWStr)] string text, int chars,
        [Out] byte[] dest, int bytes, IntPtr defaultChar, IntPtr usedDefault);
    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode, ExactSpelling=true)]
    private static extern int MultiByteToWideChar(uint cp, uint flags,
        byte[] text, int bytes,
        [Out, MarshalAs(UnmanagedType.LPArray, ArraySubType=UnmanagedType.U2)] char[] dest, int chars);

    private sealed class Profile {
        public string Id, Sha;
        public uint Snapshot, Macros, Encoders, ChannelMacros, Setlists;
        public uint Path, SecondString, Record, Ports, Codepage, ModeFlagA, ModeFlagB;
        public uint SnapshotVmt, ConsoleSnapshotVmt, ChannelSnapshotVmt,
            ModuleSnapshotVmt, MidiVmt, MacrosVmt, MacroCommandsVmt,
            EncodersVmt, EncoderCommandsVmt, ChannelMacrosVmt,
            ChannelMacroCommandsVmt, SetlistsVmt, SetlistEntriesVmt;
        public Dictionary<uint,uint> Aliases;
    }
    private sealed class Witness { public long Address; public byte[] Bytes; public string Label; }
    private sealed class ListCopy { public byte[] Header; public uint[] Items; public int Count; }
    private readonly object reader;
    private readonly MethodInfo readMethod;
    private readonly long imageBase;
    private readonly Profile profile;
    private readonly long maxBytes;
    private readonly int maxItems;
    private long bytesRead;
    private readonly List<Witness> witnesses = new List<Witness>();
    private readonly Dictionary<string, byte[]> cache = new Dictionary<string, byte[]>();
    private readonly Dictionary<string, uint> hashCache = new Dictionary<string, uint>();
    private readonly Dictionary<string, int> objectCounts = new Dictionary<string, int>();

    private static Profile GetProfile(string id) {
        if (id == "quantum1-v2126") return new Profile {
            Id=id, Sha="535097f38bb459e6e299b5da50b62fa811511d92af6d7215a13d703a416f177d",
            Snapshot=0x127305c, Macros=0x126e150, Encoders=0xa27f78, ChannelMacros=0xa284ac,
            Path=0x127b4fc, SecondString=0x127b5fc, Record=0x127a17c,
            Ports=0x91111c, Codepage=0x8ea980, ModeFlagA=0xa26570, ModeFlagB=0xa26580,
            SnapshotVmt=0x7f9bb0, ConsoleSnapshotVmt=0x7f8188,
            ChannelSnapshotVmt=0x7f90a0, ModuleSnapshotVmt=0x7f7938, MidiVmt=0x7f6b84,
            MacrosVmt=0x6b5328, MacroCommandsVmt=0x6b4f24,
            EncodersVmt=0x44aa5c, EncoderCommandsVmt=0x44a6b8,
            ChannelMacrosVmt=0x53c968, ChannelMacroCommandsVmt=0x53c64c,
            Aliases=new Dictionary<uint,uint>{{0x8e9654,0x127305c},{0x8e8848,0x126e150},
                {0x8e8a44,0xa27f78},{0x8e98a0,0xa284ac},{0x8e86bc,0x127b4fc},
                {0x8e9650,0x127b5fc},{0x8e9980,0x127a17c},
                {0x8e8914,0xa26570},{0x8e8084,0xa26580}}
        };
        if (id == "quantum1-v2242-4") return new Profile {
            Id=id, Sha="ca5b3266615b89dbbfc38583c7df837faf734180865de1736720b10ee138ee08",
            Snapshot=0x1336a04, Macros=0x1331aec, Encoders=0xaaffc8, ChannelMacros=0xab0528,
            Setlists=0xab05b8, Path=0x133eeac, SecondString=0x133efac,
            Record=0x133db28, Ports=0x99911c, Codepage=0x972980, ModeFlagA=0xaae570, ModeFlagB=0xaae580,
            SnapshotVmt=0x880070, ConsoleSnapshotVmt=0x87e648,
            ChannelSnapshotVmt=0x87f560, ModuleSnapshotVmt=0x87ddf8, MidiVmt=0x87cfec,
            MacrosVmt=0x735434, MacroCommandsVmt=0x735030,
            EncodersVmt=0x45bb48, EncoderCommandsVmt=0x45b7a4,
            ChannelMacrosVmt=0x558190, ChannelMacroCommandsVmt=0x557e74,
            SetlistsVmt=0x59fe98, SetlistEntriesVmt=0x59f874,
            Aliases=new Dictionary<uint,uint>{{0x970ed8,0x1336a04},{0x970070,0x1331aec},
                {0x970278,0xaaffc8},{0x97112c,0xab0528},{0x9705e8,0xab05b8},
                {0x96fee4,0x133eeac},{0x970ed4,0x133efac},{0x97121c,0x133db28},
                {0x970144,0xaae570},{0x96f88c,0xaae580}}
        };
        throw new InvalidOperationException("Unsupported exact Mirror oracle profile: " + id);
    }
    public static string RequiredSha256(string profileId) { return GetProfile(profileId).Sha; }
    public static Dictionary<uint,uint> RequiredAliases(string profileId) {
        return new Dictionary<uint,uint>(GetProfile(profileId).Aliases);
    }
    private DiGiCoNativeMirrorOracle(object processReader, long baseAddress, string profileId,
        long byteBudget, int itemLimit) {
        reader=processReader;
        readMethod=reader.GetType().GetMethod("Read", new Type[] { typeof(long), typeof(int) });
        if (readMethod == null) throw new InvalidOperationException("Reader must expose byte[] Read(Int64,Int32).");
        if (baseAddress < 0x10000 || baseAddress > 0xffffffffL)
            throw new InvalidOperationException("Invalid 32-bit image base.");
        if (byteBudget < 4096 || byteBudget > 268435456 || itemLimit < 1 || itemLimit > 200000)
            throw new InvalidOperationException("Invalid bounded oracle resource limits.");
        imageBase=baseAddress; profile=GetProfile(profileId); maxBytes=byteBudget; maxItems=itemLimit;
    }
    private static uint Add(uint a, uint b) { return unchecked(a+b); }
    private static uint U32(byte[] a, int o) { return BitConverter.ToUInt32(a,o); }
    private static int I32(byte[] a, int o) { return BitConverter.ToInt32(a,o); }
    private static ushort U16(byte[] a, int o) { return BitConverter.ToUInt16(a,o); }
    private static string Hex(byte[] a) { return BitConverter.ToString(a).Replace("-","").ToLowerInvariant(); }
    private static byte[] Cut(byte[] a, int o, int n) { byte[] b=new byte[n]; Array.Copy(a,o,b,0,n); return b; }
    private static bool Equal(byte[] a, byte[] b) {
        if (a == null || b == null || a.Length != b.Length) return false;
        for (int i=0;i<a.Length;i++) if(a[i]!=b[i]) return false;
        return true;
    }
    private byte[] Direct(long address, int length) {
        if (length < 0 || address < 0x10000 || address+length > 0x100000000L)
            throw new InvalidOperationException("Invalid 32-bit read extent.");
        try {
            byte[] a=readMethod.Invoke(reader,new object[]{address,length}) as byte[];
            if(a==null || a.Length!=length) throw new InvalidOperationException("Short or non-byte[] read.");
            return a;
        } catch (TargetInvocationException e) {
            throw new InvalidOperationException("Read-only oracle could not copy a required source range.",e.InnerException);
        }
    }
    private byte[] Read(long address, int length, string label) {
        string key=address.ToString("x")+":"+length;
        byte[] a;
        if(cache.TryGetValue(key,out a)) return a;
        if(length > maxBytes-bytesRead) throw new InvalidOperationException("Oracle copied-byte budget exceeded.");
        a=Direct(address,length); bytesRead+=length; cache.Add(key,a);
        witnesses.Add(new Witness{Address=address,Bytes=a,Label=label}); return a;
    }
    private uint Global(uint rva, string label) { return U32(Read(imageBase+rva,4,label+" pointer slot"),0); }
    private void Vmt(byte[] bytes, uint expectedRva, string label) {
        if(U32(bytes,0)!=(uint)(imageBase+expectedRva))
            throw new InvalidOperationException(label+" has an unexpected exact-build VMT.");
    }
    private void CountObject(string kind) {
        int count=objectCounts.ContainsKey(kind)?objectCounts[kind]:0;
        if(count >= maxItems) throw new InvalidOperationException("Oracle object-count limit exceeded: "+kind);
        objectCounts[kind]=count+1;
    }
    private ListCopy List(uint pointer, uint vmtRva, int headerBytes, string label) {
        if(pointer==0) throw new InvalidOperationException("Required list is null: "+label);
        byte[] h=Read(pointer,headerBytes,label+" header"); Vmt(h,vmtRva,label);
        int count=I32(h,8),capacity=I32(h,12); uint items=U32(h,4);
        if(count<0 || count>maxItems || capacity<count || capacity>2000000)
            throw new InvalidOperationException("Invalid list count/capacity: "+label);
        uint[] result=new uint[count];
        if(count>0) {
            byte[] data=Read(items,checked(count*4),label+" item pointers");
            for(int i=0;i<count;i++) {
                result[i]=U32(data,i*4);
                if(result[i]<0x10000) throw new InvalidOperationException("Null/invalid list item: "+label);
            }
        }
        CountObject(label);
        return new ListCopy{Header=h,Items=result,Count=count};
    }
    private uint Sum(byte[] data) { uint sum=0; foreach(byte b in data) sum=Add(sum,b); return sum; }
    private byte[] ShortString(long address, int max, string label) {
        int length=Read(address,1,label+" length")[0];
        if(length>max) throw new InvalidOperationException("Invalid bounded ShortString length: "+label);
        return Read(address,length+1,label+" prefix and bytes");
    }
    private uint Commands(uint pointer, uint vmtRva, string label) {
        string key=label+":"+pointer;uint cached;
        if(hashCache.TryGetValue(key,out cached))return cached;
        ListCopy list=List(pointer,vmtRva,16,label);uint sum=0;
        foreach(uint item in list.Items) {
            int n=U16(Read(item,2,label+" native byte-length word"),0);
            // Native sums n bytes starting at the length word itself.
            sum=Add(sum,Sum(Read(item,n,label+" raw command checksum extent")));
        }
        hashCache[key]=sum;return sum;
    }
    private uint MacroLike(uint pointer, string kind, uint vmt, uint commandVmt, bool macros) {
        if(pointer==0) {
            if(macros)throw new InvalidOperationException("Required MacrosList is null.");
            objectCounts[kind]=0;return 0;
        }
        ListCopy list=List(pointer,vmt,16,kind);uint sum=0;
        objectCounts[kind+"_rows"]=list.Count;
        foreach(uint item in list.Items) {
            // These items are packed records, not Delphi objects.
            byte[] row=Read(item,macros?0x2c:0x2a,kind+" packed row");
            sum=Add(sum,Sum(ShortString(item+2,31,kind+" name")));
            if(macros) {
                sum=Add(sum,row[0x22]);sum=Add(sum,row[0x23]);sum=Add(sum,U32(row,0x24));
            }
            sum=Add(sum,Commands(U32(row,macros?0x28:0x26),commandVmt,kind+"_commands"));
        }
        return sum;
    }
    private uint Midi(uint pointer) {
        byte[] h=Read(pointer,8,"MIDI wrapper");Vmt(h,profile.MidiVmt,"MIDI wrapper");
        uint data=U32(h,4);if(data==0)return 0;
        int n=I32(Read((long)data-4,4,"MIDI AnsiString byte length"),0);
        if(n<0 || n>1048576)throw new InvalidOperationException("Invalid bounded MIDI AnsiString length.");
        // Preserve native [data+i-1], i=0..n-1, rather than correcting it.
        return Sum(Read((long)data-1,n,"native MIDI checksum extent (starts one byte before data)"));
    }
    private uint ModuleSnapshot(uint pointer) {
        string key="module:"+pointer;uint cached;if(hashCache.TryGetValue(key,out cached))return cached;
        byte[] h=Read(pointer,0x42,"module snapshot");Vmt(h,profile.ModuleSnapshotVmt,"module snapshot");
        int n=I32(h,0x30);if(n<0 || n>maxItems)throw new InvalidOperationException("Invalid module snapshot nControls.");
        uint sum=Add(Add(Add(U32(h,4),(uint)n),h[0x34]),U32(h,0x35));
        if(n>0) {
            byte[] data=Read(U32(h,0x3e),checked(n*8),"module snapshot control data");
            for(int i=0;i<n;i++)sum=Add(Add(sum,data[i*8+3]),U32(data,i*8+4));
        }
        CountObject("module_snapshots");hashCache[key]=sum;return sum;
    }
    private uint ChannelSnapshot(uint pointer) {
        string key="channel:"+pointer;uint cached;if(hashCache.TryGetValue(key,out cached))return cached;
        ListCopy list=List(pointer,profile.ChannelSnapshotVmt,0x46,"channel_snapshots");
        int n=Math.Min((int)list.Header[0x26],31);uint sum=U16(list.Header,0x24);
        // ChannelTitle excludes its prefix, unlike the console snapshot name.
        sum=Add(sum,Sum(Cut(list.Header,0x27,n)));
        foreach(uint item in list.Items)sum=Add(sum,ModuleSnapshot(item));
        hashCache[key]=sum;return sum;
    }
    private uint CString(uint pointer) {
        if(pointer==0)return 0;
        // One-byte reads avoid requiring an unneeded page beyond the terminator.
        uint sum=0;
        for(int i=0;i<1048576;i++) {
            byte b=Read((long)pointer+i,1,"snapshot notes byte/terminator")[0];
            if(b==0)return sum;sum=Add(sum,b);
        }
        throw new InvalidOperationException("Snapshot notes have no terminator within the bound.");
    }
    private uint ConsoleSnapshot(uint pointer) {
        string key="snapshot:"+pointer;uint cached;if(hashCache.TryGetValue(key,out cached))return cached;
        ListCopy list=List(pointer,profile.ConsoleSnapshotVmt,0x7ba,"console_snapshots");
        byte[] h=list.Header;uint sum=U16(h,0x430);
        sum=Add(sum,Sum(ShortString(pointer+0x28,31,"snapshot name")));
        uint midi=U32(h,0x432);if(midi!=0)sum=Add(sum,Midi(midi));
        int[] dwords={0x436,0x43a,0x765,0x76f,0x773,0x777,0x77b,0x77f,0x783,0x787};
        foreach(int o in dwords)sum=Add(sum,U32(h,o));
        sum=Add(sum,U16(h,0x769));
        int[] bytes={0x76b,0x76c,0x78c,0x78d};foreach(int o in bytes)sum=Add(sum,h[o]);
        sum=Add(sum,CString(U32(h,0x7b6)));
        foreach(uint item in list.Items)sum=Add(sum,ChannelSnapshot(item));
        hashCache[key]=sum;return sum;
    }
    private uint Snapshots(uint pointer) {
        ListCopy list=List(pointer,profile.SnapshotVmt,0x8d,"snapshot_list");uint sum=0;
        int[] bytes={0x3c,0x3d,0x40,0x41,0x47,0x48};
        foreach(int o in bytes)sum=Add(sum,list.Header[o]);
        for(int i=1;i<=33;i++)sum=Add(sum,U16(list.Header,0x49+2*i));
        objectCounts["snapshot_rows"]=list.Count;
        foreach(uint item in list.Items)sum=Add(sum,ConsoleSnapshot(item));return sum;
    }
    private uint Setlists(uint pointer) {
        ListCopy list=List(pointer,profile.SetlistsVmt,0x28,"setlists");uint sum=0;
        uint current=U32(list.Header,0x24);objectCounts["setlist_rows"]=list.Count;
        foreach(uint item in list.Items) {
            byte[] row=Read(item,0x24,"setlist packed row");
            sum=Add(sum,current);sum=Add(sum,Sum(ShortString(item,31,"setlist name")));
            ListCopy entries=List(U32(row,0x20),profile.SetlistEntriesVmt,16,"setlist_entry_lists");
            foreach(uint entry in entries.Items) {
                byte[] e=Read(entry,8,"setlist entry packed record");
                sum=Add(sum,U32(e,0));sum=Add(sum,ConsoleSnapshot(U32(e,4)));
            }
        }
        return sum;
    }
    private static string AnsiToUnicode(byte[] bytes, uint cp) {
        if(bytes.Length==0)return "";
        char[] dest=new char[2047];
        int n=MultiByteToWideChar(cp,0,bytes,bytes.Length,dest,dest.Length);
        if(n<=0)throw new InvalidOperationException("Client MultiByteToWideChar failed for target RTL codepage.");
        return new string(dest,0,n);
    }
    private static byte[] UnicodeToShort(string text, uint cp) {
        if(text.Length==0)return new byte[0];
        int chars=Math.Min(255,text.Length);byte[] dest=new byte[511];
        int n=WideCharToMultiByte(cp,0,text,chars,dest,dest.Length,IntPtr.Zero,IntPtr.Zero);
        if(n<=0)throw new InvalidOperationException("Client WideCharToMultiByte failed for target RTL codepage.");
        return Cut(dest,0,Math.Min(255,n));
    }
    private byte[] Signature(uint cp, out byte[] source) {
        source=new byte[100];byte[] previous=new byte[0];
        for(int i=0;i<50;i++) {
            byte[] pair=Read(imageBase+profile.Ports+i*210,2,"port signature pair "+i);
            byte a=pair[0],b=pair[1];if(a==0x1d)b=(byte)(b&0xfe);
            source[i*2]=a;source[i*2+1]=b;
            // Native converts its previous ShortString back to Unicode each turn.
            string s=AnsiToUnicode(previous,cp)+(char)a+(char)b;
            previous=UnicodeToShort(s,cp);
        }
        return previous;
    }
    private void Stable() {
        foreach(Witness w in witnesses)if(!Equal(w.Bytes,Direct(w.Address,w.Bytes.Length)))
            throw new InvalidOperationException("Mirror baseline source changed during witness reread: "+w.Label);
    }
    public static Dictionary<string,object> Compute(object reader, long imageBase,
        string profileId, long byteBudget, int itemLimit) {
        DiGiCoNativeMirrorOracle o=new DiGiCoNativeMirrorOracle(reader,imageBase,profileId,byteBudget,itemLimit);
        foreach(KeyValuePair<uint,uint> alias in o.profile.Aliases) {
            uint observed=U32(o.Read(imageBase+alias.Key,4,"native readonly pointer alias"),0);
            if(observed!=(uint)(imageBase+alias.Value))
                throw new InvalidOperationException("Native Verify alias points outside its exact-build source slot.");
        }
        Dictionary<string,object> terms=new Dictionary<string,object>();uint total=0;
        uint term=o.Snapshots(o.Global(o.profile.Snapshot,"snapshot list"));terms.Add("snapshots",term);total=Add(total,term);
        term=o.MacroLike(o.Global(o.profile.Macros,"macros list"),"macros",o.profile.MacrosVmt,o.profile.MacroCommandsVmt,true);
        terms.Add("macros",term);total=Add(total,term);
        if(o.profile.Setlists!=0) {term=o.Setlists(o.Global(o.profile.Setlists,"setlists"));terms.Add("setlists",term);total=Add(total,term);}
        term=o.MacroLike(o.Global(o.profile.Encoders,"optional encoders list"),"encoders",o.profile.EncodersVmt,o.profile.EncoderCommandsVmt,false);
        terms.Add("encoders",term);total=Add(total,term);
        term=o.MacroLike(o.Global(o.profile.ChannelMacros,"optional channel macros list"),"channel_macros",o.profile.ChannelMacrosVmt,o.profile.ChannelMacroCommandsVmt,false);
        terms.Add("channel_macros",term);total=Add(total,term);
        byte[] record=o.Read(imageBase+o.profile.Record,123,"tConsoleRec raw123");
        byte[] path=o.ShortString(imageBase+o.profile.Path,255,"session path");
        for(int i=1;i<path.Length;i++)if(path[i]==0 || path[i]>=128)
            throw new InvalidOperationException("This bounded oracle requires a non-NUL ASCII current Session path.");
        byte[] lower=Cut(path,1,path.Length-1);
        for(int i=0;i<lower.Length;i++)if(lower[i]>=65 && lower[i]<=90)lower[i]=(byte)(lower[i]+32);
        byte[] second=o.ShortString(imageBase+o.profile.SecondString,255,"second Verify string (semantic unknown)");
        byte modeA=o.Read(imageBase+o.profile.ModeFlagA,1,"native Verify local mode flag A")[0];
        byte modeB=o.Read(imageBase+o.profile.ModeFlagB,1,"native Verify local mode flag B")[0];
        uint cp=U32(o.Read(imageBase+o.profile.Codepage,4,"RTL DefaultSystemCodePage"),0);
        if(cp==0 || cp>65535)throw new InvalidOperationException("Unresolved/unsupported target RTL codepage.");
        byte[] signatureSource;byte[] signature=o.Signature(cp,out signatureSource);
        o.Stable();
        List<object> ranges=new List<object>();
        foreach(Witness w in o.witnesses)ranges.Add(new Dictionary<string,object>{
            {"address",w.Address},{"length",w.Bytes.Length},{"label",w.Label},{"raw_hex",Hex(w.Bytes)}});
        return new Dictionary<string,object> {
            {"schema_version",1},{"profile",profileId},{"executable_sha256_required",o.profile.Sha},
            {"executable_hash_must_be_verified_by_caller",true},
            {"contains_private_metadata",true},
            {"memory_read_only",true},{"target_function_calls",false},{"target_memory_writes",false},
            {"network_used",false},{"ui_used",false},{"runtime_acceptance_verified",false},
            {"initial_sync_complete",false},{"packet_or_body_constructed",false},
            {"evidence_stage",profileId=="quantum1-v2126"?"static-algorithms-and-bounded-offline-compatibility-observation":"static-hash-algorithms-only"},
            {"bounded_offline_compatibility_observed_for_build",profileId=="quantum1-v2126"},
            {"current_call_peer_acceptance_observed",false},
            {"consistent_witness_reread",true},{"values_atomic",false},
            {"hash_algorithm","native uint32 wrapping additions of copied raw fields"},
            {"hash_terms",terms},{"hash_sum_uint32",total},{"hash_sum_le_hex",Hex(BitConverter.GetBytes(total))},
            {"object_counts",o.objectCounts},{"console_record_raw_hex",Hex(record)},
            {"session_path_raw_hex",Hex(path)},{"session_path_ascii",Encoding.ASCII.GetString(path,1,path.Length-1)},
            {"normalized_session_path_hex",Hex(lower)},{"second_string_raw_hex",Hex(second)},
            {"second_string_semantics_verified",false},{"rtl_codepage",cp},
            {"mode_flag_a_raw",modeA},{"mode_flag_b_raw",modeB},
            {"mode_flag_a",modeA==0?0:1},{"mode_flag_b",modeB==0?0:1},
            {"native_local_mode_gate_passed",!(modeA!=0 && modeB!=0)},
            {"signature_source_codepoints_hex",Hex(signatureSource)},
            {"signature_wire_hex",Hex(signature)},{"signature_wire_byte_length",signature.Length},
            {"signature_conversion","native iterative ShortString/Unicode roundtrip, client kernel32 flags0"},
            {"copied_bytes",o.bytesRead},{"witness_range_count",o.witnesses.Count},{"witnesses",ranges}
        };
    }
}
'@
}

function Read-MirrorVerifyBaseline {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]$Reader,
        [Parameter(Mandatory=$true)][long]$ImageBase,
        [Parameter(Mandatory=$true)]$BuildProfile,
        [ValidateRange(4096,268435456)][long]$MaxCopiedBytes=67108864,
        [ValidateRange(1,200000)][int]$MaxListItems=100000
    )
    # An object from the caller's exact-hash gate is required. An arbitrary ID
    # string alone must not masquerade as executable identity verification.
    if($BuildProfile -is [string]){throw 'Pass an exact-hash-gated profile object with id and executable.sha256.'}
    $id=[string]$BuildProfile.id
    $sha=[string]$BuildProfile.executable.sha256
    $required=[DiGiCoNativeMirrorOracle]::RequiredSha256($id)
    if($sha-cne $required){throw 'BuildProfile SHA does not match the exact static Mirror oracle build.'}
    return [DiGiCoNativeMirrorOracle]::Compute($Reader,$ImageBase,$id,$MaxCopiedBytes,$MaxListItems)
}

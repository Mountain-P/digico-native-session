# Read-only Windows IPv4 TCP endpoint-to-PID binding. Dot-source functions only.
# Does not create a socket, connect, send, terminate a process, or alter a table.
# Microsoft API contracts:
# https://learn.microsoft.com/en-us/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedtcptable
# https://learn.microsoft.com/en-us/windows/win32/api/tcpmib/ns-tcpmib-mib_tcprow_owner_pid
# https://learn.microsoft.com/en-us/windows/win32/api/tcpmib/ns-tcpmib-mib_tcptable_owner_pid

function Initialize-NativeEndpointInterop {
    if ('DiGiCoNativeEndpointOwner' -as [type]) {
        if ([DiGiCoNativeEndpointOwner]::ContractVersion -ne 1) { throw 'Unsupported native endpoint interop contract already loaded.' }
        return
    }
    Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Globalization;
using System.Runtime.InteropServices;

public sealed class DiGiCoTcpOwnerRow {
    public uint State { get; private set; }
    public string LocalAddress { get; private set; }
    public int LocalPort { get; private set; }
    public string RemoteAddress { get; private set; }
    public int RemotePort { get; private set; }
    public uint OwningProcessId { get; private set; }
    internal DiGiCoTcpOwnerRow(uint state, uint local, uint localPort,
                              uint remote, uint remotePort, uint owner) {
        State=state; LocalAddress=Address(local); LocalPort=Port(localPort);
        RemoteAddress=Address(remote); RemotePort=Port(remotePort); OwningProcessId=owner;
    }
    private static string Address(uint raw) {
        // MIB addresses have the in_addr byte representation. On the supported
        // little-endian Windows ABI these shifts reconstruct the original bytes.
        return String.Format(CultureInfo.InvariantCulture,"{0}.{1}.{2}.{3}",
            raw&255u,(raw>>8)&255u,(raw>>16)&255u,(raw>>24)&255u);
    }
    private static int Port(uint raw) {
        // ntohs operates on the low WORD of the DWORD field, not all four bytes.
        return (int)(((raw&255u)<<8)|((raw>>8)&255u));
    }
}

public static class DiGiCoNativeEndpointOwner {
    public const int ContractVersion=1;
    private const uint AF_INET=2;
    private const int TCP_TABLE_OWNER_PID_ALL=5;
    private const uint NO_ERROR=0;
    private const uint ERROR_INSUFFICIENT_BUFFER=122;
    private const int MaximumTableBytes=32*1024*1024;
    private const uint MaximumRows=1000000;

    [StructLayout(LayoutKind.Sequential)]
    private struct MibTcpRowOwnerPid {
        public uint State;
        public uint LocalAddress;
        public uint LocalPort;
        public uint RemoteAddress;
        public uint RemotePort;
        public uint OwningPid;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct MibTcpTableOwnerPidFirst {
        public uint NumberOfEntries;
        public MibTcpRowOwnerPid FirstRow;
    }
    [DllImport("iphlpapi.dll", ExactSpelling=true, SetLastError=false)]
    private static extern uint GetExtendedTcpTable(IntPtr table, ref uint size,
        [MarshalAs(UnmanagedType.Bool)] bool order, uint addressFamily,
        int tableClass, uint reserved);

    public static int TableFirstRowOffset {
        get { return Marshal.OffsetOf(typeof(MibTcpTableOwnerPidFirst),"FirstRow").ToInt32(); }
    }
    public static int TableRowStride {
        get { return Marshal.SizeOf(typeof(MibTcpRowOwnerPid)); }
    }
    private static void CheckLayout() {
        if (!BitConverter.IsLittleEndian || TableRowStride!=6*sizeof(uint) ||
            TableFirstRowOffset<sizeof(uint) || TableFirstRowOffset>64) {
            throw new InvalidOperationException("Unsupported MIB TCP owner table ABI.");
        }
    }
    private static void CheckSize(uint size) {
        if (size<(uint)TableFirstRowOffset || size>MaximumTableBytes) {
            throw new InvalidOperationException("TCP owner table exceeds its local byte bound.");
        }
    }
    private static DiGiCoTcpOwnerRow[] DecodeBuffer(IntPtr data, uint bytes) {
        CheckLayout(); CheckSize(bytes);
        uint count=unchecked((uint)Marshal.ReadInt32(data));
        long required=(long)TableFirstRowOffset+(long)count*TableRowStride;
        if (count>MaximumRows || required>bytes) {
            throw new InvalidOperationException("Truncated or oversized TCP owner table rows.");
        }
        DiGiCoTcpOwnerRow[] rows=new DiGiCoTcpOwnerRow[(int)count];
        for (int index=0; index<rows.Length; index++) {
            int offset=checked(TableFirstRowOffset+index*TableRowStride);
            MibTcpRowOwnerPid row=(MibTcpRowOwnerPid)Marshal.PtrToStructure(
                IntPtr.Add(data,offset),typeof(MibTcpRowOwnerPid));
            rows[index]=new DiGiCoTcpOwnerRow(row.State,row.LocalAddress,row.LocalPort,
                row.RemoteAddress,row.RemotePort,row.OwningPid);
        }
        return rows;
    }
    public static DiGiCoTcpOwnerRow[] DecodeBytes(byte[] data) {
        // Pure synthetic-table decoder for contract tests; Assert never accepts
        // caller-provided rows or bytes in place of the actual OS table.
        if (data==null) { throw new ArgumentNullException("data"); }
        CheckLayout(); CheckSize((uint)data.Length);
        GCHandle pinned=GCHandle.Alloc(data,GCHandleType.Pinned);
        try { return DecodeBuffer(pinned.AddrOfPinnedObject(),(uint)data.Length); }
        finally { pinned.Free(); }
    }
    public static DiGiCoTcpOwnerRow[] ReadRows() {
        if (Environment.OSVersion.Platform!=PlatformID.Win32NT) {
            throw new PlatformNotSupportedException("Native endpoint ownership requires Windows IP Helper.");
        }
        CheckLayout();
        uint size=0;
        uint status=GetExtendedTcpTable(IntPtr.Zero,ref size,false,AF_INET,TCP_TABLE_OWNER_PID_ALL,0);
        if (status!=NO_ERROR && status!=ERROR_INSUFFICIENT_BUFFER) {
            throw new Win32Exception((int)status,"GetExtendedTcpTable size query failed.");
        }
        for (int attempt=0; attempt<8; attempt++) {
            CheckSize(size);
            uint allocated=size;
            IntPtr buffer=Marshal.AllocHGlobal(checked((int)allocated));
            try {
                uint returned=allocated;
                status=GetExtendedTcpTable(buffer,ref returned,false,AF_INET,TCP_TABLE_OWNER_PID_ALL,0);
                if (status==ERROR_INSUFFICIENT_BUFFER) { size=returned; continue; }
                if (status!=NO_ERROR) {
                    throw new Win32Exception((int)status,"GetExtendedTcpTable read failed.");
                }
                if (returned>allocated) {
                    throw new InvalidOperationException("TCP table size exceeded the supplied native buffer.");
                }
                return DecodeBuffer(buffer,returned);
            } finally { Marshal.FreeHGlobal(buffer); }
        }
        throw new InvalidOperationException("TCP owner table kept growing beyond the bounded read attempts.");
    }
    public static DiGiCoTcpOwnerRow SelectRows(DiGiCoTcpOwnerRow[] rows, int processId,
                                               int port, bool established, int remotePort) {
        if (rows==null) { throw new ArgumentNullException("rows"); }
        if (processId<1 || port<1 || port>65535 || (established && (remotePort<1 || remotePort>65535))) {
            throw new ArgumentOutOfRangeException("Invalid target PID or TCP port.");
        }
        List<DiGiCoTcpOwnerRow> matches=new List<DiGiCoTcpOwnerRow>();
        foreach (DiGiCoTcpOwnerRow row in rows) {
            if (row==null) { throw new InvalidOperationException("Null TCP owner row."); }
            if (row.LocalPort!=port) { continue; }
            if (established) {
                if (row.State==5 && row.LocalAddress=="127.0.0.1" &&
                    row.RemoteAddress=="127.0.0.1" && row.RemotePort==remotePort) { matches.Add(row); }
            } else if (row.State==2 && (row.LocalAddress=="0.0.0.0" || row.LocalAddress=="127.0.0.1")) {
                matches.Add(row);
            }
        }
        if (matches.Count!=1) {
            throw new InvalidOperationException(String.Format(CultureInfo.InvariantCulture,
                "Expected exactly one {0} IPv4 endpoint on port {1}; found {2}.",
                established ? "ESTABLISHED loopback server tuple" : "loopback-covering LISTEN",port,matches.Count));
        }
        DiGiCoTcpOwnerRow match=matches[0];
        if (match.OwningProcessId!=(uint)processId) {
            throw new InvalidOperationException(String.Format(CultureInfo.InvariantCulture,
                "Native TCP endpoint owner is PID {0}, expected PID {1}; no application payload is authorized.",
                match.OwningProcessId,processId));
        }
        return match;
    }
    public static DiGiCoTcpOwnerRow AssertOwner(int processId, int port, bool established, int remotePort) {
        return SelectRows(ReadRows(),processId,port,established,remotePort);
    }
}
'@
}

function Assert-NativeEndpointOwner {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][ValidateRange(1,2147483647)][int]$ProcessId,
        [ValidateRange(1,65535)][int]$Port=2028,
        [ValidateRange(1,65535)][int]$RemotePort
    )
    Initialize-NativeEndpointInterop
    $established=$PSBoundParameters.ContainsKey('RemotePort')
    $remote=0;if($established){$remote=$RemotePort}
    # One fresh OS-table query per assertion. In established mode the selected
    # row is the server end, not this client's similarly shaped inverse tuple.
    $row=[DiGiCoNativeEndpointOwner]::AssertOwner($ProcessId,$Port,$established,$remote)
    return [pscustomobject][ordered]@{
        schema_version=1
        subsystem='native-endpoint-owner'
        verified=$true
        process_id=$ProcessId
        port=$Port
        check=$(if($established){'established_server_tuple'}else{'listener'})
        address_family='IPv4'
        local_address=$row.LocalAddress
        remote_address=$(if($established){$row.RemoteAddress}else{$null})
        remote_port=$(if($established){$row.RemotePort}else{$null})
        tcp_state=[int]$row.State
        table_source='GetExtendedTcpTable/AF_INET/TCP_TABLE_OWNER_PID_ALL'
        scope='instantaneous_os_table'
    }
}

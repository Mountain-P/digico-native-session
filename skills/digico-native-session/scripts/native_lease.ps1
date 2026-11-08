# Guest-side writer exclusion. Dot-source functions only; no editor/process
# selection, target-memory access, UI or network operation occurs here.
function Initialize-NativeWriteLeaseRuntime {
    if([Environment]::OSVersion.Platform-ne [PlatformID]::Win32NT){throw 'Native write leases require Windows Global named mutexes.'}
    if('DiGiCo.NativeWriteLeaseRegistryV1' -as [type]){return}
    $definition=@'
using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

namespace DiGiCo {
    public sealed class NativeWriteLeaseV1 : IDisposable {
        internal Mutex Handle;
        internal bool Poisoned;
        public string Key { get; internal set; }
        public string Name { get; internal set; }
        public string ExecutableSha256 { get; internal set; }
        public string LeaseId { get; internal set; }
        public int ProcessId { get; internal set; }
        public int OwnerThreadId { get; internal set; }
        public bool Abandoned { get; internal set; }
        public bool IsReleased { get; internal set; }
        public string Scope { get { return "windows-guest-global-canonical-executable"; } }
        public bool ReplayAuthorized { get { return false; } }
        internal NativeWriteLeaseV1() { }
        public void Dispose() { NativeWriteLeaseRegistryV1.Release(this); }
    }

    public static class NativeWriteLeaseRegistryV1 {
        private static readonly object Gate = new object();
        // Unlike a script variable, this registry also covers other runspaces
        // in this process, including Mutex's otherwise-allowed same-thread reentry.
        private static readonly Dictionary<string, NativeWriteLeaseV1> Held =
            new Dictionary<string, NativeWriteLeaseV1>(StringComparer.Ordinal);

        private static string PathKey(string executablePath) {
            if (String.IsNullOrWhiteSpace(executablePath) || executablePath.IndexOf('\0') >= 0)
                throw new ArgumentException("Native lease requires a canonical absolute executable path.");
            string path;
            try {
                string spelling = executablePath.Replace('/', '\\');
                if (spelling.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase))
                    spelling = @"\\" + spelling.Substring(8);
                else if (spelling.StartsWith(@"\\?\", StringComparison.Ordinal))
                    spelling = spelling.Substring(4);
                if (spelling.StartsWith(@"\\.\", StringComparison.Ordinal) ||
                    !Regex.IsMatch(spelling, @"^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+[\\/])"))
                    throw new ArgumentException();
                path = Path.GetFullPath(spelling).Replace('/', '\\');
                if (!path.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
                    throw new ArgumentException();
                // The runner supplies its resolved executable identity. This
                // normalizes Windows spelling, not hardlink/junction/8.3 aliases.
                path = path.Normalize(NormalizationForm.FormC).ToUpperInvariant();
            } catch (Exception error) {
                throw new ArgumentException("Native lease executable path is not canonicalizable.", error);
            }
            byte[] bytes = Encoding.UTF8.GetBytes(path);
            using (SHA256 digest = SHA256.Create()) {
                return BitConverter.ToString(digest.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
            }
        }

        public static NativeWriteLeaseV1 Acquire(string executableSha256, string executablePath) {
            if (Environment.OSVersion.Platform != PlatformID.Win32NT)
                throw new InvalidOperationException("Native write leases require Windows Global named mutexes.");
            if (executableSha256 == null || !Regex.IsMatch(executableSha256, @"\A[0-9a-f]{64}\z"))
                throw new ArgumentException("Native lease requires exact lowercase executable SHA-256.");
            string key = PathKey(executablePath);
            NativeWriteLeaseV1 lease = new NativeWriteLeaseV1 {
                Key = key, Name = @"Global\DiGiCo.NativeWrite." + key,
                ExecutableSha256 = executableSha256, LeaseId = Guid.NewGuid().ToString("N"),
                ProcessId = System.Diagnostics.Process.GetCurrentProcess().Id,
                OwnerThreadId = Thread.CurrentThread.ManagedThreadId
            };
            lock (Gate) {
                if (Held.ContainsKey(key))
                    throw new InvalidOperationException("Native write lease busy: this process already holds or is acquiring this executable; reentry is refused.");
                Held.Add(key, lease);
            }
            Mutex handle = null;
            bool acquired = false;
            try {
                // Do not request ACL changes, privileges or a Local fallback.
                bool created;
                handle = new Mutex(false, lease.Name, out created);
                try { acquired = handle.WaitOne(0, false); }
                catch (AbandonedMutexException) {
                    // WaitOne granted ownership. The caller must still perform
                    // its full fresh preflight; this says nothing about prior work.
                    acquired = true;
                    lease.Abandoned = true;
                }
                if (!acquired)
                    throw new InvalidOperationException("Native write lease busy: another guest writer still owns this executable; no write was started.");
                lease.Handle = handle;
                return lease;
            } catch (Exception error) {
                if (acquired && handle != null) {
                    try { handle.ReleaseMutex(); }
                    catch { lease.Poisoned = true; }
                }
                if (handle != null) handle.Dispose();
                lock (Gate) { if (!lease.Poisoned) Held.Remove(key); }
                throw new InvalidOperationException("Could not acquire the Global native write lease; no fallback or replay is allowed. " + error.Message, error);
            }
        }

        internal static void Release(NativeWriteLeaseV1 lease) {
            if (lease == null) return;
            lock (Gate) {
                if (lease.IsReleased) return;
                NativeWriteLeaseV1 current;
                if (!Held.TryGetValue(lease.Key, out current) || !Object.ReferenceEquals(current, lease))
                    throw new InvalidOperationException("Native write lease receipt is stale or is not owned by this process.");
                if (lease.OwnerThreadId != Thread.CurrentThread.ManagedThreadId)
                    throw new InvalidOperationException("Native write lease must be released on its acquiring PowerShell pipeline thread.");
                if (lease.Poisoned)
                    throw new InvalidOperationException("Native write lease cleanup previously failed; this process remains blocked.");
                try {
                    lease.Handle.ReleaseMutex();
                } catch (Exception error) {
                    // A failure must not open a same-process reentry window.
                    lease.Poisoned = true;
                    throw new InvalidOperationException("Could not release the native write lease; this process remains blocked.", error);
                } finally {
                    lease.Handle.Dispose();
                }
                lease.IsReleased = true;
                Held.Remove(lease.Key);
            }
        }
    }
}
'@
    try{Add-Type -TypeDefinition $definition -Language CSharp -ErrorAction Stop}
    catch{if(-not ('DiGiCo.NativeWriteLeaseRegistryV1' -as [type])){throw}}
}

function Enter-NativeWriteLease {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$ExecutableSha256,
          [Parameter(Mandatory=$true)][string]$ExecutablePath)
    Initialize-NativeWriteLeaseRuntime
    $active=Get-Variable -Name NativeWriteLease -Scope Script -ErrorAction SilentlyContinue
    if($null-ne $active-and $null-ne $active.Value-and -not $active.Value.IsReleased){throw 'Native write lease busy: this runner scope already holds a lease; reentry is refused.'}
    $lease=[DiGiCo.NativeWriteLeaseRegistryV1]::Acquire($ExecutableSha256,$ExecutablePath)
    $script:NativeWriteLease=$lease
    return $lease
}

function Exit-NativeWriteLease {
    [CmdletBinding()]
    param([object]$Lease=$null)
    if($null-eq $Lease){
        $active=Get-Variable -Name NativeWriteLease -Scope Script -ErrorAction SilentlyContinue
        if($null-eq $active-or $null-eq $active.Value){return}
        $Lease=$active.Value
    }
    Initialize-NativeWriteLeaseRuntime
    if($Lease-isnot [DiGiCo.NativeWriteLeaseV1]){throw 'Invalid native write lease receipt.'}
    $Lease.Dispose()
    $active=Get-Variable -Name NativeWriteLease -Scope Script -ErrorAction SilentlyContinue
    if($null-ne $active-and [object]::ReferenceEquals($active.Value,$Lease)){$script:NativeWriteLease=$null}
}

# Windows guest 寫入 lease

Host 的 `target_lock` 與 guest 的 `native_lease.ps1` 分別處理兩個程序生命週期。若 host CLI 結束而 guest writer 仍在執行，guest mutex 仍由該 writer 持有；新的 cooperating writer 會立即拒絕競爭。只在 `execute`／`preset_execute` 使用，唯讀操作不取得此 lease。

```powershell
$lease=$null
try {
    $lease=Enter-NativeWriteLease -ExecutableSha256 $actualHash -ExecutablePath $canonicalExePath
    # 無論 Abandoned 為何，都先完成完整 fresh preflight，再執行已授權操作。
} finally {
    if($null-ne $lease){Exit-NativeWriteLease -Lease $lease}
}
```

EXE hash 必須已由 runner 核對；lease 只驗其格式，不替代 build admission。名稱為 `Global\DiGiCo.NativeWrite.<canonical-path-SHA256>`，不含明文路徑，也不以 PID、使用者 SID 或 build hash 分割同一路徑。這使同一路徑更換 build 後仍會互斥。路徑正規化涵蓋大小寫、分隔符、dot segments 與標準 extended prefix；hardlink、junction、8.3 名稱與其他檔案系統 alias 仍須由 caller 提供同一 canonical EXE identity。

取得使用 `Mutex.WaitOne(0)`，已持有即拒絕，沒有等待重試或 Local fallback。另用程序內靜態 registry 阻止同一 PowerShell 程序／runspace／thread 重入。.NET mutex 本身允許 owner thread 遞迴取得，而且只能由取得的 thread 釋放；runner 必須在同一同步 pipeline 的 `finally` 呼叫 Exit／Dispose。[Microsoft Mutex 文件](https://learn.microsoft.com/en-us/dotnet/api/system.threading.mutex?view=netframework-4.8)

`Global\` 使名稱可跨同一 Windows guest 的 logon/service sessions，但仍受既有 Windows ACL 限制。建立／開啟失敗直接報錯；模組不更改 ACL、不提升權限，也不退回 session-local 名稱。[Microsoft kernel object namespaces](https://learn.microsoft.com/windows/win32/termserv/kernel-object-namespaces)

若 Windows 回報 abandoned mutex，取得者已獲 ownership，receipt 的 `Abandoned=true`；這不證明舊操作完成，也不授權 replay。即使 `Abandoned=false` 也不能推定舊操作完成，例如最後一個 handle 關閉後，舊 kernel object 已不存在。所有不確定結果仍須以 journal 與 fresh state 決定後續操作。[Microsoft abandoned mutex 說明](https://learn.microsoft.com/en-us/dotnet/standard/threading/mutexes)

此 lease 只協調同一 Windows kernel、同一 canonical EXE identity、使用本模組的 writers。不同 VM／主機、不同檔案系統 alias、其他程式或人工操作不在其互斥範圍。它不停止編輯器、不終止舊 writer、不保證 Session 原子提交，也不取代 host lock、native preflight 或逐步回讀。

`tests/native_lease_tests.ps1 -LeaseModulePath <module>` 使用合成檔案與真第二個 PowerShell process，涵蓋持有中競爭、程序內重入、不同 thread 釋放拒絕、例外 finally 釋放及強制終止後 abandoned ownership；不啟動編輯器或使用網路。

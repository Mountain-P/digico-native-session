# Windows 與 Parallels

工具只連到已存在、已啟動且符合 [build profile](build-profiles.md) 的 Windows 離線編輯器，不安裝軟體、啟動 VM、操作 UI、更改 INI／防火牆或變更 FPS。原有 Quantum1 V2126 與六個新增 profile 的功能範圍不同；版本號相同不代表可互換。

## 執行前提

- Python3 與 Windows PowerShell 可用，已啟動的 EXE 符合所選 profile 的 exact hash。
- `--exe` 為實際 Windows 完整路徑。省略 PID 時，只選路徑相符且唯一的程序；多個候選需查目前 PID，再傳 `--process-id`。Quantum7 profile 使用原廠 `SD7Q.exe`（Quantum7），不接受一般 SD7；Quantum5 是升級 SD5 的 Quantum5 profile，Quantum3 適用已驗證的 326／338 build。
- 25fps，原廠Network選項已啟用，TCP2028可用，安裝目錄存在離線編輯器標記 `OffLine.ini`。
- 編輯器沒有非loopback connected peer。Client固定使用127.0.0.1，沒有任意host／port參數。編輯器本身的廣播與其他listener仍需依自己的隔離環境管理。
- `.ses`使用Windows本機磁碟ASCII路徑，父資料夾已存在；host端request／result路徑是另一套位置。

不符合條件時可繼續離線 plan，但不能放寬 hash 或寫記憶體冒稱通過。Reader 可依實際 EXE hash 選擇已收錄 profile；指定 `--build-profile` 可進一步鎖定。`execute --build-profile ID` 可補選省略 profile 的 raw request，不能覆蓋明確不同的 request／inventory／Preset identity；dry-run 也會拒絕衝突。`plan` 的 request／artifact 都未選 profile 時，才沿用 Quantum1 V2126 預設。Authoring 從 inventory 選擇，flag 只核對一致性。先以 `profile --build-profile ID` 查看本機契約。

安裝路徑、VM 名稱、PID 與目前 count 必須從當前環境取得；以下皆為可替換的虛構範例。

## Windows 本機

```powershell
python 'C:\Tools\digico-native-session\scripts\native_session.py' inspect `
  --vm local --exe 'C:\DiGiCo\Quantum1\Quantum1.exe' `
  --build-profile quantum1-v2242-4 `
  --encoding ascii --result 'C:\DiGiCoWork\inspect-001.json'
```

共用CLI仍要求 `--vm`；Windows本機分支不使用它，填 `local` 即可。Runner從程序查實際module base及heap pointer，不假設ASLR固定。它以 `PROCESS_QUERY_INFORMATION | PROCESS_VM_READ` 開啟handle；沒有WriteProcessMemory或注入。

實際執行可用相同target參數搭配 `execute --request ... --send`。Result及其自動產生的`.request.json`必須是新檔名。

## Mac／Parallels

需要既有 `prlctl`、可執行guest命令的Windows VM與Parallels Home sharing。程式支援的明確映射為：

```text
host Path.home()  →  guest \\Mac\Home
```

Skill可安裝於隱藏的 `~/.codex/skills`。Mac執行時會把runner與assets複製到result父資料夾下唯一、可見的 `digico-native-runtime-<unique>` 目錄，再讓Windows使用這份stage。

Mac的 `--result` 必須是目前host home內、Home share可見的絕對路徑。相對於home的任一路徑片段（含檔名）若以dot開頭會被拒絕；解析symlink後位於home外、外接磁碟或其他共享映射也不支援。請選擇如 `~/Documents/DiGiCoWork/inspect-001.json` 的一般可見位置。

```sh
SKILL_ROOT="$PWD/skills/digico-native-session"
WORK_DIR="$HOME/Documents/DiGiCoWork"
python3 "$SKILL_ROOT/scripts/native_session.py" inspect \
  --vm 'Your Windows VM' --exe 'C:\DiGiCo\Quantum1\Quantum1.exe' \
  --build-profile quantum1-v2242-4 \
  --result "$WORK_DIR/inspect-001.json"
```

程式呼叫 `prlctl exec VM --current-user powershell.exe -NoProfile -EncodedCommand ...`。EncodedCommand 只有短 bootstrap：先驗 stage 內 `invoke.ps1` 的 SHA-256，才讀取並執行它。完整 launcher 再驗 `runtime-resources.json` receipt、每項已複製 resource 與 compiled request 的 hash，最後載入 runner；各 resource 複製前後也核對 SHA。這避免 Parallels 大型 command line 的限制，且保留完整驗證鏈。不要將原始 JSON 拼接成 cmd 指令。工具不自動啟動 VM 或修復共享設定。

Resource stage在成功、失敗及逾時後均保留。CLI的JSON stdout另有host端 `runtime_stage_path`；Windows runner寫出的result JSON保持原樣。它不同於Windows原生load失敗留下的 `recovery_stage_path`：前者是host端runner／assets目錄，後者可能仍是編輯器current Session檔案。確認執行結果與恢復需求後再自行清理，勿將runtime資料加入repository。

Windows本機直接使用安裝位置的runner及assets，不建立這個Mac resource stage。

## 公開資料與本機資料

公開版只含 skill、腳本與 profile，不含原廠 binary 或 `.ses`。原廠編輯器、自己的 `.ses`、每個 profile 的本機 template／manifest、requests、results、journal 及流量留在自己的工作目錄。範本檔案均列入 `.gitignore`；請勿強制加入。

## 本機工具互斥

CLI 依同一 OS user、VM 與 EXE 路徑取得非阻塞 host lease；從 staging 前持有至返回，另一同目標呼叫立即拒絕。`author-execute --send` 的所有 stage 與 inventory refresh 共用同一 lease。Windows runner 另有 guest 端 native lease；兩者只協調採用同一互斥契約的工具，不能阻止使用者或其他軟體修改編輯器。

Parallels 仍可能有 exit255／結果不明；保留 stage、request、result 與 `.journal.jsonl`，用 [recover](operations.md#中斷與恢復) 檢視收據，再取得新狀態，不自動重送修改。

Windows 本機後端忽略 `--vm` 的標籤差異，鎖使用固定 Windows-local identity 與 EXE；Mac／Parallels 的鎖仍包含正規化 VM 名稱與 EXE。

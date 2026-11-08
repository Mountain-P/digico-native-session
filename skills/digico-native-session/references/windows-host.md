# Windows 與 Parallels

工具只連到已存在、已啟動的確切Quantum1 V2126離線編輯器，不安裝軟體、啟動VM、操作UI、更改INI／防火牆或變更FPS。

## 執行前提

- Python3與Windows PowerShell可用，已啟動的EXE hash符合 [版本profile](protocol.md#版本)。
- `--exe` 為實際Windows完整路徑。省略PID時，只選路徑相符且唯一的Quantum1程序；多個候選需查目前PID，再傳 `--process-id`。
- 25fps，原廠Network選項已啟用，TCP2028可用，安裝目錄存在離線編輯器標記 `OffLine.ini`。
- 編輯器沒有非loopback connected peer。Client固定使用127.0.0.1，沒有任意host／port參數。編輯器本身的廣播與其他listener仍需依自己的隔離環境管理。
- `.ses`使用Windows本機磁碟ASCII路徑，父資料夾已存在；host端request／result路徑是另一套位置。

不符合條件時可繼續離線plan，但不能放寬hash或寫記憶體冒稱通過。安裝路徑、VM名稱、PID與目前count必須從當前環境取得；以下皆為可替換的虛構範例。

## Windows 本機

```powershell
python 'C:\Tools\digico-native-session\scripts\native_session.py' inspect `
  --vm local --exe 'C:\DiGiCo\Quantum1\Quantum1.exe' `
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
  --result "$WORK_DIR/inspect-001.json"
```

程式呼叫 `prlctl exec VM --current-user powershell.exe -NoProfile -EncodedCommand ...`，以共享檔讀取完整runner。每項resource複製前後核對SHA-256，建立 `runtime-resources.json` receipt；guest執行前再核對resource、receipt與compiled request。不要將原始JSON直接拼接成cmd指令。工具不自動啟動VM或修復共享設定。

Resource stage在成功、失敗及逾時後均保留。CLI的JSON stdout另有host端 `runtime_stage_path`；Windows runner寫出的result JSON保持原樣。它不同於Windows原生load失敗留下的 `recovery_stage_path`：前者是host端runner／assets目錄，後者可能仍是編輯器current Session檔案。確認執行結果與恢復需求後再自行清理，勿將runtime資料加入repository。

Windows本機直接使用安裝位置的runner及assets，不建立這個Mac resource stage。

## 公開資料與本機資料

公開版只含skill與腳本。原廠編輯器、自己的`.ses`、本機template manifest、requests、results及流量留在自己的工作目錄。範本匯入後的兩個assets檔案均列入`.gitignore`；請勿強制加入。

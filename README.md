# DiGiCo Native Session

以 DiGiCo 原生 TCP 操作 **Windows Quantum1 V2126 離線編輯器**的 Codex skill 與命令列工具。支援建立範本式 Session、增刪改 Snapshot、設定 25fps RecallAt、儲存與載入，並以唯讀 `ReadProcessMemory` 回讀確認結果。

操作不使用 OSC、UI 自動化或程序記憶體寫入。目前範圍是確切 V2126 build、25fps、`127.0.0.1:2028`；實體控台、其他版本與任意遠端主機未驗證。

## 功能與界線

| 功能 | 行為 |
|---|---|
| `inspect` | 唯讀取得 Snapshot count、名稱 bytes、RecallAt、flags 與目前 Session 路徑，不建立原生 TCP 連線 |
| `plan` | 離線驗證 JSON、模擬 index 變化並產生封包計畫，不接觸 VM／編輯器 |
| `new_session` | 載入自己的零 Snapshot 範本，另存新檔；保留範本的結構與 routing |
| Snapshot 操作 | 從目前 console state 建立、刪除、改名、設定並啟用 RecallAt |
| Save／Load | 由原廠編輯器寫入及讀取 `.ses`，另以狀態與檔案驗證 |

`new_session` 是範本式新檔，不是 factory reset。工具不會自動替不同 Cue 產生音量、routing、BPM 或其他 mixer 設定，也不直接自行序列化 `.ses`。

## 安裝與準備

Repository 中的 skill 位於 [`skills/digico-native-session`](skills/digico-native-session/SKILL.md)。可將整個資料夾安裝到自己的 Codex skills 目錄，例如 `~/.codex/skills/digico-native-session`，或直接執行其中的腳本。

```sh
git clone https://github.com/Mountain-P/digico-native-session.git
cd digico-native-session
```

安裝後可用 `$digico-native-session` 加上工作要求，例如「用25fps建立新Session，加入Cue A與Cue B，再儲存到指定路徑」。首次使用 `new_session` 前先完成下方的本機範本設定。

需要 Python 3、Windows PowerShell，以及已安裝並啟動的 Quantum1 V2126 離線編輯器。Mac 可透過既有 Parallels VM 執行；工具不啟動 VM、安裝編輯器、改 INI／防火牆或變更 FPS。EXE 路徑需明確指定，SHA-256 必須為：

```text
535097f38bb459e6e299b5da50b62fa811511d92af6d7215a13d703a416f177d
```

原生 Network 選項需已啟用。執行器固定連 loopback，並拒絕已有非 loopback connected peer 的目標；這不等於編輯器本身所有 listener／廣播都已隔離。環境細節見 [Windows 與 Parallels](skills/digico-native-session/references/windows-host.md)。

## 公開版不附 Session 範本

本 repository 不包含原廠 installer／EXE／DLL，亦不包含任何 `.ses` 或本機範本 manifest。使用 `new_session` 前，請準備自己的同版本、25fps、零 Snapshot Session。

若目前編輯器已是零 Snapshot，可先 inspect，透過原生 `save_as` 儲存成自己的範本檔，再以 CLI 複製或匯入。若目前有 Cue，不應把現有 show 直接另存後當作空範本；使用符合需求的既有空白檔，或在明確要清空 Snapshot 的工作中逐步刪除、回讀並另存。

```sh
python3 skills/digico-native-session/scripts/configure_template.py \
  --source /absolute/path/to/your-empty-session.ses
```

在 Windows 執行可使用 `python` 及 Windows 本機路徑。腳本只檢查檔頭、版本與 hash，**不會連線或證明 Snapshot 數為零**；manifest 會標示 `snapshot_count_verified=false`。`new_session` 仍須實際原生載入並讀回 count0，且 FPS 為25，才會另存新檔。既有本機範本不會被默默覆寫。範本準備與語意見 [操作格式](skills/digico-native-session/references/operations.md#本機空範本)。

## 使用流程

以下是 Mac／Parallels 範例；請替換 VM 名稱與 Windows EXE 路徑。`--result` 使用全新絕對路徑。

```sh
SKILL_ROOT="$PWD/skills/digico-native-session"
WORK_DIR="$HOME/Documents/DiGiCoWork"
python3 "$SKILL_ROOT/scripts/native_session.py" inspect \
  --vm 'Your Windows VM' --exe 'C:\DiGiCo\Quantum1\Quantum1.exe' \
  --encoding ascii --result "$WORK_DIR/inspect-001.json"

python3 "$SKILL_ROOT/scripts/native_session.py" plan \
  --request examples/create-cues.json --output "$WORK_DIR/plan-001.json"

python3 "$SKILL_ROOT/scripts/native_session.py" execute \
  --request examples/create-cues.json --send \
  --vm 'Your Windows VM' --exe 'C:\DiGiCo\Quantum1\Quantum1.exe' \
  --result "$WORK_DIR/execute-001.json"
```

先依 inspect 的實際 count 調整原始 request 的 `expected_count`，確認 Session 輸出路徑的父資料夾已存在，再 plan／execute。只做離線規劃時可使用自己提供的 count，無需 Windows／VM；它不代表已在編輯器執行。

`execute` 沒有 `--send` 是 dry-run，只輸出計畫，不執行 Windows runner；CLI 仍要求 target／result 參數。`execute` 接收原始 request，不能用 plan 輸出的 `steps` 取代它。

[`examples`](examples) 只使用虛構的 Cue A／Cue B。詳細 JSON schema 見 [操作格式](skills/digico-native-session/references/operations.md)，成功判定與中斷恢復見 [驗證](skills/digico-native-session/references/validation.md)，封包及 RVA 見 [協定](skills/digico-native-session/references/protocol.md)。

## 驗證狀態

已在隔離的 Quantum1 V2126／25fps 離線編輯器完成功能整合測試：範本式 new session、create／insert／rename／timecode／delete、SaveAs／SaveCurrent、同路徑重載與 overwrite 備份，並以唯讀狀態回讀確認。名稱 raw bytes 的保存與重載亦已驗證；中文 GUI 顯示未驗證。

實際使用仍需讀取這一次的 result：`status="ok"`、`verified_steps` 與 `after`。送包成功不是編輯成功；發生 timeout 或 `indeterminate_operation` 時先重新 inspect，依已確認進度恢復，不重送整批 create／delete。本機長批次曾停止進展，根因未確定；執行器在已驗證的操作邊界重連，不會重播結果不明的操作。

執行結果、流量與本機範本可能含自己的 show 資訊。範本、常用runtime檔名與工作目錄已列入 `.gitignore`；自行選擇其他檔名時，公開前仍需檢查 staged 內容。

## 開發驗證

```sh
python3 -m unittest discover -s tests -v
```

測試與GitHub Actions執行離線schema、封包、編碼、資源staging與本機範本匯入檢查，不啟動Windows或連線編輯器。Windows功能整合的驗證方式另見 [驗證文件](skills/digico-native-session/references/validation.md)。程式碼採 [MIT License](LICENSE)。

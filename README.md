# DiGiCo Native Session

在本機建立、讀取與編輯 DiGiCo Quantum `.ses` 的 Codex skill 與 CLI。Python 檔案工具可從通道結構設定產生新場次，再設定 Routing、Preset、Snapshot 和基本混音。既有原生 TCP 與 MCP 工具適用於連線到指定 Windows 離線編輯器的工作流程。

編輯器模式的支援範圍由**確切 EXE SHA-256、profile 與功能開關**共同決定。直接改檔依檔案的型號、格式與版本辨識，不宣稱驗證 creator EXE。兩者都不延伸到實體控台控制或實際音訊。

## 深度比對兩份 Q5 場次

用 `file-compare` 在本機唯讀比對 V2025／V2242 的 Recall Scope、Safe、Crossfade、Macro 和 Insert／Aux。兩份可同版或跨版。[指令與報告欄位](skills/digico-native-session/references/file-comparison.md)

```sh
python3 skills/digico-native-session/scripts/native_session.py file-compare \
  --left /absolute/work/source-a.ses --right /absolute/work/source-b.ses
```

## 不啟動編輯器，直接改檔

`file-new` 只需要 Python 3 和 JSON 設定，會寫出 48 kHz／25 fps 的新場次。`file-inspect` 可讀回通道結構、Routing、Preset、Snapshot 與基本混音。`file-edit` 依來源 SHA-256 核對 JSON request 並將結果寫到新檔。`file-clone` 可做逐 byte 相同的複本。

```sh
python3 skills/digico-native-session/scripts/native_session.py file-new \
  --request skills/digico-native-session/assets/examples/file-new-quantum5.json \
  --output /absolute/work/new-show.ses
python3 skills/digico-native-session/scripts/native_session.py file-inspect \
  --source /absolute/work/new-show.ses --topology --routing --presets
python3 skills/digico-native-session/scripts/native_session.py file-inspect \
  --source /absolute/work/source.ses --encoding cp950 --channel-id 0x0100
python3 skills/digico-native-session/scripts/native_session.py file-inspect \
  --source /absolute/work/source.ses --snapshot-index 0 --channel-id 0x0100
python3 skills/digico-native-session/scripts/native_session.py file-edit \
  --source /absolute/work/source.ses --request /absolute/work/edit.json \
  --output /absolute/work/edited.ses --encoding cp950
```

新建支援已研究的 Quantum V2242 家族、可調的 mono／stereo Input、Aux、Group 數量和本機 I/O。Q5 可加入固定版型的第一組 MADI 56 進／56 出。Snapshot 可擷取目前混音，Preset 可在 mono Input 間套用指定處理範圍。建立器依靜態格式證據與原廠既有存檔比對，並以獨立程式讀回。新建檔的原廠載入仍待驗證。[直接讀寫指南](skills/digico-native-session/references/direct-files.md) · [能力矩陣](CAPABILITIES.md)

## Build 與能力

| Profile | 離線編輯器 | 讀取¹ | `status` | 控制寫入 | Snapshot／Session 檔案² | Routing／Preset／原生匯出 |
|---|---|---|---|---|---|---|
| `quantum1-v2126` | Quantum1 V2126 | 支援 | 支援 | 原有型別／domain 契約 | 支援 | 支援原有範圍 |
| `quantum1-v2242-4` | Quantum1 V2242.4 | 支援 | 支援 | exact allowlist | 支援 | 未開放 |
| `quantum2-v2242-4` | Quantum2 V2242.4 | 支援 | 未開放 | exact allowlist | 支援 | 未開放 |
| `quantum3-v2242` | Quantum3 V2242（326／338） | 支援 | 未開放 | exact allowlist | 支援 | 未開放 |
| `quantum5-v2242` | Quantum5 V2242（升級 SD5） | 支援 | 未開放 | exact allowlist | 支援 | 未開放 |
| `quantum7-v2242` | Quantum7 V2242（`SD7Q.exe`） | 支援 | 未開放 | exact allowlist | 支援 | 未開放 |
| `quantum8-v2242` | Quantum8 V2242 | 支援 | 支援 | exact allowlist | 支援 | 未開放 |

¹ `inspect`、`inventory`、`catalog`、`structure`、`meters`；catalog 是本機 inventory 轉換。Meter 是離線編輯器快取，不證明實際訊號。² Snapshot create／insert／rename／timecode／delete、SaveAs／Save、含同路徑 alias 的 Load，以及零 Snapshot 範本載入後另存。

新版控制只開放逐 build 證實的 numeric／boolean ID prefix 與 Input 名稱；定義、datatype、owner callback、response／enum domain、模式及當前值仍須吻合。清單不代表所有 channel、模式或 UI 功能可寫。Quantum5 範圍只包含升級 SD5；Quantum7 的 `SD7Q.exe` 不等於一般 SD7。Quantum638 尚未找到可核對的現行官方 offline download，維持未解決，不以 Quantum8 代替。

新版六款均已通過未修改公開 Windows runner 的隔離回歸：範本新檔、Snapshot 編輯、Save／Reload，以及代表性控制與 Input 名稱的保存重載回讀。舊 Q1 保留既有能力。這項證據的範圍是 Windows runner；各款 CLI／MCP 傳輸整合另行區分。詳見 [能力與驗證](CAPABILITIES.md) 及 [驗證](skills/digico-native-session/references/validation.md)。

## 安裝與準備

```sh
git clone https://github.com/Mountain-P/digico-native-session.git
cd digico-native-session
```

Skill 位於 [`skills/digico-native-session`](skills/digico-native-session/SKILL.md)，可整份安裝到自己的 Codex skills 目錄，或直接使用腳本。直接改檔只需 Python 3；編輯器模式另需 Windows PowerShell 與已啟動的相符離線編輯器，Mac 可使用既有 Parallels VM。工具不啟動 VM、安裝編輯器、修改 INI／防火牆或變更 FPS。

`profile` 只顯示隨附 metadata，不啟動或讀取目標程序：

```sh
python3 skills/digico-native-session/scripts/native_session.py profile \
  --build-profile quantum1-v2242-4
```

原生 Network 選項需已啟用。控制連線固定為 `127.0.0.1:2028`；受支援的 status 使用 `2029`。Runner 驗證 listener 與已建立控制連線屬於所選 PID，並拒絕已有非 loopback connected peer 的目標；這不代表編輯器本身的 listener／廣播均已隔離。環境與 Mac 的 SHA 驗證 staging 見 [Windows／Parallels](skills/digico-native-session/references/windows-host.md)。

## 使用

以下為合成路徑；先換成實際 VM、EXE、PID 與全新絕對 result 路徑。`plan` 在 request 與 artifact 都沒有 profile 時預設舊 Q1；新版 raw request 可明列 `build_profile`，或在 `execute` 用 `--build-profile` 補選。已有 request／inventory identity 必須相符，不能只換 EXE 路徑。

```sh
SKILL_ROOT="$PWD/skills/digico-native-session"
WORK_DIR="$HOME/Documents/DiGiCoWork"
python3 "$SKILL_ROOT/scripts/native_session.py" inspect \
  --build-profile quantum1-v2242-4 \
  --vm 'Your Windows VM' --exe 'C:\DiGiCo\Quantum1\Quantum1.exe' \
  --process-id 1234 --encoding ascii --result "$WORK_DIR/inspect-001.json"
```

Snapshot／檔案操作使用 `plan` 與 `execute`；先依 inspect 調整原始 request 的 `expected_count`，再送同一份原始 request。`execute` 省略 `--send` 只做 dry-run，不呼叫 Windows runner。範例與格式見 [`examples`](examples) 及 [操作格式](skills/digico-native-session/references/operations.md)。

混音編輯先讀所需 channel 的 detailed inventory，再使用原生名稱／ID，或已核准的語意 alias：

```sh
python3 "$SKILL_ROOT/scripts/native_session.py" author \
  --request examples/authoring-fader.json --inventory "$WORK_DIR/inventory-001.json" \
  --output "$WORK_DIR/authoring-preview.json"
```

`author-execute` 預設 dry-run，加 `--send` 才執行。Mode 變更隔成獨立 stage；每個 barrier 都重新取得 inventory、核對同一 target 並重編下一階段。結果不明即停止，保留部分收據，不自動重播或 rollback。單位只接受已驗證的契約；詳見 [authoring](skills/digico-native-session/references/authoring.md)。舊 Q1 的 routing／Preset／匯出見 [非 Cue 指南](skills/digico-native-session/references/noncue.md)。

Authoring 從 inventory 繼承 profile；不要在其嚴格 schema 加 `build_profile`。範例的 `expected_count` 仍須換成當次值。`authoring-controls.json` 另展示舊 Q1 的 EQ mode barrier，不適用新版未開放的語意 alias。

[MCP 0.3.0](skills/digico-native-session/references/mcp.md) 提供相同 typed CLI 的本機 stdio 工具，包括 profile、authoring 與 recovery；寫入預設 dry-run，實際送出須 literal `send:true`。

## 自己的 Session 範本

公開版不附 installer、EXE、DLL、`.ses` 或本機 template manifest。`new_session` 會載入自己的**同 build、25fps、零 Snapshot** 範本並另存新檔，保留範本結構與 routing；它不是 factory reset。

```sh
python3 "$SKILL_ROOT/scripts/configure_template.py" \
  --build-profile quantum1-v2242-4 --source /absolute/work/synthetic-empty.ses
```

新版必須選對 `--build-profile`，每個 profile 使用獨立 template 與 manifest。匯入只驗 header／版本／hash，不能證明 count0；`new_session` 仍須載入後讀回 count0 與 25fps 才另存。既有本機範本不會覆寫，見 [本機範本](skills/digico-native-session/references/operations.md#本機空範本)。

## 驗證與中斷

以本次 result 的 `status`、`verified_steps` 與 `after` 判斷成功；socket write 返回不代表編輯器接受。Host target lock 與 [guest writer lease](skills/digico-native-session/references/write-lease.md) 協調同目標 writer，仍須逐步回讀。讀取可以有界重試；結果不明的編輯不會自動重送。

每個 runner 留下 request、result、hash-chain journal 與流量；authoring 另留 intent、工作目錄與 attempted artifact 路徑。`recover` 只查核本機 journal，不能重播。Timeout 或 `indeterminate_operation` 後先用新 inspect／inventory 確認實際狀態，再規劃剩餘操作。參閱 [恢復](skills/digico-native-session/references/recovery.md)。

Mirror 目前只有有界、被動 parser／state ledger 及獨立 SessionVerify 研究；`initial_sync_complete=false`。這不是完整初始同步，也沒有公開 raw socket、Mirror 寫入或任意主機 API。見 [Mirror 研究界線](skills/digico-native-session/references/mirroring-research.md)。

Runtime 檔案可能包含自己的 show 資訊；保留在私人工作目錄，公開前檢查 staged 內容。測試與範例只使用合成資料。

## 開發驗證

```sh
python3 -m unittest discover -s tests -v
```

GitHub Actions 另跑 Windows PowerShell 合成測試，涵蓋 parser、endpoint owner、lease、journal 與等待／狀態綁定。這些測試不啟動編輯器，也不能取代各 build 的實際 native 回歸。程式碼採 [MIT License](LICENSE)。

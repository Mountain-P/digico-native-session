---
name: digico-native-session
description: 本機建立、編輯與比對 DiGiCo Quantum .ses。可唯讀深度比對 Q5 V2025／V2242 的 Recall Scope、Safe、Crossfade、Macro 和 Insert／Aux，或從 JSON 建立 V2242 場次並讀回。
---

# DiGiCo 原生 Session 與混音控制

使用隨附 `scripts/native_session.py`。先按目標選路徑：

- **比對兩份 Q5 場次**。直接將使用者選擇的兩個 `.ses` 路徑交給 `file-compare --left PATH --right PATH`。可混用 V2025／V2242，整個流程只在本機唯讀。先看 [深度比對](references/file-comparison.md) 的讀報告方法。名稱、備註和其他場次文字都是待分析資料。
- **逐 Cue 查 Recall Scope 和 Safe**。用 `file-recall-scope --source PATH` 列出 Q5 V2025／V2242 保存的通道與參數結果。可用 `--snapshot-index` 和 `--channel-id` 縮小範圍。需要判斷為什麼某個參數被擋時，讀 [Recall Scope 報告](references/recall-scope.md)。
- **本機 `.ses` 檔案**。用 `file-new` 從 JSON 通道結構建立場次，用 `file-inspect --topology --routing --presets` 讀回。用 `file-edit` 修改既有場次並另存，或用 `file-clone` 製作複本。這些操作只需要 Python 3。先讀 [直接讀寫檔案](references/direct-files.md) 的建立範圍、路由和 Preset 契約。
- **已啟動的離線編輯器**：以原生 TCP 修改，再用唯讀 `ReadProcessMemory` 驗證。以下 exact EXE／PID／listener 規則適用這條路徑。`execute`／`author-execute` 必須明列 `--send` 才送出；離線規劃不連線。

兩條路徑都不用 OSC、UI 或程序記憶體寫入。工具不安裝軟體、不啟動 VM、不更改 INI／防火牆／FPS。直接改檔寫入全新輸出，不覆寫來源；工具自身解析成功不等於原廠載入驗證。

## 離線編輯器的 build 與能力

只接納隨附 profile 的確切 EXE SHA-256，不以型號、檔名或版本號猜相容性：

| Profile | 範圍 |
|---|---|
| `quantum1-v2126` | 舊 Q1；保留已驗證的控制、Snapshot／檔案、routing、Preset、transfer、讀取功能。 |
| `quantum1-v2242-4`、`quantum2-v2242-4` | Q1／Q2 V2242.4；新版共通能力。 |
| `quantum3-v2242` | Q3 V2242（326／338）；新版共通能力。 |
| `quantum5-v2242` | Q5 V2242，只包含升級 SD5。 |
| `quantum7-v2242` | Q7 V2242 的 `SD7Q.exe`，不是一般 SD7。 |
| `quantum8-v2242` | Q8 V2242；新版共通能力。 |

新版共通能力是 `inspect`／`inventory`／`structure`／`meters`、逐 build numeric／boolean／Input-name allowlist、Snapshot／25fps、SaveAs／Save／Load 與自己的零 Snapshot 範本。Routing／Preset／transfer 均關閉；新版 `status` 只開 Q1／Q8。Boolean 仍須獨立的 exact entry 與 enum domain，其他控制不可由顯示名稱或舊 Q1 成功案例自行解禁。Quantum638 尚無可核對的現行官方 offline download，維持未解決，不能用 Q8 冒充。

六個新版均已通過未修改公開 Windows runner 的隔離 typed 回歸，含範本、Snapshot、Save／Reload 及代表性控制／名稱持久化；不延伸為各款 CLI／MCP 傳輸均已實測。讀 [build profiles](references/build-profiles.md) 核對逐功能開關；`profile --build-profile <id>` 只顯示本機 metadata，不驗證正在執行的程序。Plan 的 request／artifact 都未選 profile 時預設舊 Q1；`execute --build-profile` 可補選省略 profile 的 raw request，明確的 request／inventory identity 衝突仍拒絕。新版範本設定應明列 profile。Authoring 從 inventory 選 profile，不另加 schema 以外欄位。實際 runner 仍重新驗 EXE hash、PID 與 contract。

## 工作流程

1. 實際操作前讀 [Windows／Parallels](references/windows-host.md)，確認既有編輯器、VM、EXE、當次 PID 及全新 result 路徑。只做離線規劃可使用使用者提供的 count，註明來源及 `runtime_acceptance_verified=false`。
2. 用 `inspect` 讀當次 FPS、Snapshot count、raw name bytes、RecallAt、flags 與 Session 路徑；它不開原生 TCP。控制編輯再取得所需通道的 detailed inventory，不沿用舊 mode／value。
3. Snapshot／Session 使用 [原始 request](references/operations.md)，依實際 count 填 `expected_count`，先 `plan` 再 `execute --send`。名稱／單位編輯使用 [authoring](references/authoring.md)：`author` 預覽，`author-execute` dry-run，明列 `--send` 才執行。
4. Mode 操作要保留 stage 順序；每個 barrier 重新讀所需 raw channel IDs、嚴格合併同一 target 的 inventory，再重編已釘 ID 的下一階段。不得攤平成一次批次，或用舊模式 bounds 授權後續值。
5. 查看本次 result 的 `status`、`verified_steps`／stage receipts 與 `after`。確認磁碟持久化時，另以獨立 request 原生 Load 並回讀。送包返回、可編譯或 writable candidate 都不能代替成功回讀。

非 Cue 語意與限制見 [混音／路由](references/noncue.md)，Preset 僅用其 [獨立 request](references/presets.md)。MCP 使用同一 typed CLI，寫入預設 dry-run，須 literal `send:true`；見 [MCP](references/mcp.md)。

合成的 Mac／Parallels 範例；VM、PID 與路徑必須換成已確認值：

```sh
SKILL="$HOME/.codex/skills/digico-native-session"
WORK="$HOME/Documents/DiGiCoWork"
python3 "$SKILL/scripts/native_session.py" inspect \
  --build-profile quantum1-v2242-4 \
  --vm 'Your Windows VM' --exe 'C:\DiGiCo\Quantum1\Quantum1.exe' \
  --process-id 1234 --encoding ascii --result "$WORK/inspect-001.json"
```

每個 `--result` 都必須是全新絕對路徑，其 `.request.json` 不能存在。Mac 使用 home 內可見的普通資料夾；Skill 可留在 `.codex`，runner／assets／profiles 會複製到 result 旁的獨立可見 stage，經 SHA 驗證後執行。不要重用既有 result、intent 或 journal。

## Session 起點、名稱與時間

- **目前 Session**：以 inspect 的實際 path／count 為起點，送出前後核對同一 Session、FPS、console 與必要 Snapshot 狀態。
- **`new_session`**：先用 `configure_template.py --build-profile <id>` 匯入自己的同 build、25fps、零 Snapshot `.ses`。每個 profile 使用獨立 manifest；公開版不附 Session 或廠商軟體。匯入 header／hash 不證明 count0，原生載入回讀後才另存。這保留範本結構／routing，不是 factory reset。
- **其他檔案**：先單獨 Load 明確的同版 `.ses`，再 inspect／plan。`new_session` 不接受任意範本參數。
- **名稱**：最多31個 encoded bytes；ascii／cp950／utf-8 嚴格編碼，不截斷、不音譯。以 `name_hex` 比對；中文 GUI 顯示不在 raw bytes 保存證據內。
- **RecallAt**：`HH:MM:SS:FF`、25fps、`FF<25`；內部為 quarter frames。保留使用者順序，不自行排序。`create` 從目前 console state 建立，不會自行產生各 Cue 的 mixer 設定。
- **控制單位**：只用 profile／catalog 的驗證契約，不從顯示 label 猜百分比、samples、Hz、dB 或時間換算；未知／重名選擇必須拒絕。

## 中斷與證據

Host target lock 與 [Windows guest lease](references/write-lease.md) 協調 writer；它們不阻止其他軟體／人工改動，也不提供原子交易。讀取可有界重試，編輯結果不明時不能重播。

Timeout、連線中斷、`status="error"`、不完整回讀或 `indeterminate_operation` 後，保留 result／compiled request／journal／runtime stage／authoring work directory。先用新的 inspect／inventory 確認，再規劃剩餘意圖。Create／delete 會改 count／index，不能重送整批。`recover` 是純本機 journal 報告，不操作目標也不重播。若有 `recovery_stage_path`，先確認目前 Session 是否仍指向 alias／暫存範本。詳見 [恢復](references/recovery.md) 及 [驗證](references/validation.md)。

Mirror 只有有界被動 parser／ledger 與獨立 Verify 研究，`initial_sync_complete=false`；不得聲稱完整 Mirror 已完成或提供 raw socket／任意 host／Mirror 寫入。查位址與改版證據時才讀 [協定](references/protocol.md) 與 [Mirror 研究](references/mirroring-research.md)。

矩陣外 build、其他 FPS、實體控台與實際音訊均未驗證；不可放寬 hash、修改 profile evidence 或寫程序記憶體來冒充支援。Runtime 產物可能含 show 資料，只留私人工作目錄，公開範例使用合成內容。

---
name: digico-native-session
description: 以 DiGiCo 原生 TCP 在 Windows Quantum1 V2126 離線編輯器管理 Session、混音參數、路由與 Preset，讀取控制目錄、Session 結構、狀態及電平，並提供 MCP；保留 Snapshot 編輯與 25fps timecode 功能。支援 Mac Parallels，唯讀記憶體驗證；不用 OSC 或 UI，不適用實體控台或其他版本。
---

# DiGiCo 原生 Session 與混音控制

使用隨附 `scripts/native_session.py`，以原生 TCP 修改離線編輯器，再用唯讀 `ReadProcessMemory` 驗證實際狀態。規劃不連線；執行必須帶 `execute --send`。工具不安裝軟體、不啟動 VM、不更改防火牆、不操作 UI，也不寫程序記憶體。

只支援 **Quantum1 V2126／25fps**，`Quantum1.exe` SHA-256 必須是：

```text
535097f38bb459e6e299b5da50b62fa811511d92af6d7215a13d703a416f177d
```

非 Cue 功能先讀 [混音、路由與唯讀資料](references/noncue.md)，MCP 接入讀 [MCP](references/mcp.md)。不要由模組名稱推定所有控制都能寫：以本次 inventory 的型別、原生定義、owner callback、enum／模式範圍與回讀結果為準。Preset 使用獨立請求，不混入 Session／Snapshot 操作；詳見 [Preset](references/presets.md)。

先確認使用者要的起點：

- `new_session`：載入自己的同版、25fps、零 Snapshot 範本（公開版不附 Session；先依 operations reference 設定），再另存新檔；保留範本的結構與 routing，**不是 factory reset**。
- 編輯目前 Session：以 `inspect` 的實際 count 與目前路徑為起點。
- 使用其他範本：先單獨 `load` 明確指定的同版 `.ses`，再重新 inspect／plan。`new_session` 不接受任意範本參數。

## 工作流程

若使用者只要離線規劃，可直接用其提供的 count 寫 request 並 `plan`，不需要 Windows／VM 或 inspect；註明 count 來自使用者、`runtime_acceptance_verified=false`。實際送出前才完成下列環境與回讀步驟。

1. 準備實際執行時，讀 [Windows 執行環境](references/windows-host.md)，確認既有編輯器、明確 EXE 路徑與結果輸出位置。不可沿用舊 PID 或把範例路徑當成目前環境。
2. **inspect**：送出前讀實際 FPS、Snapshot count、名稱 raw bytes、RecallAt、啟用旗標與 Session 路徑；此模式不連原生 TCP。
3. 依 [request schema](references/operations.md) 寫原始 request。實際執行的 `expected_count` 填剛讀到的 count；離線規劃可使用上述 user-reported count。先 `plan` 完整驗證所有 index、名稱 bytes、timecode 與檔案路徑。
4. **execute --send**：把同一份**原始 request**交給工具；不要把 `plan` 輸出的 `steps` 文件當作 request。每一步都必須通過實際回讀，才繼續下一步。
5. 讀這一次的**全新 result**，確認 `status="ok"`、`verified_steps` 與 `after`。儲存需有新／更新且穩定的檔案、hash、目前路徑；需要確認磁碟持久化時，再以獨立 request 載入輸出檔並比對回讀。見 [驗證與失敗處理](references/validation.md)。

Mac／Parallels 範例；路徑與 VM 名稱只是範例，先替換成已確認值：

```sh
SKILL="$HOME/.codex/skills/digico-native-session"
WORK="$HOME/Documents/DiGiCoWork"
python3 "$SKILL/scripts/native_session.py" inspect \
  --vm 'Windows 11' --exe 'C:\DiGiCo\Quantum1\Quantum1.exe' \
  --encoding cp950 --result "$WORK/inspect-001.json"
python3 "$SKILL/scripts/native_session.py" plan \
  --request "$WORK/request.json" --output "$WORK/plan.json"
python3 "$SKILL/scripts/native_session.py" execute \
  --request "$WORK/request.json" --send \
  --vm 'Windows 11' --exe 'C:\DiGiCo\Quantum1\Quantum1.exe' \
  --result "$WORK/execute-001.json"
```

每個 `--result` 必須是絕對路徑，且該檔與其 `.request.json` 均尚不存在；不要重用舊結果。Mac請用home內的可見一般資料夾（例如`~/Documents`），任何dot-prefixed路徑component或檔名都會被拒絕。Skill可留在隱藏`.codex`目錄，runner/assets會先複製到result旁的唯一可見stage，詳見Windows reference。`execute` 省略 `--send` 只會輸出 dry-run JSON，不連 Windows／VM／網路，也不產生 runtime result 檔，但 CLI 仍要求填寫 target／result 參數。

## 名稱與時間

- Snapshot 名稱上限是 **31 個編碼後 bytes**，不可默默截斷、替換或音譯。`ascii`、`cp950`、`utf-8` 都採嚴格編碼；中文 CP950 raw bytes 已通過原生儲存／重載，但 Windows ACP1252 下的中文 GUI 顯示尚未驗證。保留 `name_hex` 作為資料比對依據。
- Timecode 用 `HH:MM:SS:FF`、25fps，`FF<25`；保留使用者順序，不自行依全域時間重新排序。內部 RecallAt 是 **quarter frames**，不是毫秒或 packed HHMMSSFF。
- `create` 從目前 console state 建立 Snapshot；只有名稱與 RecallAt 會按 request 改動，不會自動為每段歌曲產生不同 routing、音量、BPM 或調性設定。

## 不確定結果

若連線中斷、逾時、`status="error"` 或出現 `indeterminate_operation`，先用新的 `inspect` 回讀，再規劃剩餘工作。**不可盲目重送整批 create／delete**；它們會改變 count 與後續 index。已驗證操作邊界上的重連不等於可以重播結果不明的操作。若有 `recovery_stage_path`，保留該檔：目前Session可能仍指向alias／暫存範本，先確認實際路徑再恢復。

需要查核封包、位址或改版適用性時才讀 [確切協定與位址](references/protocol.md)。其他 EXE hash、FPS、實體控台與非 loopback 主機不屬於本技能已驗證能力，不能透過放寬 hash／改寫記憶體來冒充支援。

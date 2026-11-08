# Channel processing Preset

這裡管理處理庫 Preset，與 Snapshot／Cue 不同。只適用 exact Quantum1 V2126／25fps、本機 Windows 離線編輯器及 loopback 原生 TCP。名稱、物件、處理值與 global recall scope 都以唯讀程序記憶體驗證；不操作 UI、不使用 OSC、不寫程序記憶體。

## 先讀清單

```text
native_session.py presets --encoding ascii --vm VM --exe WINDOWS_EXE --result ABSOLUTE_NEW_RESULT
native_session.py presets --encoding cp950 --content-index INDEX --vm VM --exe WINDOWS_EXE --result ABSOLUTE_NEW_RESULT
```

Windows 本機依 [Windows 環境](windows-host.md) 使用 `--vm local`。`--content-index` 可重複，最多64項；index 必須存在。預設只讀 metadata，帶 index 才複製該 Preset 的 channel/module/controller contents。此查詢不建立 TCP 連線。

Result 的 `presets` 包含 `preset_count`、`scope_hex`、有序 `presets[]`、metadata SHA、目前 Session path bytes。每個 entry 有 index/pointer、name/group/notes 的文字與 raw hex、lock flag、儲存時間 raw bytes、channel count/mix flag。文字解碼失敗時保留 raw hex，text 可為 null。`content` 包含 stored channel IDs、module scope 與 controller raw values。完整資料只保留在本次本機 result，MCP 回傳摘要與檔案位置。

## 規劃與執行

每個 request 只放一個高階 `preset_*` 操作。Capture 可產生 Capture＋Rename；Recall 可產生 Scope＋Recall＋Restore。其他 Session、Cue、save、control write 要分開請求，完成後讀 fresh inventory 再規劃。

```text
native_session.py plan --request REQUEST --presets PRESETS_RESULT [--inventory CONTROL_INVENTORY] --output PLAN
native_session.py execute --request REQUEST --presets PRESETS_RESULT [--inventory CONTROL_INVENTORY] --send --vm VM --exe WINDOWS_EXE --result ABSOLUTE_NEW_RESULT
```

`--presets` 使用本次成功查詢的 result，或成功 Preset mutation result 的 `presets_after`。`--inventory` 仍是另一份 fresh channel/controller inventory，沒有被 `--presets` 取代。送原始 REQUEST 給 execute，不能送編譯後 PLAN。省略 `--send` 是離線 dry-run。

頂層仍是 `schema_version=1`、`fps=25`、`encoding=ascii|cp950`、`expected_count`、`operations`。**expected_count 是目前 Cue 數，不是 Preset 數**；Preset count 與原 scope 從 `--presets` 綁定。所有 index 為0-based。

| op | 必填欄位 | 選填欄位 |
|---|---|---|
| `preset_capture` | channel_id、group_name | name、force_mono boolean、verify_control_ids |
| `preset_rename` | index、group_name、name | — |
| `preset_update` | index、group_name、channel_id | verify_control_ids |
| `preset_recall` | index、group_name、channel_id、scopes、verify_control_ids | — |
| `preset_set_scope` | scopes 或 scope_hex 二選一 | — |
| `preset_lock` | index、group_name、locked boolean | — |
| `preset_delete` | index、group_name | — |

Capture 限 ordinary input channel ID `0x100..0x1FF`，並要求新的非空 unique group。原生 Capture 字串是 group 名，新 Preset 初始 name 來自 channel title；提供 name 時再 Rename。其他 target 操作必須明確 group_name，且 raw bytes 與清單相符。Update/Recall 僅支援 single ordinary-input、非 MixPreset 的內容。

Name/group 限31個編碼後 bytes，嚴格 ASCII/CP950，不截斷、不替換、不接受 NUL。CP950 raw bytes 可用於比對，不代表 ACP1252 Windows GUI 能正確顯示中文。原廠 `LockFlag=2` 不可修改；Update/Delete 要 unlocked。Delete 還要求全 SnapshotList 沒有對此 Preset 的引用；不會為了刪 Preset 而默默清掉 Cue 依賴。

虛構 Capture request：

```json
{
  "schema_version": 1,
  "fps": 25,
  "encoding": "ascii",
  "expected_count": 0,
  "operations": [
    {"op": "preset_capture", "channel_id": 256, "group_name": "Lab-Unique", "name": "Lab Input"}
  ]
}
```

## Processing 驗證

Capture/Update 可加 `verify_control_ids`，1..64個明確5-byte native control ID hex。這時必須提供 `--inventory`，並在送前用 RPM 重新核對該 input 的實際 controller/owner pointer、definition、VMT/callback、current raw value。成功後同時驗證 stored subset 和 live value/output。沒有此欄位時，只能稱 metadata/content 結構驗證。

Recall **必須**有 `verify_control_ids`、fresh target `--inventory`，以及帶所選 index content 的 `--presets`。工具把 stored source channel 的 ID 映射至指定 target，用所選 scope 內已存的 raw 值作期望，再讀 actual live value/output。缺少內容、不認識的 control、非 numeric、重複或在 scope 外都拒絕，不自行猜值。這是明確 subset 驗證，不是整個 channel 全部參數或實體音訊結果的驗證。

可用 scopes 與原生 module bits：

| scope | bits |
|---|---|
| input | 1, 9, 11 |
| input_gain | 17 |
| input_route | 128 |
| eq | 4, 7 |
| dynamics | 5 |
| aux | 2, 6 |
| group | 3, 12, 13 |
| tidal_preamp | 26 |
| tidal_eq | 27, 28 |
| tidal_comp | 29 |
| tidal_gate | 30 |

沒有已證實的 Insert checkbox mapping，所以不接受 `insert`。Recall 要明確非空 scopes，執行順序為套用此 scope → Recall → 還原請求起始的完整32-byte global scope。

`preset_set_scope` 是**真正保留**的 global setter，成功後 scope 不會自動還原。可給上述 scopes，或精確32-byte `scope_hex`。若需還原，先從最初 result 保存 scope_hex，再用 fresh `--presets` 編譯一個明確還原 request；不可猜原值全0。

## 結果、保護與失敗

Mutation result 有 `presets_before/presets_after`、`verified_steps`、Cue 的 `before/after`、`original_scope_restored`、`original_preset_contents_verified`。最後旗標代表非 target 原始內容皆保持一致；Update/Delete 的明確 target 允許改變，其餘操作連 target stored content 也保護。`verified_steps` 分別回報 `metadata_verified/stored_controller_subset_verified/processing_verified`，不能混為一項。

發送前會拒絕不符的 executable/process、offline marker、runtime FPS、Cue count、外部 peer、busy save worker，以及 stale Preset list/metadata/scope/path 或 controller value。每一步後確認 Cue fields 不變、Preset count/metadata exact diff 與原始 stored content fingerprints。原 scope 對 Recall 會還原，對 `preset_set_scope` 則按要求保留。

遇到 timeout、連線錯誤或 `indeterminate_operation`，先用 fresh `presets`、`inspect` 和必要的 controller inventory 讀現況。**不要盲目重播 Capture/Delete 或整個 Recall plan**。部分 Recall 可能已套 scope、或已改部分值；失敗處不會自動送補償封包。讀 result.presets_after.scope_hex，再明確決定是否還原。

## Session 保存／載入是合併語意

原生 Session Load **不會把整個全域 Preset library 回滾成檔案版本**。本版實測：先保存 Session，再把目前庫中一項 Preset 改名但不保存；重新載入保存檔後，未保存的項目仍保留，檔案內版本被匯入為另一項，其 stored processing content fingerprint 與保存前相符。因此載入不能當作清除 lab 項目或還原全域庫的方法，也不能只看舊 pointer／index 還在就宣稱已從磁碟恢復。

保存／載入後讀 fresh `presets`，依明確 group/name 與實際內容辨識匯入項目，重新取得 index/pointer，必要時再查 content 比對。不能假定每次 Load 都新增一項、固定 index、或已完整知道 merge 的判同條件。要清理本次建立的項目，逐項重新綁定、確認沒有 Cue 依賴後，明確 Delete。

獨立 `.LIB` 檔案來源／自動寫回條件尚未建立，result 的 library_file_path 為 null；不能假稱已備份。已驗證的 Session 內容持久化不等於整個外部 library 格式、所有 Preset 類型或實體控台都已支援。

本機 exact offline editor 已實測唯讀 inventory、Capture/Rename/Update、Lock/Unlock、global Scope 設定與明確還原、Recall 後 EQ/mute subset 的 live value/output 與原 scope 恢復，以及原生 Session 保存／重新載入的內容持久化與 merge 行為；MCP 亦已走相同 runner 驗證 Scope 還原。每步均核對原始非 target Preset contents 與 Cue invariants。Delete 與最終清理狀態仍須以新 runtime result 確認；pure compiler/synthetic tests 不代替實際接受，音訊硬體未測。

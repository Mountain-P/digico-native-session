# Request schema 與操作

主入口為 `scripts/native_session.py`。輸入是 UTF-8 JSON，可含 BOM；重複 key、未知欄位與錯誤型別會被拒絕。

操作依 [build profile](build-profiles.md) 綁定 exact EXE。原有 Quantum1 V2126 保留既有功能；六個新增 profile 支援本頁的 25fps Snapshot、SaveAs／Save／alias Reload，以及本機零 Snapshot template 的 Load→SaveAs，每一步均以原生狀態回讀。新增版的非 Cue 寫入限各 profile 的 numeric／boolean／Input name exact allowlist，詳見 [非 Cue 功能](noncue.md)。

## Request

```json
{
  "schema_version": 1,
  "fps": 25,
  "encoding": "ascii",
  "expected_count": 0,
  "operations": [
    {"op": "create", "index": "append", "name": "Cue A", "timecode": "00:01:00:00"},
    {"op": "create", "index": "append", "name": "Cue B", "timecode": "00:02:00:00"},
    {"op": "save_as", "path": "C:\\DiGiCoWork\\CueDemo_01.ses"}
  ]
}
```

所有頂層欄位必填：

| 欄位 | 契約 |
|---|---|
| `schema_version` | 整數1 |
| `fps` | 整數25 |
| `encoding` | `ascii`、`cp950` 或 `utf-8`，整批共用 |
| `expected_count` | 執行前實際 Snapshot count，整數0..2000；純離線規劃可用自己提供的值 |
| `operations` | 非空陣列，最多1000項，依順序模擬 index 變化 |

另可加 `build_profile`，值為 [profile ID](build-profiles.md)。例如新增 Quantum1 V2242.4 使用 `"build_profile":"quantum1-v2242-4"`。`execute --build-profile ID` 可為省略該欄位的 raw request 選擇 build；明確的 request profile 或 inventory／Preset identity 衝突時，dry-run 與 send 均拒絕。`plan` 只使用 request／artifact identity，皆無時預設 `quantum1-v2126`。Reader 省略 profile 時則由實際 EXE hash 選擇已收錄的 build。

若先用 `plan` 檢視新版操作，請在 request 明列同一 `build_profile`，使預覽與稍後 `execute` 的 build 選擇一致；不能把舊 Q1 的預覽當作新版授權。

2000 Snapshot／1000操作是執行器的本機資源界線，不是控台規格。

## 操作

| `op` | 必填欄位 | 行為 |
|---|---|---|
| `create` | `index`, `name` | index 可為 `"append"` 或0..count；可另帶 `timecode`。從目前 console state 建立，再命名與設定 RecallAt |
| `delete` | `index` | 刪除現有的零起始索引，後續 index 位移 |
| `rename` | `index`, `name` | 改名稱 |
| `timecode` | `index`, `timecode` | 設定並啟用 RecallAt；沒有關閉旗標參數 |
| `save_as` | `path` | 可帶布林 `overwrite`，預設false；true時先建立唯一備份 |
| `save` | 無其他欄位 | 儲存目前已有有效本機 `.ses` 路徑的 Session |
| `load` | `path` | 單獨執行，替換目前 Session，載入後再 inspect |
| `new_session` | `path` | 單獨執行，從本機零 Snapshot 範本另存新檔，禁止覆寫 |

Snapshot index 是零起始 list index，不是畫面顯示號碼或物件ID。`delete`／`rename`／`timecode` 必須指向現有 Snapshot，執行器拒絕操作 lock bit `0x2000` 的目標。每次插入或刪除都要按變動後的位置規劃下一步。

`save`／`save_as` 必須是整批最後一項。`load`／`new_session` 必須是唯一操作；完成後取得新狀態，另開 request 編輯。它們會替換目前未存狀態。

## 名稱、時間與路徑

- 名稱非空，嚴格編碼後1..31bytes，不含NUL，不自動縮短或替換。舊 Q1 V2126 的 CP950 保存／重載 raw bytes 已驗證，不推定新版中文顯示或持久化皆已測；`utf-8`只是輸出bytes的選項，不保證編輯器GUI按UTF-8解讀。
- 時間嚴格為 `HH:MM:SS:FF`，HH0..23、MM／SS0..59、FF0..24；不支援drop-frame、分數秒或任意quarter-frame餘數。
- `path` 是ASCII Windows本機磁碟絕對路徑、`.ses`、最多255bytes。不可用UNC、相對路徑、dot／parent路徑片段、萬用字元或Windows保留裝置名稱。父資料夾必須存在。
- `save_as` 預設拒絕既有檔案；`new_session` 一律拒絕既有目標。

## 本機空範本

公開版沒有原廠 binary 或 `.ses`。用對應 profile 的編輯器準備自己的 25fps Session，確認它包含想保留的 structure／routing 與零 Snapshot。若目前 count0，可先透過原生 `save_as` 儲存並獨立 `load` 驗證，再於 skill 資料夾中匯入。新增 profile 必須明確傳入 `--build-profile`：

```sh
python3 scripts/configure_template.py \
  --build-profile quantum1-v2242-4 \
  --source /absolute/path/to/your-empty-session.ses
```

`--skill-root` 可選，預設為腳本所在 skill root。每個 profile 使用自己唯一的 template／manifest 檔名，以 profile 的 `session_file.template_filename`／`manifest_filename` 為準；已有任一檔案就拒絕覆寫。Quantum1 V2126 保留 `assets/quantum1-v2126-empty.ses` 與 `assets/empty-template.json`。檔案都保留在本機，不加入公開 repository，不跨 profile 共用 manifest。

匯入只檢查所選 model／版本檔頭與 SHA-256；不啟動編輯器、不讀程序記憶體。Manifest 中的 `snapshot_count=0` 是期待條件，`snapshot_count_verified=false` 明示未由匯入工具證明。`new_session` 仍需原生載入唯一暫存副本、讀回 count0 且25fps，才另存指定路徑。載入前的 manifest 宣告不等同 runtime 驗證。

`new_session` 不接受 `template` 或 `overwrite` 欄位。它保留範本的structure／routing／current mixer state，並非factory reset。若範本不是零Snapshot，操作必須回報失敗，不能偷偷刪掉其內容。

若要從目前有Cue的Session準備「保留目前console但清空Snapshot」的範本，這是實際刪除操作：保留來源檔後，依目前count逐步刪除並回讀，遇到locked cue停止，count0再另存。不要把這個流程稱為重建factory設定。

## CLI

```text
native_session.py plan --request REQUEST [--output PLAN]
native_session.py profile [--build-profile ID]
native_session.py inspect --vm VM --exe WINDOWS_EXE --result ABSOLUTE_NEW_RESULT [--build-profile ID] [--process-id PID] [--encoding ascii|cp950|utf-8]
native_session.py execute --request REQUEST --vm VM --exe WINDOWS_EXE --result ABSOLUTE_NEW_RESULT [--build-profile ID] [--process-id PID] [--inventory INVENTORY] [--send]
native_session.py author --request AUTHOR_REQUEST --inventory INVENTORY [--output PLAN]
native_session.py author-execute --request AUTHOR_REQUEST --inventory INVENTORY --vm VM --exe WINDOWS_EXE --result ABSOLUTE_NEW_RESULT [--build-profile ID] [--process-id PID] [--send]
native_session.py recover --journal JOURNAL [--compiled-request COMPILED_REQUEST] [--output REPORT]
```

`plan` 輸出 `steps`、計算後index、quarter-frame值與封包hex；它不接觸VM／程序／網路。執行時仍傳原始request。

`profile` 在本機輸出所選 build 的 identity、validation 與 feature gates，不接觸 target。這不是版本自動升級或相容性推測。

`inspect` 不建立原生TCP連線。預設ASCII解碼遇到不可解碼bytes時，`name`可為null，但`name_hex`仍保留；已知CP950資料可指定 `--encoding cp950`。

`execute` 不帶 `--send` 是dry-run，只輸出JSON，不產生runtime result，也不呼叫Windows runner；target／result參數仍為CLI必填。

實際執行時，`--result`、`.request.json` 及 `.journal.jsonl` 都要是尚不存在的全新絕對路徑。結果帶本次 compiled request 的 hash，CLI 核對 `request_sha256`。另有 `.tx.bin`／`.rx.bin`；可能包含自己的 Session 資料，不應公開。

主要結果：`status`、`before`、`after`、`verified_steps`、`error`、`tx_bytes`、`rx_bytes`。部分失敗另有 `indeterminate_operation`／`recovery`，需要保留暫存檔時有 `recovery_stage_path`。`after.snapshots`包含 `index`、`name_hex`、`name`、`time_quarterframes`、`recall_at_active`與`flags`。儲存結果另有目的路徑、hash、大小與可能的備份路徑。

## 語意 authoring

Authoring 使用獨立 request schema；profile 從成功的 inventory 綁定，這個 schema 不加 `build_profile`。以下是合成範例，通道與參數仍需在本次 inventory 中唯一匹配並通過所選 profile 的寫入契約：

```json
{
  "schema_version": 1,
  "kind": "authoring",
  "fps": 25,
  "encoding": "ascii",
  "expected_count": 0,
  "operations": [
    {"op": "set", "channel": {"type": 1, "logical": 1}, "parameter": "fader", "value": {"value": 0.5, "unit": "native"}},
    {"op": "rename", "channel": {"type": 1, "logical": 1}, "name": "Input A"}
  ]
}
```

Channel selector 可用單獨 `id`，或 `logical`／精確 `name` 加可選 `type`。Parameter 可用已驗證 alias、`native:<精確 definition name>` 或 `id:<5byte hex>`；多個匹配必須補 `band` 或 exact ID。`value` 與可選 `expected` 使用 `{value,unit}`，unit 為 `native`、`Hz`、`dB`、`ms` 或 `s`；只有 catalog 中已證實的換算會通過，顯示單位本身不授權換算。

`author` 輸出 diff 與 stages；mode／shape 變更獨立成 stage，只有第一階段有可執行的 `raw_request`，後續明示 `requires_fresh_inventory`。預覽的 `runtime_acceptance_verified=false`，不是執行成功收據。

`author-execute` 預設 dry-run。加 `--send` 後，在同一 host lease 下依序執行、更新相關通道 inventory 並重編譯後續階段，核對 build／程序／Session identity。結果保留 `work_dir`、artifacts 與各 stage 收據；不是原子交易，出錯不回滾、不自動重送。

## 中斷與恢復

Runner 將 request identity、step started／verified 與 completion 寫入附 hash chain 的 `.journal.jsonl`，逐筆要求 flush；VM 共享檔案系統的持久性仍取決於環境。Hash chain 是完整性檢查，不是身分認證，也不授權 replay。

```sh
python3 scripts/native_session.py recover \
  --journal /absolute/work/result-001.json.journal.jsonl \
  --compiled-request /absolute/work/result-001.json.request.json
```

`recover` 只在本機驗證已完整寫出的紀錄與可選 compiled request，忽略未完成尾行，列出 verified／indeterminate operation indices；不呼叫編輯器、不自動產生或重送 retry。先取得新 inspect／inventory，再對照 request 與既有收據規劃剩餘工作。互斥 lease 的範圍見 [Windows 與 Parallels](windows-host.md#本機工具互斥)。

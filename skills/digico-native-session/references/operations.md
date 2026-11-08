# Request schema 與操作

主入口為 `scripts/native_session.py`。輸入是 UTF-8 JSON，可含 BOM；重複 key、未知欄位與錯誤型別會被拒絕。

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

- 名稱非空，嚴格編碼後1..31bytes，不含NUL，不自動縮短或替換。CP950保存／重載raw bytes已驗證；`utf-8`只是輸出bytes的選項，不保證編輯器GUI按UTF-8解讀。
- 時間嚴格為 `HH:MM:SS:FF`，HH0..23、MM／SS0..59、FF0..24；不支援drop-frame、分數秒或任意quarter-frame餘數。
- `path` 是ASCII Windows本機磁碟絕對路徑、`.ses`、最多255bytes。不可用UNC、相對路徑、dot／parent路徑片段、萬用字元或Windows保留裝置名稱。父資料夾必須存在。
- `save_as` 預設拒絕既有檔案；`new_session` 一律拒絕既有目標。

## 本機空範本

公開版沒有 `.ses`。準備自己的Quantum1 V2126／25fps Session，確認它包含想保留的structure／routing與零Snapshot。若目前count0，可先透過原生 `save_as` 儲存並獨立 `load` 驗證，再於skill資料夾中匯入：

```sh
python3 scripts/configure_template.py --source /absolute/path/to/your-empty-session.ses
```

`--skill-root` 可選，預設為腳本所在skillroot。腳本固定產生本機 `assets/quantum1-v2126-empty.ses` 與 `assets/empty-template.json`；已有任一檔案就拒絕覆寫。兩者都必須保留在本機，不加入公開repository。

匯入只檢查Q1檔頭、版本與SHA-256；不啟動編輯器、不讀程序記憶體。manifest中的 `snapshot_count=0` 是期待條件，`snapshot_count_verified=false` 明示未由匯入工具證明。`new_session` 仍需原生載入唯一暫存副本、讀回count0且25fps，才另存指定路徑。載入前的manifest宣告不等同runtime驗證。

`new_session` 不接受 `template` 或 `overwrite` 欄位。它保留範本的structure／routing／current mixer state，並非factory reset。若範本不是零Snapshot，操作必須回報失敗，不能偷偷刪掉其內容。

若要從目前有Cue的Session準備「保留目前console但清空Snapshot」的範本，這是實際刪除操作：保留來源檔後，依目前count逐步刪除並回讀，遇到locked cue停止，count0再另存。不要把這個流程稱為重建factory設定。

## CLI

```text
native_session.py plan --request REQUEST [--output PLAN]
native_session.py inspect --vm VM --exe WINDOWS_EXE --result ABSOLUTE_NEW_RESULT [--process-id PID] [--encoding ascii|cp950|utf-8]
native_session.py execute --request REQUEST --vm VM --exe WINDOWS_EXE --result ABSOLUTE_NEW_RESULT [--process-id PID] [--send]
```

`plan` 輸出 `steps`、計算後index、quarter-frame值與封包hex；它不接觸VM／程序／網路。執行時仍傳原始request。

`inspect` 不建立原生TCP連線。預設ASCII解碼遇到不可解碼bytes時，`name`可為null，但`name_hex`仍保留；已知CP950資料可指定 `--encoding cp950`。

`execute` 不帶 `--send` 是dry-run，只輸出JSON，不產生runtime result，也不呼叫Windows runner；target／result參數仍為CLI必填。

實際執行時，`--result`及其`.request.json`都要是尚不存在的全新絕對路徑。結果帶本次compiled request的hash，CLI核對 `request_sha256`。另有`.tx.bin`／`.rx.bin`；可能包含自己的Session資料，不應公開。

主要結果：`status`、`before`、`after`、`verified_steps`、`error`、`tx_bytes`、`rx_bytes`。部分失敗另有 `indeterminate_operation`／`recovery`，需要保留暫存檔時有 `recovery_stage_path`。`after.snapshots`包含 `index`、`name_hex`、`name`、`time_quarterframes`、`recall_at_active`與`flags`。儲存結果另有目的路徑、hash、大小與可能的備份路徑。

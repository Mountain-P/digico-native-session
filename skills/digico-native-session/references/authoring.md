# 語意編輯與差異預覽

`scripts/authoring.py` 是無 I/O 的資料層：用已取得的 inventory 解析通道與參數，驗證明確單位，產生現有 raw request，以及 current／intended 差異。模組不啟動 VM、不接觸網路、不操作 UI，也不寫程序記憶體。可使用 CLI 的 `author` 命令讀取本機 JSON；執行仍須經現有 native compiler、target binding 與逐步回讀。

## CLI預覽與執行

本機預覽只讀 request與inventory JSON，可指定輸出plan，無需Windows參數：

```sh
python3 skills/digico-native-session/scripts/native_session.py author \
  --request /absolute/work/authoring.json \
  --inventory /absolute/work/inventory.json \
  --output /absolute/work/authoring-preview.json
```

`author-execute` 預設仍是dry-run；CLI要求target與result參數，但省略 `--send` 不呼叫runner，也不建立result、intent或工作目錄：

```sh
python3 skills/digico-native-session/scripts/native_session.py author-execute \
  --request /absolute/work/authoring.json \
  --inventory /absolute/work/inventory.json \
  --vm 'Your Windows VM' --exe 'C:\DiGiCo\Quantum1\Quantum1.exe' \
  --process-id 1234 --result /absolute/work/authoring-run-001.json
```

確認完整階段預覽後，以同樣參數加 `--send` 才執行。VM、EXE、PID及路徑都是範例，必須改成實際target。`--build-profile` 可要求特定已驗證build；dry-run與send都會拒絕和inventory不符的profile。Windows本機使用同一runner契約，環境參數見 [Windows與Parallels](windows-host.md)。

送出時 `--result` 必須是全新絕對路徑，其 `.request.json` 也須不存在。CLI先以exclusive方式發布 `<result>.request.json` 工作意圖，內含原始authoring request及新建的 `author-<unique-id>` 工作目錄；既有intent、result或同名工作目錄不會被重用。整個workflow持有同一target lock。

每一個barrier會依下一階段所需的raw channel IDs重新取得含response tables的inventory，嚴格合併同一profile、EXE hash、PID、image base、console pointer、Session path、FPS與Snapshot invariants，再重編已釘ID的意圖。不能只重用上一階段的value／mode。最後若只有一項mode操作，完成後仍做一次fresh inventory檢查，不重送該操作。

結果內 `stages` 保留每階段raw request、inventory binding及native execution result；`refreshes` 保留刷新與來源hash。`artifacts` 指向工作目錄中的 `stage-000.json`、`refresh-001-0100.json` 等實際runner路徑。其 `.request.json` 是compiled native request，其 `.journal.jsonl` 是逐步收據；頂層workflow intent與compiled native request不能直接當作authoring request重送。已登記的attempted artifact路徑可能只有request／journal而沒有完整result，表示該嘗試的證據需一併檢查。

完整成功回 `status:"ok"` 與exit0；錯誤、結果不明、回讀不完整或refresh失敗會停止，回exit2與可審閱的部分收據，`auto_replay:false`。已完成階段不會因後續刷新失敗而重送。`remaining_authoring_requests` 是剩餘意圖的審閱資料；先重新inspect／inventory並核對已驗證操作，再另建新的執行request與result路徑。原生階段的journal可用既有 `recover --journal ...` 查核。

若target lock取得失敗，或最終result發布遇到檔案競態，頂層result可能尚未產生；已建立的intent、工作目錄、native request與journal仍會保留，不會覆寫其他writer的檔案或自動重跑。階段式操作不是原子交易，也沒有自動rollback。

## Request

```json
{
  "schema_version": 1,
  "kind": "authoring",
  "fps": 25,
  "encoding": "ascii",
  "expected_count": 0,
  "operations": [
    {
      "op": "set",
      "channel": {"type": 1, "logical": 1},
      "parameter": "fader",
      "value": {"value": -12, "unit": "native"}
    }
  ]
}
```

這裡的 `logical:1` 查 inventory 的 `logical_channel`，不是 raw ID。已觀察到的 Input1 raw ID 為256；不要把 logical1直接當raw1。可使用以下三種 selector：

- `{"id":256}`：完整原生 uint16 ID；不得再混用其他欄位。
- `{"logical":1,"type":1}`：logical number，加上原生通道 type；`type` 可省略，但重複時會拒絕。
- `{"name":"Synthetic Vocal","type":1}`：大小寫完全相同的已解碼名稱；`type` 可省略。重名會回報按 raw ID 排序的候選，不自動挑選第一項。

`set` 必須含 `channel`、`parameter`、`value`。`value` 與可選的 `expected` 都是 `{"value":number,"unit":"..."}`。`expected` 是 compare-before-write 的明確前提；省略時以 inventory 或同階段前項預期值為準。布林值不是 numeric enum，mute 的原生數值使用0或1。

`rename` 的格式是 `{"op":"rename","channel":{"id":256},"name":"Synthetic Vocal"}`。名稱採 `encoding` 嚴格編碼，要求1–31 bytes，不截斷、不替換字元。它會編譯成 `set_channel_name`。

所有欄位採嚴格 schema，未知欄位會拒絕。這一層只提供通道名稱與 numeric controls；Snapshot、routing、Preset及Session檔案操作仍使用既有獨立契約。

## 參數選擇

已證實的Q1 V2126語意名稱如下；實際控制仍須存在於當次inventory，並通過其native bounds、owner callback與mode契約。已admitted的 `quantum-layout-v2` exact profile若明列 `authoring_aliases:["fader"]`，只另外開放該build已證實的fader alias；不會順便開放mute／pan／EQ等其他alias。沒有此明列證據的build仍保留原生名稱／ID選擇。Alias也不授權單位換算或超越control write allowlist。

| 語意名稱 | 對應 |
|---|---|
| `fader`、`mute`、`pan`、`trim` | 已證實的基本混音控制 |
| `eq.frequency`、`eq.q`、`eq.gain`、`eq.in`、`eq.shape` | Classic EQ；多個band匹配時必須加 `band` |
| `lpf.frequency` | 已證實的 hi-filter frequency，原生band0；不是HPF |
| `delay.time`、`delay.fine` | main delay與fine samples；total delay為衍生值，不提供alias |
| `compressor.attack`、`compressor.release`、`compressor.mode` | 原生dynamics band0 |
| `gate.attack`、`gate.release`、`gate.mode` | 原生dynamics band3 |

`band` 是原生零起算band欄位，不能由顯示的band編號自行推導。對EQ等有重複項的參數，可加 `"band":0`；仍須以catalog確認。

完整選擇方式還有：

- `native:<exact definition name>`：完整、大小寫完全相同的 copied definition 名稱；重複時加band或改用ID。
- `id:<10 hex digits>`：明確5-byte control ID，仍要屬於所選通道。

HPF尚無專用alias。可依當次catalog的完整definition名稱選擇已存在、具可寫契約的控制；不要把已證實的LPF頻率當成HPF。`authoring_catalog(inventory)` 列出原生名稱、ID、band、alias、可寫候選、bounds及單位證據。

## 明確單位

接受 `native`、`Hz`、`dB`、`ms`、`s`；不猜測百分比、samples與頻率的比例，也不按顯示字串自動換算。

- `native`：沿用catalog的原生內部數值；每項仍需通過typed setter與bounds。
- `ms`／`s`：只在catalog的 `verified_conversion.native_unit` 為 `seconds` 時接受。`ms` 乘0.001後再按float32與原生bounds驗證。
- `Hz`／`dB`：只在可信的normalized catalog提供 `verified_identity_units`，其 `native_unit` 完全相同且有 `evidence` 時接受；display label本身不足以授權。
- `delay.fine`：原生值是samples。即使重用的definition帶S/mS字樣，也拒絕 `ms`／`s`；尚未實作runtime sample-rate換算。

單位轉換與bounds是不同檢查。EQ Q依當前shape，Comp/Gate attack/release依當前mode；顯示單位正確不表示切換模式後仍能使用舊範圍。

## 階段與差異

`compile_authoring_request(request, inventory)` 和 `preview_authoring_request(request, inventory)` 都是純函式，不修改傳入物件。回傳的 `inventory_binding` 含normalized inventory SHA-256與可用的Session路徑。

`stages` 保留使用者順序，任何EQ shape／Comp mode／Gate mode操作都獨立成階段。只有第一階段含可交給現有compiler的 `raw_request`，其中只有既有嚴格raw欄位。每個後續階段：

- `status:"requires_fresh_inventory"`、`barrier_before:"fresh_inventory_and_recompile"`。
- `raw_request:null`、`inventory_sha256:null`；不能把initial inventory當作未來模式的授權。
- `authoring_request` 將通道與控制釘成原生ID。完成前項、確認回讀後，以新inventory重新編譯該request。它不靠被rename的舊名稱重新選通道。

呼叫端仍須核對前階段的實際結果、Session identity與Snapshot count；新inventory本身不能證明上一步有成功。不得將所有階段攤平成一次raw request。若結果不明，先重新inspect／inventory，再規劃剩餘意圖。

`changes` 提供每項的通道、原生控制名稱、current與intended、`changed` 與 `validation`。Numeric項另含 `intended_native`，已編譯者含payload bytes。後續階段標記 `current_may_change_at_barrier:true`；其差異只是對copied inventory與前項意圖的預覽，bounds尚未在新mode驗證。`fully_compiled:false` 表示仍有階段需要refresh／recompile。`values_atomic` 與 `runtime_acceptance_verified` 永遠為false。

`AuthoringError` 具有穩定的 `code`、`message`、`details` 與 `as_dict()`。常見code包括 `unknown_channel`、`ambiguous_channel`、`unknown_parameter`、`ambiguous_parameter`、`unit_unverified`、`native_contract`；錯誤不會偷偷改用相近名稱。

## Python接入

```python
from authoring import compile_authoring_request
from native_session import compile_request

preview = compile_authoring_request(authoring_request, copied_inventory)
first_raw = preview['stages'][0]['raw_request']
native_plan = compile_request(first_raw, inventory=copied_inventory)
# 由既有執行端送出first_raw並確認新result。
# 後續stage須使用fresh_inventory重編stage['authoring_request']。
```

Repository的 `examples/authoring-controls.json` 全部使用合成意圖。預覽與單元測試不代表已在編輯器、DSP或實體控台接受。

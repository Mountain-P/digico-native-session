# 不啟動編輯器，直接讀寫 `.ses`

`native_session.py` 的 `file-new`、`file-inspect`、`file-clone`、`file-edit` 只需要本機 Python 3。這些命令與 `scripts/ses_file.py` 的 `new`、`inspect`、`clone`、`edit` 共用實作。

## 檔案格式與驗證範圍

支援 [build profiles](build-profiles.md) 所列七個 vN／vO model／release header，包括舊 Q1 V2126 與六個新版 Quantum profile。Header 只能識別檔案格式家族，不能證明建立檔案的 exact EXE；結果明列 `format_profile`、`executable_identity_verified:false` 及 `native_acceptance_verified:false`。

檔案工具辨識容器、ConsoleRec、C9 通道與 socket 結構、F6／FC port 資料、DB Preset、D2 Snapshot、D3 current state。編輯既有檔案時，未編輯的 section、延伸欄位與 padding 原樣保留。無修改 roundtrip 與來源逐 byte 相同。[已解格式](ses-format.md)

直接產生輸出不需要 editor。原廠是否接受輸出，須另以相符版本的 native Load 及獨立回讀驗證。六款新版已各自驗證合成檔的 CP950 Snapshot 名稱、RecallAt、複製／刪除／capture、Input 名稱及 Fader／Trim／Mute；原廠重存後 D2 與獨立輸出逐 byte 相同。舊 Q1 另驗證從空範本 capture 與基本混音。Q5 另測共享引用由1變2及由1變0，原廠重存仍保留正確 UserCount。這些代表案例不能推定任意內容都相容，完整範圍見 [能力矩陣](../../../CAPABILITIES.md)。

## 從設定建立新 Session

把 `schema_version: 1`、`build_profile`、`encoding`、`structure` 寫入 JSON，可附上 `operations`。`file-new` 依通道設定產生 header、C9、D3、local port、bank layout 與空的 Preset／Snapshot list，再依序執行操作。輸入只是一份 JSON。下例用 Q5 建立含 Input patch、stereo Master L/R、Preset 和 Snapshot 的場次。[完整合成 request](../../../examples/file-new-quantum5.json)

```sh
SKILL="$PWD/skills/digico-native-session"
python3 "$SKILL/scripts/native_session.py" file-new \
  --request examples/file-new-quantum5.json \
  --output /absolute/work/synthetic-new.ses
python3 "$SKILL/scripts/native_session.py" file-inspect \
  --source /absolute/work/synthetic-new.ses --topology --routing --presets
```

新建固定 48 kHz／25 fps。`structure.inputs`、`auxes`、`groups` 各接受 `{"mono": 8, "stereo": 2}` 形式的計數。第一個 stereo Group 是 Master，至少配置一個。`matrix_inputs`、`matrix_outputs`、`control_groups`、`graphic_eqs` 各接受非負整數。省略的項目使用已研究的同型號預設數量。下表上限依觀察到的組態設定，mono 計一 leg，stereo 計兩 leg。

| Profile | Input legs | Aux legs | Group legs | Matrix In／Out | Control Group | GEQ |
|---|---:|---:|---:|---:|---:|---:|
| `quantum1-v2242-4` | 48 | 9 | 9 | 12／8 | 12 | 12 |
| `quantum2-v2242-4` | 48 | 18 | 18 | 12／12 | 12 | 16 |
| `quantum3-v2242` | 48 | 18 | 18 | 24／24 | 24 | 24 |
| `quantum5-v2242` | 108 | 18 | 18 | 36／36 | 24 | 32 |
| `quantum7-v2242` | 72 | 18 | 18 | 32／32 | 24 | 48 |
| `quantum8-v2242` | 72 | 18 | 18 | 32／32 | 24 | 48 |

建立器以可核對的欄位和控制值生成本機 I/O 及初始混音。Q5 可在最外層加上 `"madi_port1": true`，建立固定版型的 MADI 1 Rack，含 56 進、56 出，port selector 為 `port_type: 2`、`port_number: 0`。新檔的實體路由由 `operations` 指派。舊 Q1 V2126 支援編輯既有檔案。任意 rack 卡組態、既有場次的任意通道數量遷移，以及新建檔的原廠載入與實際音訊仍待驗證。

## 先讀取或複製

以下路徑、名稱與 ID 皆為合成範例，改成自己的檔案與實際 channel ID：

```sh
SKILL="$PWD/skills/digico-native-session"
python3 "$SKILL/scripts/ses_file.py" inspect \
  --source /absolute/work/synthetic-source.ses --encoding cp950 --channel-id 0x0100

python3 "$SKILL/scripts/native_session.py" file-clone \
  --source /absolute/work/synthetic-source.ses \
  --output /absolute/work/synthetic-copy.ses
```

`inspect` 輸出來源 SHA-256、格式、結構 raw／已辨識欄位、section 摘要及可用的 Snapshot／current-mix 資料。`--channel-id` 只縮小 current-mix 的檢視，使用 raw uint16 ID，例如 Input1 常為256／`0x0100`。`--encoding` 決定報告中的文字解碼；不能嚴格解碼時保留 raw hex。

`--include-raw-controls` 可另列所選通道的原始 controller ID／flags／value bytes，不推測未知資料型別或單位；輸出有本機筆數上限，資料較多時使用 `--channel-id`。

加入 `--snapshot-index N` 可查看該 Snapshot 保存的混音，並解析共用 module pool；例如 `file-inspect --source /absolute/work/synthetic-edited.ses --snapshot-index 1 --channel-id 0x0100`。`selected_snapshot.stored_mix` 是保存的 controller records，可用來核對先改 current mix 再 create 的結果；它沒有執行 recall，不能證明所有 scope／繼承／保護條件下的有效召回結果。

`clone` 寫入逐 byte 相同的新 `.ses`；`file-inspect` 與 `file-clone` 也可換成 standalone 的 `inspect`／`clone`，參數相同。`--build-profile ID` 可要求特定相符檔案格式，不會替換 header 或跨版本轉檔。

## 編輯 request

先從 `inspect` 取得實際來源的 `sha256`。下例全0 hash 僅示範格式，必須替換；編碼與操作也須符合來源：

```json
{
  "schema_version": 1,
  "expected_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "encoding": "cp950",
  "fps": 25,
  "operations": [
    {"op": "set_channel_name", "channel_id": 256, "name": "Synthetic Input A"},
    {"op": "set_control", "id": "210c000001", "value": -12},
    {"op": "create", "index": "append", "name": "測試 A", "timecode": "00:00:10:00"}
  ]
}
```

必填 `schema_version:1`、64位小寫 `expected_sha256`、`encoding` 與 `operations`；選填 `build_profile` 必須與來源格式相符，選填 `fps` 只能為整數25。操作依序執行，最多1000項；空陣列可作無修改 roundtrip。來源 hash 不符、重複 JSON key、未知欄位／操作、錯誤型別或任一操作失敗，都拒絕整個輸出，來源維持原樣。

```sh
python3 "$SKILL/scripts/ses_file.py" edit \
  --source /absolute/work/synthetic-source.ses \
  --request /absolute/work/synthetic-edit.json \
  --output /absolute/work/synthetic-edited.ses --encoding cp950
```

主 CLI 使用相同參數，只將 `edit` 換成 `file-edit`。這是明確的新檔寫入命令，無需 native `--send`；輸出父資料夾須已存在。

Request 的 `encoding` 控制寫入名稱 bytes，CLI 的 `--encoding` 控制輸出報告解碼。Ascii／CP950／UTF-8 均嚴格編碼；名稱必須為1–31 encoded bytes、不含 NUL，不截斷、不替換或音譯。CP950 bytes 保存不代表任何 Windows ACP 下的 GUI 顯示都相同。

## 操作與順序

| `op` | 欄位與範圍 |
|---|---|
| `set_channel_name` | `channel_id`、`name`。支援唯一存在的 Input、Aux、Group、Matrix Output 的目前名稱。 |
| `set_control` | `id`、有限數值 `value`。band0 Fader／Mute 支援 Input、Aux、Group、Matrix Output，Trim 支援 Input。 |
| `rename` | `index`、`name`；改既有 Snapshot 名稱。 |
| `timecode` | `index`、`timecode`，可加布林 `active`，預設 true；時間為 `HH:MM:SS:FF`、25fps、FF0..24。 |
| `number` | `index`、正整數 `number`；設定 whole displayed Cue number，不能與其他 Cue 重複。 |
| `create` | `index` 為 `"append"` 或0..count；從**同一檔案的 D3 current capture**建立，可加 `number`、`name`、`timecode`、`active`。 |
| `duplicate` | `source_index`、插入位置 `index`；複製該步當下的 Snapshot，可加同樣的編號／名稱／時間欄位。 |
| `delete` | `index`；刪除指定 Snapshot，locked 目標拒絕。 |

Snapshot index 為零起算，插入／刪除後後續 index 會改變，與 displayed Cue number 不同；`active` 只可與 `timecode` 一起提供。Rename／timecode／number／delete 拒絕 locked 目標。先修改 current mix，再 `create`，新 Snapshot 才會包含這些變更；工具不從名稱猜測每個 Cue 的混音，也不從目前正在執行的 editor 取值。

Inspect 分別回報 `number_raw` 與 displayed `number`，原生保存值為 displayed number ×100。`number` 操作目前只接受正整數；新 create／duplicate 預設使用既有最大 displayed number 向下取整再加1，保留既有編號，也可明確指定未使用的整數。這是檔案 authoring 策略，未重現原廠 midpoint 編號或 MIDI control-number 配置。

Create／duplicate／delete 另需 D3 current capture、DB Preset，以及 vO 的73 SetList section 齊備並可嚴格解碼：current／Preset module 的 original-Snapshot references 必須全為-1，SetList 必須為空，缺少、模糊或不明 scope 拒絕。這版保留這些外部 section 原 bytes，不猜非空引用的重排方式。能 inspect 或無修改 clone，不表示所有進階操作都適用；rename／timecode／number 與基本 current-mix 修改不改 Snapshot index。

| Input control prefix | `value` 的原生範圍 |
|---|---|
| `210c00` fader | -150..10，原生 dB float32。 |
| `010100` trim | 舊 Q1、新 Q1／Q8 為 -40..60；其他新版為 -20..60，原生 dB float32。 |
| `220c00` mute | 數值0或1，保存為原生 float32；不接受 JSON boolean。 |

完整 ID 是 prefix 加 channel ID 的 little-endian bytes，例如 Input `0x0100` 的 fader 為 `210c000001`。Control／channel 必須唯一存在，owned inline module、module type、flags 與 parent scope 也要符合已解契約；共享、繼承或不明控制保持唯讀。檔案沒有 live definition／VMT／response-pointer 證據，這些範圍來自逐 profile 的已審閱原生 seed，不能當成任意參數 setter。Request 不接受任意 offset／raw patch 或自訂 bounds。

## Routing 與 Preset

先用 `file-inspect --routing --topology` 取得 raw channel ID 和端點。端點可用實體 socket 的數值 ID，或有 F6 port 資料時用 `{"port_type": 1, "port_number": 0, "socket_index": 0}`。`socket_index` 從零起算。既有外部 port 依檔案中可核對的 C9、D3、F6／FC 資料選擇。

| `op` | 欄位與範圍 |
|---|---|
| `set_input_route` | `channel_id`、`source`，選填 `route_kind`。Mono Input 支援 `main`、`alt`、`key`、`mustard_key`，已有 controller 時可用 `insert_return` 搭配 `insert_index` 0／1。Stereo Input 的 `main`／`alt` 指定已核對的相鄰接點之第一個。`source: null` 解除。 |
| `set_output_route` | `channel_id`、`destination`、`connected`。支援 mono Input／Aux／Group；stereo Aux／Group 另指定 `leg: "left"` 或 `"right"`。已被其他訊號占用的端點會拒絕連接。 |
| `set_group_assignment` | `channel_id`、`group_channel_id`、`enabled`。支援已存有相應 assignment controller 的 mono Input。 |
| `set_aux_send` | `channel_id`、`aux_channel_id`、`level`、布林 `enabled`。支援 mono Input 到 mono Aux 的 −150 至 +10 dB send。 |
| `preset_capture` | `channel_id`、`name`，可選 `group_name`、`notes`、`scopes`，從目前 mono Input 擷取指定處理資料。 |
| `preset_update` | `index`、`channel_id`、可選 `scopes`，以同型態目前 Input 更新已有的處理資料。 |
| `preset_rename` | `index`、`name`，可選 `group_name`、`notes`。 |
| `preset_lock` | `index`、布林 `locked`，適用一般使用者 Preset。 |
| `preset_apply` | `index`、`channel_id`、必填 `scopes`，套用相符的處理控制到目前 mono Input。 |
| `preset_delete` | `index`，刪除未鎖定 Preset，並檢查、更新 C9／D2／D3／DB 的索引。 |

`file-inspect --presets` 列出名稱、索引與處理範圍。可加 `--preset-index N --include-raw-controls` 讀指定 Preset。`index` 從零起算。名稱與群組最多31 encoded bytes，備註最多255 bytes。`scopes` 從 `trim`、`eq`、`dynamics`、`delay`、`fader`、`tidal_preamp`、`tidal_eq`、`tidal_comp`、`tidal_gate` 選擇。套用僅複製同型態 Input 已辨識的處理值。實體輸入增益、Insert send、硬體連線、FX／Mix／多通道 Preset 在目前 authoring 範圍外。

## 輸出與資源界線

輸出只可為全新 `.ses`，既有檔案、來源本身或同名路徑一律不覆寫。工具先驗證、在目的資料夾寫完整暫存檔並 flush，再 exclusive atomic publication；失敗不發布部分目的檔，也不退回 overwrite。Python `edit()` 回傳新 `SesFile`，不修改原物件。

目前檔案與 Snapshot stream 各有128MiB byte 上限，編輯 JSON 為1MiB，另有 record／channel／Snapshot count 及插入成長界線；這些是本機資源限制，不是控台規格。讀取只接受 regular file，拒絕會阻塞的來源／request pipe；非法 directory／EOF、越界或不遞增 offset、缺少／衝突 tag、截斷記錄，以及已解範圍內的引用／有限值檢查失敗會拒絕。

`inspect`／`clone` 可以保留其他有限 frame rate 的檔案，語意 `edit` 僅支援來源25fps。完整 Session、request、報告、hash 與原生回載證據留在自己的私人工作目錄；公開測試只使用手工合成容器，沒有原廠 blob 或使用者 Session。

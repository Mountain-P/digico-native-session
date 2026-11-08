# 不啟動編輯器，直接讀寫 `.ses`

`scripts/ses_file.py` 在本機讀取、逐 byte 複製及編輯已辨識的 `.ses` 記錄，不啟動 Windows editor／VM，不連網，也不讀寫程序記憶體。`native_session.py file-inspect`／`file-clone`／`file-edit` 委派到同一實作，無需 `--vm`、`--exe` 或 PID。

## 檔案格式與驗證範圍

支援 [build profiles](build-profiles.md) 所列七個 vN／vO model／release header，包括舊 Q1 V2126 與六個新版 Quantum profile。Header 只能識別檔案格式家族，不能證明建立檔案的 exact EXE；結果明列 `format_profile`、`executable_identity_verified:false` 及 `native_acceptance_verified:false`。

已辨識的容器包含456-byte header、section offset／tag directory、123-byte console record，以及 D2 Snapshot／D3 current-state 記錄。未知 section、記錄多出的 bytes、未變更的 shared pools 與 padding 保留原樣；無修改 roundtrip 必須與來源逐 byte 相同。這個工具尚未完整理解任意 structure、routing、Preset、外部整合與 factory 初始化，不能由少數已解記錄合成任意全新 Session。

直接產生輸出不需要 editor。原廠是否接受輸出，須另以相符版本的 native Load 及獨立回讀驗證。六款新版已各自驗證合成檔的 CP950 Snapshot 名稱、RecallAt、複製／刪除／capture、Input 名稱及 Fader／Trim／Mute；原廠重存後 D2 與獨立輸出逐 byte 相同。舊 Q1 另驗證從空範本 capture 與基本混音。Q5 另測共享引用由1變2及由1變0，原廠重存仍保留正確 UserCount。這些代表案例不能推定任意內容都相容，完整範圍見 [能力矩陣](../../../CAPABILITIES.md)。

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
| `set_channel_name` | `channel_id`、`name`；只改已存在、唯一的 Input raw ID `0x0100..0x01ff` 的 current-state 名稱。 |
| `set_control` | `id`、有限數值 `value`；只改既有 Input 的 band0 fader／trim／mute，見下表。 |
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

## 輸出與資源界線

輸出只可為全新 `.ses`，既有檔案、來源本身或同名路徑一律不覆寫。工具先驗證、在目的資料夾寫完整暫存檔並 flush，再 exclusive atomic publication；失敗不發布部分目的檔，也不退回 overwrite。Python `edit()` 回傳新 `SesFile`，不修改原物件。

目前檔案與 Snapshot stream 各有128MiB byte 上限，編輯 JSON 為1MiB，另有 record／channel／Snapshot count 及插入成長界線；這些是本機資源限制，不是控台規格。讀取只接受 regular file，拒絕會阻塞的來源／request pipe；非法 directory／EOF、越界或不遞增 offset、缺少／衝突 tag、截斷記錄，以及已解範圍內的引用／有限值檢查失敗會拒絕。

`inspect`／`clone` 可以保留其他有限 frame rate 的檔案，語意 `edit` 僅支援來源25fps。完整 Session、request、報告、hash 與原生回載證據留在自己的私人工作目錄；公開測試只使用手工合成容器，沒有原廠 blob 或使用者 Session。

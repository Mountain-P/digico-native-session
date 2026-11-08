# 非 Cue 功能

適用 [build profiles](build-profiles.md) 收錄的 exact EXE、已啟動的 Windows 離線編輯器。所有寫入走原生 loopback TCP2028；記憶體只讀。Target CLI 共用 `--vm`、`--exe`、全新絕對 `--result`，可加 `--build-profile ID`；本機 Windows 見 [Windows 與 Parallels](windows-host.md)。編輯器與 VM 必須先存在。

Quantum1 V2126 保留既有數值／名稱、路由、Preset 與檔案傳輸功能。新增 Quantum1／2 V2242.4、Quantum3（326／338）／5（升級 SD5）／7（`SD7Q.exe` Quantum7）／8 V2242 只開放各自 exact allowlist 的已驗證 numeric／boolean setter 與 Input name；boolean 另須明確 enum domain，其他 owner 或讀得到的 controller 不因此取得寫入資格。這六個 profile 的 routes、presets、transfer 均關閉；inspect、inventory、structure、meters 可用。新增版 `status` 只開放 Quantum1／8，Quantum5／7 的 TCP2029 不支援。

## 探索、讀取與規劃

- `status --encoding ascii`：在 profile 開放時連原生 TCP2029，接收並驗 checksum／版本／frame，不送 application payload。
- `structure --encoding cp950`：核對原生 alias，連續兩次讀123byte結構並解碼 count slots／取樣率等欄位；未證實的 mode labels 保留 raw，不提供 structure setter。
- `inventory --encoding cp950 --channel-id 0x0100`：讀實際 tree、通道／模組／controller、定義與 response curves。省略 channel-id 讀完整 tree 的 compact raw inventory，不附曲線/LUT/重複浮點視圖；需特定通道的可寫範圍時再做 filtered inventory。結果完整寫入 result，stdout 預設摘要。
- 加 `--include-physical-sources --include-group-outputs`：在單通道 inventory 補齊可用實體 I/O 與 Group Output metadata。路由規劃必須有這些資料。
- `catalog --inventory /absolute/inventory.json --module 4 --writable-only`：本機查 typed 候選，支援 query、channel-id、limit、output；仍需通過 profile 的 exact write allowlist。讀取 tree 或顯示候選不等於可寫。
- `routes --inventory /absolute/inventory.json --direction input`：Quantum1 V2126 的本機路由目錄，列出本次已配置且實際 fitted 的端點；output 可查輸出。新增六個 profile 不開放路由功能。
- `meters --channel-id 0x0100 --limit 64`：只讀 native meter registry／output／secondary。回報 freshness 與合成循環狀態，不把 offline 數字聲稱為實體音訊。

原生 Input1 的 raw ID 是 `0x0100`（十進位256），不是 logical channel1。Control ID 是保留 band 的5bytes hex。以本次 inventory 為準，不跨重載沿用 pointer。

## 寫入 request

使用既有 schema：`schema_version:1, fps:25, encoding:"cp950", expected_count:<inspect實際值>, operations:[...]`；新增 profile 在 raw request 加入對應 `build_profile`，或由成功 inventory 綁定。以 `plan --request … --inventory …` 預覽，`execute --request … --inventory … --send …` 執行。`--inventory` 填完整成功 result。此處 operations 不改 Snapshot count。

| op | 必要欄位／用途 |
|---|---|
| `set_control` | `id`、數值 `value`；可加 `expected`。依 profile 的 datatype、range 與 allowlist 檢查，不接受任意 packed DWORD |
| `set_channel_name` | `channel_id`、`name`；嚴格編碼後1–31bytes。新增六個 profile 限已驗證的 Input name owner |
| `set_input_route` | Quantum1 V2126：`channel_id`、`source:{port_type,port_number,socket_index}`，或 null 解除；`route_kind` 見下文 |
| `set_output_route` | Quantum1 V2126：`channel_id`、`destination:{port_type,port_number,socket_index}`、bool `connected` |
| `set_group_assignment` | Quantum1 V2126：`channel_id`、本次觀察到的 `group_channel_id`、bool `enabled` |
| `export_session` | Quantum1 V2126：`source`、全新 `destination` Windows ASCII `.ses` 路徑；獨立請求 |

`route_kind`：main（預設）、alt、key、mustard_key、insert_return、insert_send；兩種 Insert 加 `insert_index:0或1`。目前語意路由只實作 mono Input 的已知 callback，拒絕不認識的 owner／端點，不猜硬體配置。Insert send 的 source 選的是實體 output；其他 source 是實體 input。

數值使用 **原生內部單位**。時間參數常為秒，fine delay 可為 samples；欄位上顯示 mS 不代表可以直接送毫秒。以 catalog 的 value_units、verified_conversion、range／allowed_values 為準；不自動轉換。EQ Q、Comp/Gate attack/release 的範圍依當前 shape／mode；工具在送包前獨立重讀 mode。模式切換與同 owner 其他寫入分開 request，切換後重新 inventory。衍生 total-delay、label colour 不可當 setter。

為避免整組集合過期，一批內不重複修改同一 output-set、physical-output ownership 或 BussRoutes；rename 與 route 也分開請求。每次改完相關集合後再 inventory。批次最後可加既有 `save`；load/new_session/export 皆獨立。

Quantum1 V2126 Preset 的七項操作與內容回讀見 [Preset](presets.md)，使用獨立 `--presets` 契約；不是 Snapshot Recall。新增六個 profile 不開放 Preset。

## 語意 authoring

`author --request … --inventory …` 在本機解析精確通道名稱／logical channel／native ID 與參數，輸出目前值、預期值、換算後 native 值及 diff。Request 使用 `kind:"authoring"`，操作為 `set` 或 `rename`；完整範例見 [操作](operations.md#語意-authoring)。名稱與參數必須唯一匹配，不模糊猜測。單位只接受已證實的換算，別名不會繞過 profile allowlist。

`author-execute` 預設 dry-run，加 `--send` 才依序執行。Mode／shape 變更有獨立 stage，後續 stage 重新 inventory、核對 target identity 並重編譯；不重用舊 pointer 或把未編譯 stage 當成可直接送出的 request。多階段不是原子交易，失敗後保留已完成的收據。

## 原生 Session 傳輸

Quantum1 V2126 的 `export_session` 要求明確非空 source，使用原生 file-transfer request／block ACK／SessionVerify，串流寫入 destination.partial，驗證大小與 hash 後發布；失敗保留 partial 與 recovery artifacts。此功能匯出既存磁碟檔，不含尚未儲存的 live changes，不接受 mirror、不觸發 autosave 或載入。不能與 Save 混成同一請求；先完成 native Save 再匯出。

## 驗證界線

成功需讀該次 result；每步檢查 target identity、owner、原生值及語意結果。已開放功能有各 profile 的離線編輯器回讀證據；沒有實體控台、DSP、Dante 音訊傳送或聲學驗證。全量 tree inventory 與嚴格 typed setter 不等於每一個原廠功能都有可用 API。

公開 Mirror 工具是有資源界線的被動 parser 與 evidence ledger，另保留獨立 Verify 研究；`initial_sync_complete` 仍為 false，沒有完整 mirror state exchange 或初始同步完成的宣稱。Compatibility acceptance 不等於 Session 已同步。

目前沒有已證實的任意 Session channel-count／bus-count headless setter、factory reset、任意 palette、插件 GUI、或任意跨版本控制。結構 decoder 只讀123byte native record，不能自行改寫 `.ses`。未知處應標記未支援，不放寬 hash 或寫程序記憶體。

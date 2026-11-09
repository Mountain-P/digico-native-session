# 已實作的 `.ses` 記錄

這是 `ses_file.py`、`ses_snapshot.py` 與 `ses_console.py` 使用的有限規格，來自原廠 Store／Load 靜態分析及合成檔案核對。範圍為已收錄的 Q1 V2126／vN 與 Quantum V2242／vO 格式；不是所有 DiGiCo Session 的完整規格。檔案 header 只能識別格式，不能證明來源 exact EXE。操作方式見 [直接檔案工具](direct-files.md)。

## 容器與基本記錄

整數為 little-endian。已解 section 是可直接解析的原始記錄，沒有把整個檔案當成壓縮包或記憶體映像。

| 檔案 offset | 已確認內容 |
|---|---|
| `0..25` | 型號／`.SES`／vN 或 vO header；須與選定格式相符。 |
| `32` | `uint16` header 大小，456。 |
| `34` | `uint16` release：2126 或2242。 |
| `36..158` | 123-byte `tConsoleRec`：count slots、timecode／sample-rate 等配置；其他 bytes 保留。 |
| `256..415` | 40個 `uint32` section offsets。 |
| `416..455` | 對應40個 byte tags。 |

第一個 active tag0 的 offset 是456；後續終止 tag0 的 offset 是 EOF，其後 slots 為0。Active offsets 嚴格遞增。修改 section 長度時重建 offsets，保留未知 section、順序及其 bytes。Tag 只標識 directory entry；不能假定每段本體都再含相同 token。

原生 length-record 是 `uint16 byte_length + raw bytes`，不是壓縮資料。Token 是 `uint16`。現代 `tCTList.Store` 寫 `int32 count` 再逐 item 寫入；不寫 RAM pointer／capacity。舊式6-byte list header 另有版本分支，本實作不推廣支援。

Length-record 的原生 Load 會對短記錄補零，忽略超過目標容量的 bytes；工具保留原始長度及多出 bytes，只有明確修改的欄位才改寫。變長字串亦是 `uint16 byte_length + bytes`，不得把 RAM ShortString 的一個長度 byte 直接複製到磁碟。

## D2：Snapshot 清單

完整 directory block 開頭是兩個 `D2` token：外層 section token，加上 `SnapshotList.Store` 自己的 token。接著依序為：

1. Module pool：`int32 count`，每筆 `DA + ModuleSnapshot`。
2. Unused ChannelSnapshot list：`int32 count`，每筆 `D5 + ChannelSnapshot`。
3. Snapshot groups：`int32 count`，每筆 length-record，原生容量336 bytes。
4. Cue list：`int32 count`，每筆 `D3 + ConsoleSnapshot`。
5. 三個 length-record：options237、section scope288、section exclusions264 bytes。

沒有 pool、group 或 Cue 的普通 D2 block 是815 bytes。已有零 Cue Session 仍可含未使用的 pool；不能只因 count0 就刪掉整段 pool。

Options 解碼後的 offset0、4、8、12 分別是 Current、Undo、Surface、Scroll。它們不是四個可一律同樣重排的 live reference。原生 Delete 修改 Current／Surface，保留 Undo／Scroll；Undo 與未序列化的 RAM undo object 有關。工具沿用這個分界，插入保留既有 Current／Surface 所指向的物件。

## Channel、Module 與 Controller

ChannelSnapshot 的 stream 為：`D5`、一個容量1的 OwnModuleSnapshots length-record、`int32 module_count`、module items、容量39的 metadata length-record。Own=true 時 item 是完整 `DA` ModuleSnapshot；Own=false 時 item 是 `int32` pool index，`-1` 為空值。Metadata 解碼後包含 channel ID、固定槽名稱、Preset index 與 stereo mode。

ModuleSnapshot 為 `DA`、容量58的 header length-record、`8 × nControls` 原始 bytes，再接三個 `int32`：parent module pool index、original ConsoleSnapshot index、Player index-plus-one。Header 解碼後的主要位置如下；這些是**記錄內 offset**，不是整個檔案或 RAM object offset。

| Offset | 內容 |
|---|---|
| `0` | Module TypeID，int32。 |
| `4` | 名稱 ShortString 槽32 bytes。 |
| `36` | TimeStored，8 bytes。 |
| `44` | nControls，int32。 |
| `48/49/53` | ScopeFlags／CrossfadeTime／IgnoreScope。 |
| `54` | UserCount，int32。 |

每個8-byte controller record 是 `Parameter / Module / Band / Flags / raw value4`。Channel／ChannelType 來自 parent channel ID。這不是傳輸協定的5-byte ID加值，也不包含 live controller pointer、definition pointer 或 response LUT。

Global pool 的 parent 必須為 `-1` 或較早的 pool index：原生 LoadItem 完成後才加入 pool，future／self／cycle reference 不會被當成有效 parent。Inline owned modules 可引用已完整載入的 global pool。

UserCount 是原生 shared module 使用計數，Load 不會自動重算。Duplicate 新增 shared channel edge 時逐次加1，Delete 移除時逐次減1；工具依變動量修改，拒絕 underflow，保留 pool 排序及零使用的記錄。單純改名／改時間不改 UserCount。

## D3：目前混音與 Cue metadata

獨立的 directory tag D3 與 D2 內每個 Cue 都使用 `D3 + ConsoleSnapshot` stream。依序是 channel list、變長名稱、timestamp8、scope992、flags2、`int32 MIDI_byte_count + MIDI bytes`、relays8、layout807、timing81、notes、allocation；未明確註明的數字是 length-record 的原生容量。Allocation 是4-byte items。工具保留 notes／layout 等未修改內容，不推定所有欄位語意。

Timing 解碼後 offset0 是 Number int32，以百分之一號保存；raw100 顯示 Cue1。工具報告分成 `number_raw` 與 `number`，`set_number(index, integer)` 接受正整數顯示號並乘100。新 Cue 使用未占用的下一個整數號，是檔案工具的明確政策，沒有聲稱重現原生中間插號或 MIDI 號分配。

Timing offset10 是 RecallAt int32 quarter-frames；flags 的 bit`0x80` 控制啟用。25fps 下 `00:01:00:00` 為6000 quarter-frames。名稱最多31個 encoded bytes，與 Unicode 字元數不同；CP950／UTF-8 不可混用或靜默截斷。Locked bit`0x2000` 的既有 Cue 拒絕編輯／刪除。

從 current D3 建立 Cue 是複製**同一檔案已保存的 current-state capture**，不是讀取目前執行中的 editor，也不是從畫面名稱推算混音。Create 需要 owned inline modules；外部繼承／引用另受檢查。

## 跨 section 引用

vO 的 tag`73` 是 SetList：top count，每筆 raw32-byte name 加 child count／int32 主 Cue indices，末尾是 current SetList index。RAM entry 中的 Snapshot pointer 會在 Store 時轉為主清單 index。`SnapshotSetlists` 有靜態格式對應的 insert／delete／reorder remapper，但公開檔案編輯的結構操作目前仍保守拒絕非空 SetList，直到相應原生案例驗證。

DB Preset section 為 `DB + count`，每筆 `D7 + ChannelSnapshot list + 三個變長字串 + 容量14的 metadata length-record`。D3 與 DB 的 ModuleSnapshot 都可能攜帶 original Snapshot index；結構編輯目前要求其為`-1`，不猜測改寫語意。D2 內已解 origin references 則依插入／刪除映射修正。無修改 roundtrip、改名或時間碼成功，不代表可忽略這些外部索引去改變清單數量／順序。

## 驗證界線

解析與重新輸出的 bytes 相同，證明已解 framing 與保留策略相符。合成測試另覆蓋截斷、越界、鎖定、引用、計數與變動範圍。原廠接受度須以相符版本載入、獨立欄位讀回及必要的原廠另存核對。[能力表](https://github.com/Mountain-P/digico-native-session/blob/main/CAPABILITIES.md)

## 新建場次與通道結構

C9 是通道和接點結構。Token 後有兩組 socket selector，每組含 `i32 byte_length`、CRLF 文字與每列一個 `u32` key。接著是 group list。CA／D1 group 的 metadata block 為 108 bytes。CA group 的 CB channel metadata block 在 vN 為 120 bytes，在 vO 為 121 bytes。Channel ID 位於 `+0x00`，ShortString31 名稱位於 `+0x04`，StereoMode 位於 `+0x25`，Preset index 位於 `+0x26`，paired buss ID 位於 `+0x32`。建立器同時生成 ConsoleRec 計數、C9 通道與 D3 current-state 通道。

F6 含 50 個 210-byte port record，port type／number 位於 `+0xd0`／`+0xd1`。FC 存 device rows 與 fitted socket type arrays。新建以已比對的本機 F6 record 起始。Routing 寫入 D3 的已辨識控制。實體輸出 socket 的 `00 12 00` 保留來源 Channel ID 於 high word。Stereo Aux／Group 的右聲道使用 C9 reciprocal `BussID` 指向的 type7 companion。

F3 是 `tChannelBanks`，每列為 361 bytes，包含 bank key、兩個 ShortString15 及十二個 Channel ID。D4 的 807-byte layout 包含八個 bank layout 與 master fader IDs。建立器依各型號保留樣本的 slot 位置指派 bank key，並清零 process-local pointer 欄位。D3 的 layout 與 D4 使用同一份新 layout。

DB／D7 Preset 的直接編輯維護 C9 `+0x26`、D5 channel snapshot metadata `+34` 的 positional index。套用處理值時依完整的 parameter、module、band ID 對應 mono Input，保留目前路由。

生成範圍、編輯契約與驗證層級見[直接讀寫指南](direct-files.md)。既有未知 sections 的原始資料由編輯器保留。原廠新建檔載入、任意結構遷移、FX／Mix Preset 與跨版本轉檔仍需獨立證據。

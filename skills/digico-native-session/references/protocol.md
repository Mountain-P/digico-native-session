# Quantum1 V2126 原生協定與RVA

本頁記錄已驗證client使用的有限協定，不是所有DiGiCo設備的完整API。執行能力以腳本白名單為準。

## 版本

| 項目 | 值 |
|---|---|
| Target | Windows Quantum1離線編輯器 V2126，PE32／x86 |
| EXE SHA-256 | `535097f38bb459e6e299b5da50b62fa811511d92af6d7215a13d703a416f177d` |
| Preferred image base | `0x400000` |
| Pointer | 4bytes |
| Client目的地 | `127.0.0.1:2028` |
| Timecode profile | 25fps |

記憶體位置使用runtime module base＋RVA；從程序讀出的heap pointer已是runtime位址，不再加ASLR差值。版本不符即停止，不寫globals來冒充connected或版本狀態。

## Frame

Little-endian固定prefix為16bytes：

| Offset | 欄位 |
|---:|---|
| 0 | uint32 Size，含全部prefix、payload與checksum |
| 4 | uint16 ConnectionIndex，本client送255，由接收端管理本機索引 |
| 6 | byte Category |
| 7 | 5bytes ControlID |
| 12 | int32 ListIndex |
| 16 | payload，至少2bytes |
| Size−2 | uint16 checksum：前方全部bytes相加mod65536 |

最小frame20bytes，int32 payload的frame22bytes。Delphi記錄size20包含從+16開始的data union，不是20-byte固定wire prefix。

Command ID以 `<HBH>(IDlo, IDBand, IDhi)` 編碼，以下band均0。Category3字串payload是uint32 byte length＋raw bytes，不是NUL結尾、字元數或UTF-16序列。

| 操作 | Category | IDhi | IDlo | ControlID hex | Payload |
|---|---:|---:|---:|---|---|
| Create snapshot | 1 | `0x708` | `0xC9` | `c900000807` | int32 −1 |
| Delete snapshot | 1 | `0x708` | `0xCE` | `ce00000807` | int32 0 |
| Rename | 3 | `0x708` | `0xCD` | `cd00000807` | byte length＋name |
| RecallAt並啟用 | 1 | `0x715` | `0xB5` | `b500001507` | int32 quarter frames |
| SaveAs | 3 | `0x1A5` | 0 | `000000a501` | byte length＋ASCII絕對路徑 |
| Save current | 1 | `0x1A6` | 0 | `000000a601` | int32 0 |
| Load file | 3 | `0x19B` | 0 | `0000009b01` | byte length＋ASCII絕對路徑 |

Snapshot ListIndex是零起始位置；create的−1取目前console state。Save／Load的ListIndex為0。`new_session`組合LoadFile與SaveAs，沒有獨立factory-reset opcode。

## Identity與liveness

單純TCP accept或Network選項不足以開啟一般command。Client先送Category0/sub1，ControlID=`<HBH>(1,0,2126)`。Body為state1、flags2（remote PC）、network ID255、integration0、Pascal short name。名稱從程序的compatibility prefix起始，後接client後綴，總長最多30bytes。

合法接收封包經StoreMessage→Connect重設peer的AYTcount；原廠亦使用Category0/sub0、四個IPv4 payload bytes的discovery／idle frame。Client保留接收流量並送idle traffic。靜態路徑不是連線穩定性的保證；長批次的本機中斷原因尚未確定。封裝在已驗證操作邊界斷線、等舊peer清理後重新正常握手，不重播未確認的create／delete。

## 唯讀profile

| 項目 | RVA或物件offset | 型別 |
|---|---|---|
| SnapshotList global | module+`0x127305C` | u32 pointer |
| FPS | module+`0x127AD30` | u8 |
| Compatibility prefix | module+`0x127AD9C` | ShortString |
| Protocol version | module+`0x127A158` | u16 |
| Any connected peer | module+`0x127AD6E` | u8 |
| Connection manager | module+`0x126E670` | u32 pointer |
| Current session path | module+`0x127B4FC` | ShortString |
| Filing result | module+`0xA28560` | i32，不能單獨作新save的完成證明 |
| Save worker pointer | module+`0xA28490` | u32，VMT應為module+`0x535038` |
| Worker path／Finished | worker+`0x30`／+`0x11` | ShortString／u8 |
| Worker fatal／SaveFail／CopyFail | worker+`0x28`／+`0x131`／+`0x132` | u32 exception pointer／u8／u8 |
| SnapshotList VMT | module+`0x7F9BB0` | list+0 |
| Items pointer | list+4 | u32 |
| Count／capacity | list+8／+12 | i32 |
| Snapshot pointer | items+4×index | u32 |
| Snapshot VMT | module+`0x7F8188` | snapshot+0 |
| Snapshot name | snapshot+`0x28` | ShortString[31] |
| RecallAt | snapshot+`0x76F` | i32 quarter frames |
| Flags | snapshot+`0x430` | u16，active為0x80、lock為0x2000 |
| Current／surface index | list+`0x24`／+`0x2C` | i32 |

Snapshot size=`0x7D4`，SnapshotList size=`0x358`。Connection record的IPv4在+0、AYTcount／threshold／Connected在+`0x129`／+`0x12A`／+`0x12B`、socket pointer在+`0x10D`。

Reader檢查VMT、count／capacity及名稱長度，讀完重查list／items pointer與count。Handle權限為 `PROCESS_QUERY_INFORMATION | PROCESS_VM_READ` (`0x410`)；沒有WriteProcessMemory、注入或任意call。

## 時間、名稱與檔案

```text
Q = ((HH * 3600 + MM * 60 + SS) * 25 + FF) * 4
```

每frame4單位、每秒100單位。虛構Cue A的 `00:01:00:00` 對應6000。RecallAt handler同時設active bit0x80；Duration是另一欄位。

名稱儲存是最多31bytes的ShortString，沒有per-name codepage。Serializer／loader保留raw bytes；CP950跨save／load保存已驗證，GUI渲染不由此保證。路徑同為byte字串，本client限ASCII本機磁碟路徑最多255bytes。

載入前檢查36-byte header：前26bytes為 `DiGiCo     Q1  .SES vN   \r`，offset34的uint16為2126。這是狹窄的model／version篩選，不是完整`.ses` parser，也不能證明檔案為零Snapshot、25fps或其餘內容有效。這些條件需要原廠載入及實際回讀。


## 非 Cue 擴充

封包仍使用同一16-byte prefix與尾端u16加總 checksum。Category8直接保留5-byte native control ID（Parameter/Module/Band/Channel/ChannelType），ListIndex0；numeric payload是4個原生 bits，string payload是u32 byte_length後接raw bytes。不同raw datatype不能互換；packed routes必須用語意planner。

Processing Preset命令group0x1B6：C9 capture、CE rename、CC update、C8 recall、D2 lock、D1 delete；scope D3用Category2與u32長度32後的set256。詳細欄位、scope與merge語意見[presets](presets.md)。Meter只讀controller output/secondary，沒有注入Category9來模擬音訊。

File-only Session傳輸使用獨立request、block ACK與SessionVerify，不接受完整mirror。Block header有未初始化欄位，ACK需回送原5-byte identifier再改subcommand；不能將該欄位固定成從單次capture猜出的常數。完整payload與檔案hash均需驗證，成功由原生load另核對。

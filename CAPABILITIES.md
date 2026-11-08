# 原生協定能力與驗證範圍

適用對象是 **Windows Quantum1 V2126 離線編輯器、本機 loopback、25fps Session**。工具使用原生 TCP 與唯讀程序記憶體讀回，不使用 OSC、UI 自動化或程序記憶體寫入。實體控台、rack、實際音訊與其他版本尚未驗證。

本頁記錄能力與驗證，不附任何使用者 Session、歌單、時間碼、個人路徑、安裝器或廠商二進位檔。範例與測試使用獨立的合成資料。

## 如何閱讀證據等級

| 等級 | 能支持的結論 |
|---|---|
| Wire | 已辨識封包 framing、checksum、category、packed control ID、payload 型別與傳送方向。 |
| Static | EXE 的 native 定義、RTTI/VMT、實際 callback 與分支支持該語意。仍需實測目標是否接受。 |
| Runtime | 在指定離線編輯器送原生指令後，獨立讀回 controller 與相關語意欄位。儲存／重載另行列出。 |

「可編譯」或「writable candidate」是有足夠型別與範圍資料的候選，不表示每個 channel、模式、參數或硬體組合均已測試。

## 已有 runtime 證據

| 能力 | 已確認行為 | 實測範圍與界線 |
|---|---|---|
| 通道與控制項 inventory | 讀取 channel/module/controller、packed ID、native datatype、定義、owner callback、current/next/output raw bits、routing 與 I/O 配置 | Filtered inventory 已讀回；結構在結尾重驗。控制值屬循序取樣，整份資料不宣稱原子性。 |
| Structure 唯讀 | 原始123-byte配置記錄、已知欄位與alias視圖 | 兩次讀取一致的驗證已測；headless修改channel/bus counts尚未支援。 |
| 基本混音 | Fader、mute、pan、digital trim | 原生 Category8 寫入及 Value/Output 讀回已測；離線數值不代表音訊或硬體增益已改變。 |
| Channel 名稱 | 原生 channel label 寫入與 raw bytes 讀回 | 已測名稱變更；名稱上限依 encoded bytes 判定。 |
| EQ、filter、delay | 代表性的 EQ frequency/Q/gain、EQ in、filter frequency、delay/on | 有混合矩陣讀回案例。Shape4時 Q 上限0.85、超界計畫拒絕及合法 Q 值回讀已有專項測試。 |
| Dynamics | 代表性的 compressor threshold/gain/ratio、Gate attack/release | 一般與替代模式的時間參數邊界已有獨立實測；不延伸為所有模式／參數已測。 |
| Aux sends | Level、on、pan、pre/post | 代表性的監聽 send 已經原生寫入及讀回。 |
| Mustard | 代表性的 preamp tube drive、EQ gain、compressor/Gate threshold | 限已測控制項；實際 DSP 音訊效果尚未驗證。 |
| Input routing | Main、Alt、normal Gate key、Mustard key | 以實際 port/device/socket/channel domain 編譯，驗 controller 及可用的 channel route 欄位。 |
| Insert routing | 兩個 insert 的 return/send | 各自的來源／目的及 module/band suffix 綁定、兩組 send/return 清空均已測；未涵蓋任意替換組合。 |
| Direct output routing | 新增一個實際配置的 physical output | 驗完整 OutputSockets 集合及目的 socket 的 source controller。負值移除指定連線亦已實測；嚴格檢查完整輸出集合、目的端source清空及命令echo／歸零的穩定狀態。 |
| Group/Buss assignment | 指定 GroupOutput 的 boolean assignment | 原生 float32 0/1 controller與 BussRoutes bitset交叉驗證；驗證只改指定 bit。 |
| Session 保存與載入 | 原生 save/load 與重載檢查 | 混音矩陣曾經保存、原生匯出、再載入並核對保留值；一般檔案操作仍須符合版本與路徑約束。 |
| 原生 Session 檔案匯出 | 多區塊傳輸、完整接收、來源／接收檔案 hash相等 | 已測 file-only transfer，來源檔保持相同。接收檔另經原生 load 驗證。 |
| Input presets | Store、rename、lock/unlock、scope、update、scoped recall | 單項原生讀回及recall scope恢復已測；save/load後routing與preset captured內容指紋核對保留。更強的unsaved sentinel覆蓋重載測試結果待補。 |
| MCP實際呼叫 | Status、meters、presets、preset execute | Read工具及`preset_execute(send:true)`已經實際呼叫；寫入仍通過同一原生／readonly readback驗證。離線meter欄位不代表實體音訊訊號。 |

另已實測 readonly status、13筆選定通道 meter、123-byte structure讀回，以及 MCP連續查詢與明確 send:true 寫入。

混合控制測試包含23個變更與一次保存；routing 測試包含10個 routing/group變更與一次保存。這些計數描述測試覆蓋，不是工具的總功能數。

## 型別與語意驗證

Category8 數值控制有 float32、離散 float32、packed int32 等不同型別。工具從實際 controller 定義決定 payload，不從顯示名稱猜型別。Seed/response資料或 semantic domain不完整時，控制項保持唯讀。

Routing 使用具體 endpoint。Physical來源／目的需同時符合 port record、配置的 IODevice、socket type、實際 channel與socket module；InputSocket copy/listen狀態與OutputSocket active狀態也參與驗證。Direct output 是增量新增／移除指定連線，insert send則先處理原連線再連新目的，兩者有不同的 postcondition。

Compressor與Gate時間參數按目前模式決定界線，並檢查Resending與適用的mode timer。Classic EQ Q另按同band shape限制上限。Context在inventory與實際寫入前分別讀取；舊模式資料不能授權新模式的值。

## Inventory 詳細程度

* **Filtered detailed**：指定channel，保留完整數值 views、可選response curves/LUT及必要的physical/group targets，適合產生可寫計畫。
* **Full compact raw**：預設全量模式。保留全部遍歷到的IDs、定義、owner/callback、必要raw值、context及routes；省略重複的數值物件、response tables與獨立edges向量。結果明示省略項目，數值缺少bounds時仍保持唯讀。

全量 compact 實測完成：25 groups、513 channels、2,259 modules、44,680 controllers、未知節點0；遍歷217.5秒、結構重驗6.5秒。讀回包含103,614份identity witness，end驗使用1,530次page讀取與11次exact fallback。這是指定離線編輯器的測試配置與耗時，不是所有Session的性能保證。特定通道曲線仍使用filtered detailed模式。

## 尚未列為已驗能力

* 實體控台／rack控制、實際訊號與音訊效果。
* 其他DiGiCo型號、軟體版本、任意遠端主機。
* 任意stereo/stem/Nodal routing與完整硬體patch模型。
* Comp-link、Nodal mask、mix-minus等尚無獨立semantic domain的packed值。
* 任意Session structure建立、完整`.ses` parser、完整console mirror初始同步。
* Headless修改結構counts、palette與未完成語意驗證的structure寫入。
* 未列出callback與runtime驗證的其他UI功能。

新功能應保留「原生送出 → 獨立讀回 → 適用時save/reload再讀回」的驗證鏈；物理設備驗證另行記錄。

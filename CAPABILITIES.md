# 能力與驗證範圍

0.3 適用於隨附 exact build profile 的 **Windows Quantum 離線編輯器、loopback、25fps Session**。所有修改使用原生 TCP，另以唯讀程序記憶體確認；不用 OSC、UI 自動化或程序記憶體寫入。EXE 名稱或產品版本相同，不代表 hash、layout 或能力相同。

## 逐 build 矩陣

| Profile | 型號／版本 | Inspect／inventory／structure／meters | Native status | Numeric／boolean／Input name | Snapshot／25fps | SaveAs／Save／Load／範本新檔 | Routing／Preset／transfer |
|---|---|---|---|---|---|---|---|
| `quantum1-v2126` | Quantum1 V2126 | 已驗 | 已驗 | 既有 typed/domain 契約 | 已驗 | 已驗 | 既有範圍已驗 |
| `quantum1-v2242-4` | Quantum1 V2242.4 | 已驗 | 已驗 | exact allowlist | 已驗 | 已驗 | 關閉 |
| `quantum2-v2242-4` | Quantum2 V2242.4 | 已驗 | 關閉 | exact allowlist | 已驗 | 已驗 | 關閉 |
| `quantum3-v2242` | Quantum3 V2242（326／338） | 已驗 | 關閉 | exact allowlist | 已驗 | 已驗 | 關閉 |
| `quantum5-v2242` | Quantum5 V2242（升級 SD5） | 已驗 | 關閉 | exact allowlist | 已驗 | 已驗 | 關閉 |
| `quantum7-v2242` | Quantum7 V2242（`SD7Q.exe`） | 已驗 | 關閉 | exact allowlist | 已驗 | 已驗 | 關閉 |
| `quantum8-v2242` | Quantum8 V2242 | 已驗 | 已驗 | exact allowlist | 已驗 | 已驗 | 關閉 |

新版六款均已通過未修改公開 Windows runner 的 typed request 回歸，含範本新檔、Snapshot 編輯／未存標記重載，以及代表性 fader／trim／EQ gain／LPF／mute／Input name 保存重載後的獨立回讀。範圍是各 exact EXE 的離線 runner，不等同六款皆有獨立 CLI／MCP 傳輸實測。未將舊 Q1 的 routing、Preset 或 transfer 成功推定到新版。`catalog` 為本機 inventory 轉換，沒有額外目標連線；詳見 [驗證](skills/digico-native-session/references/validation.md)。

SD 範圍只包含 Quantum5 的升級 SD5，以及 Quantum7 的 `SD7Q.exe`；一般 SD5、SD7 不在此矩陣。Quantum638 未找到可核對的現行官方 offline download，仍未解決；Quantum8 的測試不能替代它。矩陣外的 EXE、其他 FPS、實體控台與遠端主機均未驗證。

## 證據代表什麼

| 層級 | 可支持的結論 |
|---|---|
| Wire | framing、checksum、category、packed ID、payload 型別或方向已辨識。 |
| Static | 該 EXE 的定義、RTTI／VMT、callback 與分支支持語意，仍需 runtime 接受證據。 |
| Runtime readback | 指定離線編輯器接受原生操作後，獨立讀回 controller 或相關語意欄位。 |
| Save／reload | 原廠編輯器保存並載入輸出檔後，再比較持久化欄位；不代表所有 Session 內容都已解析。 |

「可編譯」或 `writable_candidate` 不是完整產品認證，也不代表所有 channel、模式、參數或硬體組合都測過。以下證據均不證明實際音訊、DSP 效果、硬體 gain 或網路端點的物理交付。

## 共通能力與界線

- **讀取**：取得 channel／module／controller、定義、owner callback、raw value、response 資料與選定結構欄位。Filtered detailed inventory 適合產生控制計畫；full compact 可省略曲線及重複 views。結構在結尾重驗，但值為循序取樣，`values_atomic=false`。
- **新版控制**：只開放逐 build native 回讀、恢復已驗證的 numeric／boolean prefix 及 Input name entry。每次仍要 exact datatype、definition、owner VMT、callback、有效 response seed／LUT、bounds／明確 enum domain 與 fresh value binding。缺少模式 domain 的控制保持唯讀；其他 boolean／packed 值不因相似名稱而新增授權。
- **Snapshot 與檔案**：create／insert／rename／timecode／delete、25fps RecallAt、SaveAs／Save、不同路徑 Load、同路徑 alias Reload 已逐 build 回讀。零 Snapshot 範本 Load 後另存也有回讀證據。`new_session` 保留範本結構／routing，不是 factory reset；同版範本必須由使用者提供。
- **Authoring**：通道 ID／logical／exact name 與參數 alias／native name／ID；未知或重名拒絕。只在契約有證據時接受 Hz／dB／s／ms。Mode 操作分 stage，barrier 必須 fresh inventory、同 target 合併與重編。失敗回部分收據，不宣稱原子交易或自動 rollback。
- **執行約束**：EXE hash／profile／PID、loopback endpoint owner、Session path／FPS、當前值與 callback 重新核對；讀取有界重試，未知寫入結果不重播。Host lock 與 guest lease 只協調使用本工具的 writer，不取代獨立回讀。
- **可審閱產物**：request／result、逐步 journal、authoring intent／stage／refresh artifact；`recover` 只讀本機紀錄，檢查 hash chain 與已確認前綴，不開目標也不重播。MCP 使用同一 typed CLI，寫入預設 dry-run。

## 舊 Quantum1 V2126 保留的驗證

| 能力 | 已驗代表案例與限制 |
|---|---|
| 混音與處理 | Fader、mute、pan、trim，代表性的 EQ／filter／delay、Dynamics、Aux send 與 Mustard 控制；不是所有參數與模式的完整掃描。 |
| 模式邊界 | Classic EQ Q 按同 band shape，Comp／Gate time 按目前模式與 timer；inventory 與送出前各自核對。Fine delay 為 samples，不由顯示標籤推定毫秒。 |
| Routing／Group | Mono Input Main／Alt／key、兩組 Insert return／send、實體 output 新增／移除、Group assignment。以 controller 與 channel／socket／bitset 交叉驗證，不推定任意 stereo／stem／Nodal patch。 |
| Preset | Input processing store／rename／lock／scope／update／scoped recall；獨立 request 與回讀，不能套用到新版被關閉的 Preset feature。 |
| 原生匯出 | File-only 多區塊傳輸、ACK、接收檔與來源 hash 核對、再以原生 Load 驗證。它不授權 Mirror 連線。 |
| 保存與 MCP | 代表性混音／routing／Preset 資料的 Save／reload，以及 read／explicit-send MCP 呼叫；每次使用仍須查看當次 receipt。 |

## Mirror 與尚未驗證的工作

公開 `mirror_state.py` 是有界被動 decoder／state ledger；SessionVerify 的相容性檢查另有獨立研究。成功的 Verify reply 不等於已套用全部 live state。尚無已證實的完整初始同步終點，`initial_sync_complete=false`、`end_marker_verified=false`；CLI／MCP 不提供 full-Mirror 寫入、raw-packet 傳送或任意 socket API。新版 Verify 欄位與 checksum 差異不能沿用舊 Q1 的語意判定。

實體控台／rack、音訊效果、任意 Session structure setter、完整 `.ses` parser、未核准 packed domain、palette、完整硬體 patch 與矩陣外 build 仍不列為已驗能力。詳細契約見 [build profiles](skills/digico-native-session/references/build-profiles.md)、[authoring](skills/digico-native-session/references/authoring.md) 與 [Mirror 研究](skills/digico-native-session/references/mirroring-research.md)。

本頁不附使用者 Session、歌單、時間碼、個人路徑、installer 或廠商二進位檔；範例與自動測試採合成資料。

# 驗證與失敗恢復

## 已驗證範圍

在確切Quantum1 V2126／25fps／本機loopback離線編輯器，以拋棄式Session做功能整合測試，涵蓋範本式new session、create／insert／rename／timecode／delete、SaveAs／SaveCurrent、覆寫備份，以及從磁碟恢復未存測試標記的同路徑重載。

驗證使用唯讀程序記憶體、檔案hash與原生Save／Load；沒有UI輸入或程序記憶體寫入。Raw name bytes保存與重載已驗證，中文GUI呈現未驗證。其他EXE版本／FPS、實體控台或現場音訊、任意Session的全部mixer欄位及factory reset均不在這項驗證範圍。

公開repository不含私人測試Session、流量、範本、歌單或其派生hash。自己的runtime資料也可能包含show內容，請留在本機。

## 每次執行的成功條件

1. `plan`成功只證明schema／index／bytes通過檢查。它保留 `runtime_acceptance_verified=false`，不能稱作已修改編輯器。
2. 實際執行讀取本次新result，需有正確 `request_sha256`、`status="ok"`、相符的 `verified_steps` 及 `after`。TCP寫入成功或收到資料不是操作成功。
3. Snapshot修改比較實際count、名稱raw bytes、RecallAt與flags，並檢查其他Snapshot的相關欄位。不要以解碼後的替代字元取代 `name_hex`。
4. Save先確認原廠save worker的VMT、目的路徑、Finished與SaveFail／CopyFail／fatal exception。等worker完成且無錯誤後才開目的檔計算hash，接著檢查新／變更且穩定的檔案、目前路徑與Snapshot fingerprint。舊filing result0不能單獨證明新save完成。
5. 要確認持久化，可用獨立load request重載輸出檔，將載入後Snapshot列表與保存前狀態逐列比較。Load會替換目前狀態，不與create等操作混在同一request。

Same-path load沒有可觀測filename轉換，不能只靠舊result與穩定舊欄位證明這次load執行。封裝先建立SHA相同的唯一暫存alias，原生載入alias並觀測路徑變化，再重新連線載回原檔，觀測第二次變化並比對兩次Snapshot fingerprint及原檔hash。這不代表已證實原廠會忽略同名load。

正常成功後刪除alias。若失敗且目前路徑仍指向stage，或無法確定，保留檔案並回傳 `recovery_stage_path`。含外部伴隨檔／第三方整合的Session未另行驗證；不要把本機Snapshot測試推廣為所有外部資源皆已恢復。

## 本機範本的驗證責任

`configure_template.py`只驗檔頭、版本、hash並作本機複製；它不連Windows，不檢查Snapshot count，manifest明示 `snapshot_count_verified=false`。

`new_session`需實際載入本機範本副本、讀回零Snapshot及25fps，再另存唯一新路徑。這會套用範本的結構／routing／mixer state；不是清空任意目前Session或factory reset。錯誤範本不能靠修改manifest宣告count0變成有效空白。

需要獨立反證測試時，可在已授權的拋棄式Session做一個未儲存的Cue標記，再load已存檔並驗證標記消失／原內容恢復。這是實際資料修改，不是readonly inspect，不會自動套用到有其他未存工作的Session。

## 部分成功與連線中斷

多包create包含建立、改名與可選timecode。只有完整狀態回讀符合後，該操作才加入 `verified_steps`。中途失敗可能已建立Snapshot，但尚未完成後續欄位；結果需保留：

- `verified_steps`：已確認操作。
- `indeterminate_operation`：可能已執行但結果未證明的操作。
- `after`：可讀取時保留實際失敗後狀態。
- `recovery_stage_path`：仍可能是current Session的暫存檔，恢復前不要刪除。

先用全新result執行inspect，再依實際count／name_hex／時間規劃剩餘步驟。不可盲目重送create／delete；它們改變count與後續index。外層timeout或沒有有效result也屬接受情況未知。

本機長批次曾停止進展，根因未確定。封裝只在已成功回讀的操作邊界重建原廠identity連線，不重播不明操作；不能把它描述成已證明keepalive根因或保證任意長批次自動續傳。

## 變更腳本後

先檢查離線schema、index shift、嚴格編碼、31-byte限制、FF<25、路徑與fresh result。確認plan及不帶 `--send` 的execute不啟動VM／Windows runner／socket。Runtime重測以具體變更為界，在拋棄式Session、獨立輸出路徑及已知baseline上做。

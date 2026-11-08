---
name: digico-native-session
description: 使用原生 TCP 與唯讀狀態回讀，在本機 Windows Quantum1 V2126／25fps 離線編輯器建立範本式 Session、增刪改 Snapshot、設定 RecallAt、儲存與載入；支援 Windows 本機及 Mac Parallels，不使用 OSC 或 UI 自動化。
---

# DiGiCo 原生 Session

使用 `scripts/native_session.py` 操作確切 Quantum1 V2126 離線編輯器。原生 TCP 只連 `127.0.0.1:2028`；唯讀 `ReadProcessMemory` 用於驗證，不用來寫入、注入或繞過原廠 gate。其他 EXE hash、FPS、實體控台及任意遠端主機不屬於已驗證能力。

## 選擇起點

- 編輯目前 Session：以新一次 inspect 的 count、路徑及名稱 bytes 為起點。
- `new_session`：載入本機配置的零 Snapshot 範本，再另存新路徑；保留範本結構與 routing，並非 factory reset。公開版不附 `.ses` 範本，按 [本機空範本](references/operations.md#本機空範本) 準備自己的檔案。
- 指定其他同版 Session：先以獨立 `load` request 載入，再重新 inspect／plan。

Create 取目前 console state，不會自行為每段 Cue 建立不同的 mixer 設定。Load／new_session 會替換目前 editor 狀態；不要把其視為唯讀查詢。

## 執行順序

1. 實際送出時，先讀 [執行環境](references/windows-host.md)，確認既有程序、明確 EXE 路徑、25fps、Network 與全新 result 位置；不可沿用舊 PID。
2. 執行 `inspect`，取得實際 count、name_hex、RecallAt、active flags 與目前 Session 路徑。此模式不連原生 TCP。
3. 按 [request schema](references/operations.md) 寫原始 JSON，填入最新 `expected_count`，先 `plan` 驗證整批 index／編碼／路徑。
4. 對已授權的編輯工作執行 `execute --send`，傳入同一份原始 request；plan 輸出的 `steps` 不是輸入格式。
5. 讀本次新 result，核對 `status="ok"`、`verified_steps` 與 `after`。檔案持久化需另用獨立 `load` request 比對保存前後欄位，見 [驗證與恢復](references/validation.md)。

純離線規劃可直接使用使用者提供的 count，不要求先連 Windows／VM；保留 `runtime_acceptance_verified=false`。`execute` 沒有 `--send` 也只輸出 dry-run，不呼叫 Windows runner。

## 資料契約

Snapshot 名稱上限31個編碼後 bytes，禁止默默截斷、替換或音譯。支援嚴格 `ascii`／`cp950`／`utf-8`；CP950 raw bytes 的儲存與重載已驗證，GUI 顯示及任意編碼解釋不可據此保證。以 `name_hex` 比對。

Timecode 使用25fps的 `HH:MM:SS:FF`，`FF<25`；內部是 quarter frames。保留使用者的 Cue 順序，不自行按全域時間排序。路徑限ASCII Windows本機磁碟絕對 `.ses` 路徑。

遇到 error／timeout／`indeterminate_operation`，先用新的 inspect 回讀；create／delete不是冪等操作，不可盲目重播整批。若有 `recovery_stage_path`，保留該暫存檔，可能仍是目前Session路徑。工具不安裝軟體、啟動VM、改INI／防火牆、操作UI或寫程序記憶體。

需要查封包或版本位址時才讀 [協定與RVA](references/protocol.md)。

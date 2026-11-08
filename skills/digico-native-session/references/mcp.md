# MCP stdio

`scripts/mcp_server.py` 封裝同套 typed CLI，適用已啟動的 exact Quantum1 V2126 Windows 離線編輯器。只用 Python 標準函式庫；stdio 的 stdout 只有逐行 UTF-8 JSON-RPC，診斷送 stderr。不建立 HTTP／TCP server，也不提供 raw packet、任意主機、記憶體位址、OSC 或 UI 工具。

## 啟動

以下是虛構的 client 設定，請替換路徑與既有 VM／EXE：

```json
{
  "mcpServers": {
    "digico-native": {
      "command": "/absolute/path/to/python3",
      "args": [
        "/absolute/path/to/digico-native-session/scripts/mcp_server.py",
        "--vm", "Existing Windows VM",
        "--exe", "C:\\DiGiCo\\Quantum1\\Quantum1.exe",
        "--work-dir", "/Users/YOUR_USER/Documents/DiGiCoWork"
      ]
    }
  }
}
```

Windows 本機同樣啟動 Python adapter；`--vm` 填本機目標標籤，實際 backend 依作業系統選擇。其他 startup options 為 `--process-id` 與 `--timeout-seconds`（預設180，範圍1–600）。工具參數不能更換此目標。Adapter 不安裝或啟動編輯器、不開機 VM、不改防火牆。

預設工作目錄為 `~/Documents/DiGiCoWork`。Mac 必須使用 Home share 可見、沒有隱藏 component 的目錄，供 Parallels `\\Mac\Home` 存取；POSIX 新目錄為0700，既有非 private 目錄會拒絕。每次呼叫產生全新 UUID request/result；失敗保留 artifacts。這些資料可能含私人 Session、Preset 或 Cue，不能隨 skill 發布。

## 工具

| 工具 | 主要參數 | 行為 |
|---|---|---|
| `digico_status` | encoding | 原生 loopback status 讀取，不送 application payload |
| `digico_inspect` | encoding | 唯讀目前 Session／Cue 狀態 |
| `digico_structure` | encoding（ascii/cp950） | 唯讀123-byte Session 結構，雙讀／alias 驗證後本機解碼；沒有結構 setter |
| `digico_inventory` | encoding、channel_id、without_response_tables、include_physical_sources、include_group_outputs | 唯讀實際 native tree、definitions、路由來源；完整資料留 result |
| `digico_meters` | encoding、channel_id、limit | 唯讀 cached meter codes；不是實體音訊驗證 |
| `digico_catalog` | **inventory_path**；channel_id、module、query、writable_only、limit | 純本機查既有 inventory，不連 Windows／VM |
| `digico_presets` | encoding（ascii/cp950）、content_indices | 唯讀 metadata／scope；明確指定 index 才複製其 stored processing content |
| `digico_preset_execute` | **request、presets_path**；inventory_path、send | 單一高階 Preset 操作，預設只規劃 |
| `digico_plan` | **request**；inventory_path、presets_path | 整體驗證與編譯，不呼叫 Windows runner |
| `digico_execute` | 同 plan，另有 send | 預設 dry-run；只有 boolean `send:true` 才執行 |

`channel_id` 是原生 uint16 ID，例如256為0x0100，不是 logical channel1。`catalog.limit` 預設25、`meters.limit` 預設64，範圍都為1–1000。`inventory_path`／`presets_path` 必須是存在的本機絕對 JSON 路徑。大型 inventory、raw definitions 與 stored Preset contents 不塞進 model 回覆；工具回傳摘要、`result_path`，必要時標記 `response_truncated`。

## 規劃、寫入與回讀

Request 沿用 `schema_version:1, fps:25, encoding, expected_count, operations`，其中 expected_count 是目前 **Cue 數**。一般文字用 ascii／cp950／utf-8 的嚴格編碼；Preset 只接受 ascii／cp950，名稱不截斷、不替換。送原始 request，不送 compiled plan。

非 Cue 操作包含 typed control／channel name、語意 input/output routes、Group assignment 與原生 Session export；詳見 [非 Cue 功能](noncue.md)。`set_control.value`／`expected` 都是 **native internal** 值，display_units 只是標籤，不能由 mS 猜單位或自動換算。Catalog 的 definition、enum/range、mode context 與 fresh runner gates 都保留；切換 shape／mode 後要重新 inventory，再規劃相依寫入。

虛構 `digico_plan` arguments（ID、count 與 current 值必須取自自己的 fresh inventory）：

```json
{
  "inventory_path": "/Users/YOUR_USER/Documents/DiGiCoWork/FRESH.result.json",
  "request": {
    "schema_version": 1,
    "fps": 25,
    "encoding": "ascii",
    "expected_count": 0,
    "operations": [{"op": "set_control", "id": "210c000001", "value": -10, "expected": 0}]
  }
}
```

Preset 的七項操作、scope 與 lock 規則見 [Preset](presets.md)。每個 request 只放一個 `preset_*` 操作，並傳 fresh presets_path；Recall 還要求 inventory_path、明確 scopes、verify_control_ids，以及查詢時已複製該 index 的 content。Capture／Update 沒有 verify_control_ids 時，不能把 metadata/content 核對稱為 processing 驗證。

回覆的 `preset_verification` 分別保留 metadata、stored subset、processing、原 scope／原 Preset contents 的結果；audio_delivery_verified 不會因離線值回讀而變成 true。Meter 回覆同樣保留非 atomic、freshness 未知、synthetic-cycle 與 physical_audio_verified:false；cached code 不代表實體 DSP 或輸入聲音。Structure 的 native count slots 不自行推成未證實的 mono／stereo 資源標籤。

## 錯誤與協定

CLI nonzero exit、錯誤／缺少 fresh result、malformed JSON 或 timeout 都回 `isError:true`。`error:null` 是正常結果欄位。Timeout／取消可能發生在操作已開始後；先讀保留 result、inspect 與 fresh inventory／presets，不能盲目重試寫入。取消只停止該 MCP 回覆，不回滾原生操作。

Adapter 一次只執行一個 CLI；子程序 stdin 明確接 DEVNULL，避免消耗下一個 MCP 請求。關閉 stdin 後等待已開始、已有 timeout 的呼叫結束。輸入本機上限2MiB、result 檔128MiB；這些是 adapter 資源限制。

支援官方2026-07-28逐請求 metadata／server/discover，以及2025-11-25 initialize／initialized。沒有宣告 resources、prompts、tasks 或 subscriptions。協定依據：[stdio](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)、[tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)、[versioning](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning)、[legacy lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)。

Exact 離線編輯器已做 MCP discovery、連續 status／meters／presets 與明確 send 的 scoped Preset 操作整合驗證，包含 processing subset、原 Preset contents 保留與原 scope 還原。這不代表實體音訊或所有 control 都已驗證；每次結果仍以該次 fresh result 為準。

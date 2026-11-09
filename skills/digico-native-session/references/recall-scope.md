# 逐 Cue 檢查 Quantum5 Recall Scope 與 Safe

`file-recall-scope` 用本機 Python 3 唯讀分析 Q5 V2025（vM）與 V2242（vO）的 `.ses`。預設遍歷所有 Snapshot 及其保存的 controller records。下例篩選第一個 Cue 和原生通道 `0x0100`。省略任一選項可擴大範圍。Cue index 從零起算，與畫面上的 Cue number 分開。

```sh
SKILL="$HOME/.codex/skills/digico-native-session"
python3 "$SKILL/scripts/native_session.py" file-recall-scope \
  --source /absolute/work/source.ses --snapshot-index 0 --channel-id 0x0100
```

輸出 JSON 到 stdout，預設 CP950，可用 `--encoding ascii` 或 `--encoding utf-8`。需要保存時，請重導向到私人資料夾。報告可能包含通道名稱和整場 Cue。

## 如何讀報告

- `cues[].channels[]` 列出原生通道 ID、該 Cue 保存的名稱和 `parameter_set` 索引。依索引查 `parameter_sets` 可得到參數列表。多個通道共用同一種結果時只存一份列表。
- 參數 key 是 `parameter/module/band` 三個原生 bytes。用 `parameters` 查識別碼和已核對的名稱，例如 `210c00` 是 Fader。未命名參數仍有原生編號。
- `allowed` 表示保存的已解 Scope 與 Safe 條件允許召回。是否送出更新還取決於當下的值、控台與硬體。
- `scope_blocked` 表示至少有一層 Scope 明確排除。`c9_group` 是通道群組，`d2_selected_section` 是目前套用的 Section，`cue` 是 Cue 參數遮罩。模組和控制器的個別排除旗標分開列出。已知同時啟用的 Safe 也會保留在理由中。
- `safe_blocked` 表示已辨識的 Safe 會擋住參數，例如 `full_safe`、`fade_mute_safe`、`inserts_safe` 或 `destination_aux_safe`。
- `unknown` 表示現有檔案不足以判定，例如缺少目前控制器、缺少 Safe、未辨識的模組或只存在執行環境的 `DontMirrorAltInput`。報告不會將未知視為允許。

`file-compare` 也會在左右兩份的 `recall.cues` 列出 `saved_scope_status_counts` 及 `saved_scope_input_fader_status_counts`。舊的 `input_fader_candidate_after_saved_safes_count` 只涵蓋個別排除與 Safe 兩層，應以新的全域與 Cue Scope 結果判斷。

## 已解的儲存結構

| 層級 | 欄位與判讀 |
|---|---|
| C9 通道群組 | metadata 的 `+0x21` 是 32-bit 模組 Recall mask，`+0x29` 是 256-bit 參數 Recall mask。原生對參數 ID 0 略過群組參數位元。立體聲右腿查對應的主 bus 群組。實體群組依 Load 規則重置參數遮罩。 |
| D2 Section／Cue 群組 | ConsoleRec `+0x01` 的 bit `0x04` 保存 Section 功能狀態。C9 Channel `+0x54` 為 Section ID。D2 每個 Section 佔 72 bytes，Recall 模組和參數遮罩分別在 `+0`、`+8`。Cue 指向 Snapshot Group 時，使用該群組從 `+0x20` 起的 Section 設定。 |
| D2 Cue | 992 bytes 為模組 1–31 各 32 bytes 的參數 bitset。索引是 `(module-1)*32 + parameter//8`，位元是 `parameter%8`，band 共享同一位元。CG membership 和 Gang 的四個位元另受 Cue flags 的 Misc Scope 設定覆寫。 |
| Saved controls | 一般路徑還檢查模組與控制器旗標 `0x04`。已辨識的 Output Socket 有獨立路徑。模組 `IgnoreScope` 會繞過一般 Scope 與 Safe，但原生 Section 模式的 Fader 規則仍先套用。 |
| D3 Safe | 依目前保存的 Full、分類、Mustard、Aux 節點與 Aux 目的地 Safe 判讀。Save 不含可用值時回報未知。 |

解析依兩個 Q5 版本的原生 Store、Load 與 Recall 分支核對。Section 判斷採檔案保存時的模式，載入另一台控台時仍可能依目標設定轉換。實際觸發、外接設備狀態和音訊行為不在此唯讀流程的驗證範圍內。

以原廠執行檔的 preferred-image VA 核對時，可用下表的入口。

| 原生方法 | V2025 | V2242 |
|---|---:|---:|
| `tGroup.Load` | `0xB1EAE8` | `0xBD2334` |
| `tSnapshotList.InGlobalRecallScope` | `0xA4FBC0` | `0xB0099C` |
| `tModuleSnapshot.Recall` 的 Cue mask 檢查 | `0xA47923` | `0xAF8687` |
| `tChannel.ControllerSnapshotSafe` | `0xB25948` | `0xBD9308` |

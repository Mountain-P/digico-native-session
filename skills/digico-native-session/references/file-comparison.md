# 唯讀比對兩份 Quantum5 場次

`file-compare` 自動辨識 Q5 V2025（vM）與 V2242（vO）。兩份可同版或跨版。只需要 Python 3 和使用者選定的兩份本機 `.ses`。V2025 是檔案唯讀 profile，適用於 `file-compare` 和 `file-inspect`。

```sh
SKILL="$HOME/.codex/skills/digico-native-session"
python3 "$SKILL/scripts/native_session.py" file-compare \
  --left /absolute/work/source-a.ses --right /absolute/work/source-b.ses
```

預設以 CP950 解碼名稱，可用 `--encoding ascii` 或 `--encoding utf-8`。命令把 JSON 送到 stdout。需要留存時，重導向至私人資料夾中的全新報告檔。場次和報告都可能含私人名稱、訊號分配或整場 Cue，公開文件與測試只使用合成資料。

## 閱讀報告

1. 先看 `left`／`right` 的版本、通道結構、時鐘，以及 `differences` 的變動。對應通道使用 C9 group ID 和兩側唯一且完全相同的名稱 bytes，對應 Cue 使用兩側唯一且相同的原生編號和名稱 bytes。重名與只出現在一側的項目分開列出。比較結果中的通道對應是候選關聯，通道名稱相同仍可能接到不同音源。
2. 看 `recall.current_safes`、`recall.snapshot_list_options` 及 `recall.cues`。Cue 包含 Crossfade、Input Fader 排除筆數和逐層套用保存遮罩後的分類計數。Scope 指紋可核對區塊是否相同。Safe 計數按原生 channel ID 統計，包含獨立保存的右聲道 bus leg。用 `file-recall-scope` 查逐通道、逐參數的理由。[Recall Scope 報告](recall-scope.md)
3. 看 `signal.current_channels` 的 Main／Alt、Insert A／B、已開啟的 Aux send（含 tap 和 dB）及 Group 路由，再看 `signal.cue_deltas` 的相鄰 Cue 差異和 `signal.current_vs_selected_cue` 的存檔目前混音差異。Cue 比對以兩邊都保存的欄位為準，單側欄位獨立計數。通道和接點名稱取自檔案目前的 C9。每個 Cue 的差異明細有上限，未列出的筆數會標明。
4. 看 `automation.macros` 的 Trigger 與命令。已辨識的 Smart Key、Main／Alt、Insert 開關與 Aux send 開關用語意欄位表示。Macro 的 Input 範圍使用邏輯通道號，Aux 目的地使用原生 band。其他命令保留 raw bytes。F8 section 缺席時回傳 `null`。

要專看某條訊號路徑，可給兩側各自的原始通道 ID，例如 `--left-channel-id 0x100 --right-channel-id 0x101`。這兩個選項只縮小 Signal 報告，仍會讀取完整 Cue 和 Macro 設定。較大的檔案若超過展開分析限制，可用通道篩選縮小訊號比對。

跨檔的 `current_signal_rows` 列出配對通道的差異欄位，原生接點 ID 或目的地名稱改變都會算入。`current_safe_rows` 比較兩側讀到的啟用項目。Macro 依唯一且相同的名稱 bytes 對應，並比對已解析的命令目標。結果中的 index 指向各自的 `automation.macros.records`。報告保留左右兩側的已解析資料，以便追查接線或命令的細節。

## 可驗證的範圍

工具唯讀解析 C9、D2、D3 和 F8，E3 存在時另讀音訊同步。V2025 與 V2242 分別採 88／121-byte C9 Channel metadata。解析器辨識已核對的遮罩、旗標、跨段索引和共享模組。任何 D3 Safe、Macro 或 D2 Cue 值都代表檔案保存的狀態。實體控台的載入、Recall、Macro 執行與音訊尚未由此流程驗證。

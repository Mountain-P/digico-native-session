# 中斷紀錄與恢復

每次 Windows runner 使用全新的 `<result>.journal.jsonl`。它在傳送前記下步驟與封包開始、在 socket write 返回後記錄返回，最後才記獨立回讀已確認的步驟。`packet_send_returned` 不能代表編輯器已接受。

每行保存 exact UTF-8 payload 的 SHA-256 與前一行 digest，逐筆要求 `Flush(true)`。這能檢查意外損毀與已觀察前綴，不是數位簽章，也不保證 Parallels shared-folder 在主機斷電後仍持久化。尚未換行的末筆沒有完成權威；完整但損毀的行會拒絕，不能略過。

```sh
python3 "$SKILL/scripts/native_session.py" recover \
  --journal "$WORK/execute-001.json.journal.jsonl" \
  --compiled-request "$WORK/execute-001.json.request.json" \
  --output "$WORK/recovery-001.json"
```

此命令只讀本機檔案，列出已確認前綴、尚未確認的步驟與是否必須重新取得狀態；不開 Windows、不連網、不產生重送請求。請求檔必須是該 runner 的 compiled `.request.json`，不是原始操作輸入。

出現 timeout、runner 消失或 `indeterminate_operation` 時，保留 result、request、journal、runtime stage 與 authoring work directory。先用全新的 inspect/inventory 讀實際目標，再自行產生剩餘操作。Create/delete 會改 count/index；單純重送可能重複或刪錯項目。

Authoring 的各階段不是交易，也不提供自動 rollback。執行過的先前階段可能已生效；mode 變更後必須取新 inventory，再編譯下一階段。未完成的階段有自己的嘗試路徑，即使沒有 result，也要查看其 request/journal。參閱 [authoring](authoring.md) 與 [Windows 寫入鎖](write-lease.md)。

Journal 與完整結果可包含 show 名稱、時間碼、路徑、控制值與封包；只存於自己的工作目錄，公開文件只用合成資料。

# Build profile 與改版驗證

版本相容性以實際 EXE 的 SHA-256 與 profile ID 決定。產品名稱、檔名、相同 TCP port 或相同版本號，都不能代替這項比對。0.3 保留舊 Q1，並加入以下六個新版 exact profile；沒有符合紀錄的 EXE 仍拒絕。

## 隨附 profile

| ID | 離線編輯器 | `status` | 控制寫入 | Routing／Preset／transfer |
|---|---|---|---|---|
| `quantum1-v2126` | Quantum1 V2126 | 已驗 | 原有 typed／domain 契約 | 保留原有範圍 |
| `quantum1-v2242-4` | Quantum1 V2242.4 | 已驗 | exact allowlist | 關閉 |
| `quantum2-v2242-4` | Quantum2 V2242.4 | 關閉 | exact allowlist | 關閉 |
| `quantum3-v2242` | Quantum3 V2242（326／338） | 關閉 | exact allowlist | 關閉 |
| `quantum5-v2242` | Quantum5 V2242（升級 SD5） | 關閉 | exact allowlist | 關閉 |
| `quantum7-v2242` | Quantum7 V2242（`SD7Q.exe`） | 關閉 | exact allowlist | 關閉 |
| `quantum8-v2242` | Quantum8 V2242 | 已驗 | exact allowlist | 關閉 |

這些 profile 均開放 `inspect`／`inventory`／`structure`／`meters`、Snapshot／25fps、SaveAs／Save／Load 與自己的零 Snapshot 範本。新版 allowlist 只納入逐 build 已回讀並恢復的 numeric／boolean prefixes，以及獨立驗證的 Input-name entry；boolean 另需明確 enum domain，不沿用舊 Q1 全部控制，也不由相似名稱新增其他 boolean／packed 操作。Q5、Q7 的 TCP2029 不在支援範圍，不能由 control TCP2028 正常便推定 status 可用。

Quantum5 僅涵蓋升級 SD5；Quantum7 `SD7Q.exe` 不是一般 SD7。Quantum638 尚未找到可核對的現行官方 offline download，維持未解決，不能替換成 Q8 profile。實體控台與實際音訊未驗證。

六個新版均已通過未修改公開 Windows runner 的隔離 typed 回歸：範本 Load／SaveAs、Snapshot 編輯、未存標記後的 alias Reload，以及代表性控制／Input name 保存重載。控制另驗 Value／Output、名稱與 Snapshot raw；[驗證文件](validation.md) 區分 runner 證據及 CLI／MCP 傳輸整合。Profile 的 `validation` 記錄與功能開關不等同每個 channel／mode 已完整掃描；使用時仍看當次結果。

```sh
python3 skills/digico-native-session/scripts/native_session.py profile \
  --build-profile quantum1-v2242-4
```

`profile` 只讀隨附 metadata，回傳 exact executable identity、validation 與 features，不啟動 VM 或驗證目前程序。未指定 ID 時顯示舊 `quantum1-v2126`。新版 raw request 可明列 `build_profile`；`execute --build-profile ID` 也能為省略該欄位的 raw request 選擇 build。明確的 request profile 或 inventory／Preset identity 與 flag 不同時，dry-run 與 send 均拒絕。`plan` 沒有這個 CLI flag，使用 request／artifact identity；皆無才預設舊 Q1。帶 inventory 的 authoring 從該 inventory 選擇，不能另加未知 request 欄位，`author-execute --build-profile` 只核對一致性。

`assets/build-profiles/*.json` 集中保存 executable identity、runtime globals、VMT／callback／definition 位址、record layouts、原生 enum、routing selector、模式範圍與已驗證的單位契約。`addresses.hex_*` 是以首個支援版本位址命名的穩定查找鍵；值才是所選 build 的實際 RVA／證據 VA，不能把鍵名當成新版本位址。

每個新 plan、step、control／route／group／Preset binding 都帶 `build_profile` 與 `executable_sha256`。讀取舊結果時可以省略 profile ID，但仍須有可識別的 exact hash；外層與內層 inventory／contract 若不一致即拒絕。未知 hash 不會退回預設 Quantum1 profile。

## Python 接線

```python
from build_profiles import select_profile, use_profile, profile_identity

profile = select_profile(actual_executable_sha256, requested_profile_id)
with use_profile(executable_sha256=actual_executable_sha256):
    # 支援模組在此 context 讀同一份 profile；不修改共享全域常數。
    identity = profile_identity()
```

具版本語意的公開 planner／normalizer 接受選填 `build_profile=`。帶 inventory 的 helper 會從其 hash 選擇 profile，並核對其他輸入。沒有 inventory 的離線規劃可以選擇 profile ID；這不證明目前程序符合，也不代表 runtime 接受。實際執行仍由 runner 對 EXE 重新計算 hash。

`load_profile_record(path)` 只讀候選研究資料，不註冊、不選取，也不授權 native 操作。`select_profile` 只接納隨附、標記已完成離線 runtime 驗證且有對應 helper implementation 的紀錄。`features` 是逐功能開關，不能由一項成功推定整個產品相容。

`quantum-layout-v2` 是資料驅動的 layout implementation ID，本身不會接納任何新 EXE。其 `control_write_allowlist` 以三個 raw ID bytes、datatype、definition RVA、owner VMT RVA 與 callback RVA 全部 exact match 才開放指定控制；缺少、`null` 或空清單均拒絕寫入。舊 `quantum1-v2126-layout-v1` 缺少／`null` 清單則保留原有型別與 domain 驗證。這項清單不取代 response bounds、模式 context 或 fresh RPM binding。Channel name 屬 datatype 4，沒有獨立核准 entry 就不會隨 fader 寫入一併開放。

Inventory 已驗證而 `control_write=false` 的 profile 可以產生 catalog，但所有控制仍為唯讀。已知需要模式限制的 EQ Q／Dynamics time／Delay 控制若沒有該 build 的 context，也保持唯讀；不以一般 response bounds 代替遺漏的原生限制。

語意 alias 另由 `authoring_aliases` 控制。新版只在 exact profile 明列時開放已證實的 `fader` alias；其他已 allowlist 控制可使用 catalog 的 `native:<exact name>` 或完整 ID。Alias、可寫控制與工程單位是三份不同授權，不能互相推定。詳見 [authoring](authoring.md)。

v2 無 seed 的 datatype 0 另須有顯式 `control_enums` 限制，不沿用舊版本的 default `{0,1}` 或 EQ 特例。成功測試兩個狀態只能證明該測試子集；例如 Aux pre/post 可能有更多 selector 值，不能因此一併開放。

## PowerShell 接線

Runner 先設定自己的 `$SkillRoot`，以既有 ScriptBlock 載入方式 dot-source `native_profile.ps1`，再呼叫：

```powershell
$null=Set-NativeBuildProfile -ExecutableSha256 $actualHash -BuildProfile $requestedProfile
```

這一步須在任何 native RPM／dispatch 之前。未指定 profile ID 時仍必須用實際 hash 查唯一紀錄。選擇失敗會清空前次選擇。支援模組以 `Assert-NativeBuildFeature` 與 `Assert-NativeBuildContract` 核對功能、profile、hash；更新套件或 staging 時必須包含 profile JSON。

私有研究工具可明確呼叫 `Set-NativeBuildProbeProfile -Path <candidate.json> -ExecutableSha256 <actual-hash>`，使用候選的完整 layout 做唯讀 inventory。此路徑強制關閉所有寫入功能，輸出 `probe_read_only=true`、`writes_admitted=false` 與原始驗證階段；不會把候選加入正常 registry。缺少完整 layout／field bounds、VMT／owner／definition 不符或結構在讀取期間改變時，應保留錯誤與研究資料，不得改標已支援。

## Layout 與單位

Controller 的 owner、AudioID、Restore 與 record 長度，以及 Channel、ControllerLibrary 等欄位，均由選定 layout 提供。物件 layout 的 `size` 是 collector 所需的讀取窗口／最低 instance 大小；實際 class 大小另由 VMT 取得，部分 socket 物件只讀其前段欄位。新增 build 必須核對實際 RTTI／handler 與讀取窗口，不能只替換 RVA。完整 definition raw bytes 必須保留；尾欄增加也不能截成舊長度。

v2 必須另有已保存、穩定的 controller CurrentResponseSeed／LUT 指標。缺少證據，或非零指標與 definition 不同時，目前保持唯讀；definition seed 的 bounds 不能冒充未驗證的 effective domain。Plan 綁定這些指標，Windows 在送出前及回讀時重新比對，指標變動即拒絕沿用舊 plan。較早 inventory 沒有這些欄位，必須重新讀取。

`verified_identity_units` 僅對 profile 中明列的 controller／owner callback／datatype／response curve／原生 descriptor／範圍同時吻合時附加。它允許已驗證的 dB 或 Hz identity quantity，不是任意 display label 的換算器，也不解除缺少 bounds 或模式 context 的唯讀限制。Fine delay 的 samples 不會因畫面標籤而被當成毫秒。以上均不代表實體 DSP 或音訊輸出已驗證。

## 每個 build 自己的範本

`session_file.template_filename` 與 `session_file.manifest_filename` 是該 profile 的本機範本檔名；新版各自唯一，不共用舊 Q1 的 manifest。匯入新版時必須選對 build：

```sh
python3 skills/digico-native-session/scripts/configure_template.py \
  --build-profile quantum1-v2242-4 --source /absolute/work/synthetic-empty.ses
```

公開包不附 `.ses`、installer、EXE、DLL 或本機 manifest。`configure_template` 只驗該 build 的 header／版本與檔案 hash；`new_session` 仍須原生 Load、讀回 count0／25fps，再 SaveAs 到新檔。它保留範本結構／routing。不能以其他型號範本、改 header 或修改 profile 來跨版本載入。

## 與執行及 Mirror 的關係

Runtime staging 包含整份 profiles，Mac 的短 launcher 先驗 SHA，再執行含 receipt／resource／request 驗證的 launcher。Host target lock 與 guest writer lease、endpoint PID ownership、fresh Session／FPS／console 比對及每步回讀，均與 build admission 並行；它們不會放寬 feature 或 allowlist。部分結果與 journal 保留給 [本機 recovery](recovery.md)，不能用來自動重播。

Profile 的控制寫入／transfer 開關不授權完整 Mirror。公開 passive parser 仍以已解的協定版本為限，`initial_sync_complete=false`；SessionVerify 的新舊 checksum／字串差異及未解欄位不能猜。見 [Mirror 研究](mirroring-research.md)。

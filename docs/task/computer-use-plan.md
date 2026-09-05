# CoWorker OS Computer Use — 內置能力開發方案與落地記錄

> 本文檔基於對 CoWorker 現有架構的完整代碼調研，以及對業界標杆本地 agent（OpenAI codex、openclaw）computer-use 行為的對照調研，給出並落地了「Computer Use 作為一等公民內置能力」的完整方案。
>
> 調研對象：CoWorker 本倉（backend + electron + frontend）、`codex`（codex-rs Rust 工作區）、`openclaw`（TS/Node monorepo）、Anthropic/OpenAI computer-use 規範。
>
> 狀態：已實現（macOS 為首要平台），含 17 個新增後端單元測試；全量 412 個後端測試通過，前端可建置，Electron 冒煙通過。

---

## 一、定位：Computer Use vs MCP vs Browser vs Terminal

對 CoWorker 這類「本地桌面 agent」，正確定位是把每種工作丟到最小干擾面：

```
Terminal (run_command)   無 GUI 的 CLI/headless 工作（既有）
Browser  (內嵌 webview)  有 GUI 的網頁工作；已是「隔離平行面」（既有）
Computer (本方案)        原生 App / 無 API 的長尾 GUI 工作（新增）
MCP                      外部系統結構化能力接入（既有）
```

* **MCP 是廣度**（把外部系統接成工具）、**Computer Use 是深度**（沒有 API 的 App 用像素/事件級操控）。兩者互補，都在產業主潮流上（Apple Siri 控整支手機＝平台 Owner 用 OS 原生能力做 Computer Use）。
* **真·雙軌並行**只存在於隔離面：CoWorker 的內嵌瀏覽器已是 agent「自己的」Chromium，用戶可用自己的 App，兩者互不搶滑鼠。OS 級原生控制共享唯一滑鼠/鍵盤，採「序列化 burst + 人為讓位/搶占」模型（見 §四）。

## 二、業界標杆調研 → 採納原則

| 來源 | 調研結論 | CoWorker 採納 |
|---|---|---|
| codex | 不實作 OS 級 CU，靠外部 plugin + Guardian 多模態審查/證據；per-turn exposure；審批 profile；base64 永不進 tool text | ① OS 控制不開給子代理；② 動作＋截圖證據留在 session 記錄與 `computer/` 截圖目錄；③ 能力需「顯式 enable」才 mount（預設關）；④ 截圖走原生 image block / 落盤 |
| openclaw | OS 控制全委派外部；權限集中 native 殼（TCC）；截圖降檔 JPEG；無 vision 用副 model 描述 | ① 截圖固定降檔（max_width=1024, JPEG q60）；② 無 vision model 落盤給路徑；③ TCC onboarding 由 Electron(native) 探測與引導 |
| Anthropic/OpenAI CU 規範 | 座標標準化、動作集最小、動作後驗證 | 座標契約（shot-space→display points）、最小動作集、observe→act→驗證循環 |

## 三、目標架構（已落地）

完全複用 browser bridge 模式，新增獨立通道（computer 可點擊/輸入全桌面，**絕不**與低權限面共用 channel）：

```
Electron main: electron/desktop-controller.js
  DesktopController
    ├─ displays      screen.getAllDisplays()  (含 bounds/scaleFactor)
    ├─ screenshot    desktopCapturer → 固定降檔 JPEG（保長寬比）
    ├─ input         @nut-tree-fork/nut-js（in-process；macOS CGEvent）
    ├─ pause         powerMonitor 鎖屏/睡眠自動讓位
    ├─ abort         globalShortcut Cmd/Ctrl+Shift+Esc + tray「Emergency Stop」
    └─ state         platform / permissions(accessibility, screen) / frontmost app
         │
         loopback HTTP 127.0.0.1 隨機 port + bearer token（獨立 second bridge）
         │
Python backend: coworker/computer/bridge_client.py
    ├─ ComputerClient（httpx，讀 .coworker_settings.json 的 computer_bridge key）
    ├─ computer_observe（read-only：state/displays/screenshot）
    ├─ computer（mutating：click/double_click/right_click/move/drag/scroll/
    │             type/key/clipboard_set/paste）
    └─ computer_capability_line → SystemAssembler 注入 system prompt
```

**座標契約**：`computer_observe` screenshot 回傳 `shot{width,height}` 與 `display{bounds}`；model 讀圖上的像素座標，於 `computer` 回傳 `display` + `shot_width/shot_height`；Electron 以
`pointX = bounds.x + (x / shot.width) * bounds.width` 換算（保長寬比降檔 → 對 Retina 精確）。`shotToPoint` 為純函數、含單元測試。

**共用層**：`coworker/bridge_common.py`（BridgeInfo、設定檔讀寫、截圖 data-url 驗證/落盤、`LoopbackBridgeClient`）。browser bridge 保留既有實作；computer bridge 使用共用層，避免新面與舊面漂移。

## 四、安全模型（對齊「默認/完整」兩級權限）

| 工具 | 默認權限 (guarded) | 完整權限 (autonomous) |
|---|---|---|
| `computer_observe`（讀） | auto | auto |
| `computer`（每次注入動作） | **逐動作 HITL 審批**（`hitl.py`）| auto |

### macOS 權限：被動自動觸發與「取消後重置」

macOS 的 TCC 權限**沒有**第三方可呼叫的「允許/不允許」系統對話框 API。正確做法是**被動自動**——用到才觸發，並按狀態分流：

| 狀態 | 行為 |
|---|---|
| `not determined` | **自動彈出**：Screen Recording 在 App 首次真實截屏時由 macOS 自動彈出同意 alert；Accessibility 由 `requestAccess` 呼叫 `askForAccessibilityAccess()` 自動帶出「輔助功能」設定頁（部分 macOS 先彈開啟設定 alert） |
| `denied`（用戶取消） | **OS 不會再自動彈**（TCC 記住決定）。工具遇 `screen_permission`/`input_permission` 時自動呼叫 `/permissions/request` → Electron 用深鏈 `x-apple.systempreferences:…Privacy_ScreenCapture\|Accessibility` 開到**精準設定頁**，agent 提示用戶「把 CoWorker 開關關掉再開」重置 → 回到 `not determined` → 下次用到時 OS alert 自動再現 |
| `authorized` | 綠，可操作 |
| `restricted` | 受系統/組織管理，App 無解 |

實作：
* `electron/desktop-controller.js`：`requestAccess(kind)`（screen=真實截屏觸發 OS alert；accessibility=`systemPreferences.isTrustedAccessibilityClient(true)` 觸發 AX prompt；denied 時先嘗試 AX prompt、仍拒絕則自動深鏈開設定頁）、`openPermissionSettings(kind)`
* bridge 路由：`POST /permissions/request`、`POST /permissions/open-settings`
* Python：`ComputerClient.request_permission/open_permission_settings`；工具在權限錯誤時**每種權限自動 request 一次**（mount 工具集 once-guard），提示語按 returned status（granted→重試 / prompt_shown→請用戶按允許 / denied→已開設定頁請重置 / restricted→受管理）回給 agent
* `DesktopController.act` 注入前 preflight：macOS 上非 Accessibility-trusted 時直接丟 `input_permission`（libnut/CGEvent 在無權限時是「靜默失敗」，不檢查會假成功、永不觸發提示）

> 授權觸發完全由 agent「用到時」自動進行，**不在 Settings 放權限 UI**。agent 遇上權限不足會在對話中給出清晰指引；用戶按 OS 彈窗允許後回覆一句（如「好了」）即可繼續。

與權限正交的硬防護：
1. **主開關 `computer_use_enabled`（預設 OFF）**：`computer_feature.py`；Off → 工具完全不 mount，model 看不到。環境變數 `COWORKER_COMPUTER_ENABLED` 可 code-level bypass（測試/進階用）。
2. **權限先決**：macOS Accessibility（輸入）+ Screen Recording（截圖）。缺權限回 `screen_permission`/`input_permission` 並引導用戶到 System Settings；agent 被告知不要重試。
3. **人為搶占**：`powerMonitor` lock/suspend/shutdown 自動暫停，unlock 恢復；`Cmd/Ctrl+Shift+Esc` 或 tray「Emergency Stop Computer」立即暫停並拒絕後續注入（`computer_paused`）。
4. **子代理排除**：`computer`/`computer_observe` 列入 `_CHILD_EXCLUDED_TOOLS`，且 delegation 不傳入 → worker/委派子代理永不觸碰 OS。
5. **審計/證據**：工具呼叫（name/input/output_full）進入 session 記錄；截圖另存 `data_dir/screenshots/<session>/shot-*.jpg`（與內嵌瀏覽器共用 per-session 目錄，session 刪除時一併清理）供回放。

## 五、Phase/工具集接線

* `computer_observe` → `_READ_ONLY_TOOLS`（discuss/execute 皆可用，僅 desktop+開關）
* `computer` → `_EXEC_TOOLS`（僅 execute）；phase gate 在 execute 才暴露
* HITL `interrupt_on["computer"]`：`when = phase==execute && autonomy!=autonomous`（與 MCP destructive ladder 同構）
* runtime graph cache key 加入 computer tool names，避免跨權限快取誤用

## 六、UI / 設定

* Settings（Agent 群組）新增「啟用電腦操控」toggle → `POST /settings` 持久化 `computer_use_enabled`（預設關）。
* 依你的決定**不建專屬監看 panel**：用戶直接看實體螢幕即可監看；tray 提供暫停/緊急停止。
* 語系：en/zh/zh-TW/zh-HK/ja/ko/de/fr/es/pt-BR/ru 已補 `settings.computer_use_enabled(_desc)`。

## 七、測試與驗證（已執行）

* `backend/tests/test_computer_use.py`（17 個）：主開關預設 off/持久化/env override；capability status 三態；resolve 門檻；observe state/screenshot vision 與非 vision 落盤/空截圖拒絕；act ok/paused hint/座標傳遞；HITL guarded 審、autonomous 放、observe 不 gate。
* 既有回歸：`tests/` 全量 412 passed。
* 前端 `npm run build` 成功。
* Electron 冒煙：`[computer] bridge listening on 127.0.0.1`、nut.js 載入無誤、無崩潰（dev 無後端時註冊重試為預期）。

## 八、已知限制與跨平台

* **macOS**：CGEvent（nut-js）+ desktopCapturer；需 TCC 權限（一次設定）。
* **Windows**：同一套 API 走 SendInput；Screen Recording 免 TCC。尚未實機驗證（列 v1.1）。
* **Linux**：nut-js 需 X11（Wayland 限制）；AppImage 環境變數與權限模型待驗證（列 v1.1）。
* 多顯示器：座標換算按 display bounds 精確；nut-js 全局座標空間以主顯示器為原點，次要顯示器為正偏移——單元測試覆蓋換算數學，實機多螢幕待驗。
* 每個 screenshot 為 token 成本，預設 max_width=1024、單步一張；無 vision model 自動落盤不進 context。

## 九、相關檔案

| 檔案 | 角色 |
|---|---|
| `electron/desktop-controller.js` | DesktopController（capture/input/pause/permission/coords） |
| `electron/main.js` | computer bridge server、註冊 `/api/computer/bridge`、tray、熱鍵、powerMonitor、設定 IPC |
| `backend/coworker/bridge_common.py` | 共用 bridge 基礎（BridgeInfo/截圖/設定/httpx client） |
| `backend/coworker/computer_feature.py` | 主開關（預設 OFF + env bypass） |
| `backend/coworker/computer/bridge_client.py` | ComputerClient + `computer_observe`/`computer` + capability |
| `backend/coworker/computer/__init__.py` | 套件 re-export |
| `backend/coworker/agent/graph.py` | tool mount + capability 注入 |
| `backend/coworker/agent/runtime.py` | resolve computer tools / capability line |
| `backend/coworker/agent/core.py` | phase 工具集 + 子代理排除 |
| `backend/coworker/agent/middleware/hitl.py` | computer 逐動作審批 |
| `backend/coworker/api/settings.py` | `/settings` 開關 + `/api/computer/bridge` 註冊 |
| `backend/tests/test_computer_use.py` | 新增測試 |
| `frontend/...`（App/SettingsView/chatService/electron.d.ts/locales） | 開關 UI |
| `package.json` | 依賴 `@nut-tree-fork/nut-js` |
| `electron-builder.config.json` | `asarUnpack` nut-js native module |

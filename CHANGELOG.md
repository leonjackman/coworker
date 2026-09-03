# Changelog

本文件記錄每次發布的重要變更。每個 `## <版本>` 標題須與 git tag（去掉開頭 `v`）對齊，
例如 tag `v0.5.0` 對應 `## 0.5.0`。發布工作流程會擷取對應版本區段作為 Release 說明；
若找不到精確版本，則退回 `## Unreleased`；兩者皆無時才退化為自動生成的 commit 清單。

## 0.5.0

本版本聚焦於「目標模式」這一全新能力，以及一系列根治「降智」、強化上下文預算與執行期穩健性的改進。

### 新增

- **目標模式（Goal Mode）**：用 `/goal <目標>` 設定持久目標，Agent 在無需額外輸入的情況下自動連續多輪推進，直到標記為 `complete` 或 `blocked`；任務面板常駐進度卡片（目標、狀態、已用時間、token 預算條），並支援 pause / resume / clear 控制。目標嚴格按會話隔離，HITL 審批會暫停迴圈並於批准後續跑。
- **32 個模型廠商預設**：內建 OpenAI、Anthropic、Google Gemini、DeepSeek、Qwen / DashScope、Moonshot (Kimi)、Zhipu (GLM)、Doubao、Minimax、Cohere、Groq、xAI、Mistral、Ollama、vLLM、OpenRouter、SiliconFlow 等廠商模板與圖標；同時仍支援自訂 OpenAI 兼容端點。提供商配置已集中重構為 `providers/` 套件，並補上 agent 級 fallback 提供商與 `max_output_tokens` 預設。
- **日誌與可觀測性**：結構化 `app.log`（JSON）、`agent_trace.jsonl`、`tool_audit.jsonl`、`command_approvals.json`，以及可重放的 sub-agent `worker_events/`；新增執行期日誌等級 / 輪轉 / JSON / 請求日誌 API（敏感參數脫敏），以及 `session_id` 關聯，便於單會話追蹤。
- **Electron 桌面端與待辦 Dock**：支援桌面端運行，並新增待辦 dock 組件（含 Goal 進度卡片）。
- **目標能力可開關**：於設置頁提供 toggle，並可透過環境變數 bypass。

### 改動

- **根治「降智」問題**：統一系統提示詞裝配為 `SystemAssembler`；workspace 暴露 + 工具註冊表 + 行為模式注入；移除 `CONTEXT.md` 靜態目錄樹（只保留穩定專案資訊）；系統提示詞去重（workspace / 工具目錄只注入一次），修復普通對話降智。
- **記憶與上下文預算**：記憶注入改為 token 預算索引 + 作用域掃描；新增 token 預算護欄，對工具結果 / 附件 / 搜尋結果進行裁剪；壓縮摘要跨輪次持久化，失敗時優雅降級。
- **CJK token 計數**：擴展 CJK 範圍、將快取 token 納入校準、修正估算器，提升上下文用量統計準確度。
- **記憶自動提取一步到位**：移除多步 extract → stage → consolidate → verify，簡化為單步自動提取。
- **middleware 模組化**：將單體 `middleware.py` 拆分為模組化套件；MCP 歸屬併入 `SystemAssembler`。

### 修復

- **目標迴圈守衛**：新增進度感知的空閒迴圈守衛，遞迴超限時優雅處理；修復目標續跑空轉停止（根治降智無限續跑）；修正 loop guard 與目標輪次流程的 session 狀態處理。
- **執行期穩健性**：圖編譯快取、背景命令、崩潰恢復；resume / rerun 正確傳入 `session_id`，skills 改為不可變 dataclass。
- **串流與持久化**：委派 / 引導 buffer 清理與流事件全量緩衝；assistant 訊息冪等更新與工具輸出顯示截斷；`output_full` 透傳；工具呼叫 continuation 按名稱回退路由。
- **插話與卡死兜底**：插話流活性偵測、卡死兜底、佇列禁用態。
- **read_file 分頁**：分頁讀取 + 歷史工具呼叫重建 + 工具輸出落盤。

## 0.5.1

本版本根治「降智」的底層根因，並修復其引入的一處解析崩潰。

### 修復

- **根治「降智」根因（歷史工具呼叫回放）**：修復 `prepare_agent_messages` 在送模型前剝除 `role="tool"` 訊息與 assistant `tool_calls` 鍵的問題——此前跨輪歷史只剩純文字敘述，模型會模仿「只敘述不執行工具」（實例：會話連續 3 輪輸出「先查看當前狀態，再一次性 commit：」後即停）。現歷史中的 `assistant(tool_calls) → tool(result)` 序列完整回放，與 opencode / codex 對齊。
- **修復歷史回放解析崩潰**：對早期「工具 input 捕獲為空」遺留的空/非法/非物件參數統一校正為 `{}`，避免 LangChain `convert_to_messages` 對空 `arguments` 執行 `json.loads` 拋 `Expecting value: line 1 column 1 (char 0)` 打崩整輪。

### 品質

- 新增工具歷史回放管線測試（含 2d8080e8 降智會話回歸、空/非法參數回歸）；修正 2 個既有測試的錯誤斷言（錯查檔案 / 讀錯 `loop_reason` 鍵）。

## 0.5.2

本版本為快捷鍵體系：新增全局快捷鍵註冊表與設置頁，支持改綁與停用，並讓 Esc 承擔「關彈窗 → 返回上級 → 停止生成」的分層語義。

### 新增

- **快捷鍵設置頁**：設置 → 快捷鍵，列出全部全局快捷鍵；每項可「改綁」（錄製新組合鍵，含衝突檢測）與停用，改綁後顯示「重置」按鈕。
- **全局快捷鍵註冊表**：`Cmd+.` 切換 Plan / Build、`Cmd+Enter` 插話、`Esc+Esc` 停止生成、`Cmd+L` 聚焦輸入框、`Cmd+Shift+U` 附加文件、`Cmd+Shift+R` 重新生成、`Cmd+Shift+E` 編輯最後一條用戶消息、`Cmd+Shift+C` 複製最後回覆、`Cmd+N` 新建對話、`Cmd+Shift+N` 新建項目、`Cmd+/` 切換自主度、`Cmd+1..4` 切換提供商/MCP/技能/記憶視圖、`Cmd+B / Cmd+\\ / Cmd+J` 切換側邊欄/右側/底部面板、`Cmd+,` 打開設置。
- **Esc 分層語義**：先關彈窗/菜單/抽屜 → 其次「返回上級」逐層退回（設置子頁 → 設置主頁 → 對話視圖）→ 生成中連按兩次 Esc 停止。
- **插話快捷鍵**：`Cmd+Enter` 在任務運行中立即入隊並 steer 引導運行中的圖，空閒時退回普通發送；回車（Enter）維持發送。

### 改動

- 快捷鍵列表按功能分組排序（生成 → 輸入 → 訊息 → 對話 → 導航 → 面板 → 設置），並優化各語言的標題與文案。
- 快捷鍵持久化改綁（localStorage），改綁與衝突檢測統一以註冊表為單一真源。

### 修復

- 修復 Shift 組合快捷鍵（如 `Cmd+Shift+N`）因大小寫比對失配而全部失效的問題。
- 修復快捷鍵設置頁開關因缺少 `id` 而無法點擊的問題。

## 0.5.3

本版本引入系统内置的「聊天」项目与 Lazzzy Boy 人设：无需先创建项目即可开始闲聊，并彻底修正「空项目回退到应用仓库目录」的安全隐患。

### 新增

- **系统内置「聊天」项目**：应用启动时自动创建并置顶固定在侧栏，行为与普通项目一致，但不可删除 / 重命名；其工作区为系统指定的独立沙箱目录（`COWORKER_DATA_DIR/chat`），与普通项目完全隔离。
- **开箱即聊（空态引导）**：首启或仅剩聊天项目时展示 onboarding 首页，可直接「开始聊天」或创建项目；全局「新对话」在无真实项目时默认落入聊天项目。
- **Lazzzy Boy 聊天人设**：聊天项目内的 Agent 使用来自 [lazzzyboy.com](https://lazzzyboy.com) 的「懒懒男孩」人格——轻松、随和、聪明，先给结论、简洁作答；除非用户明确要求，否则不主动读取 / 修改文件、不运行命令、不调用搜索或 MCP 工具（工具仍保留，行为与其他项目一致）。
- **聊天项目名称本地化**：侧栏 / 标题栏 / 工作区选择器 / 记忆页面按界面语言显示（聊天 / Chat / チャット …）。

### 改動

- **空项目安全回退**：无项目会话的工作区回退由「应用自身仓库根目录」改为「聊天项目沙箱目录」，彻底杜绝 Agent 读写应用源码的隐患；`/chat/stream` 无项目兜底改为归入聊天项目。

### 修復

- 修复首启时因自动选中置顶的聊天项目而跳过 onboarding 引导页的问题。

### 品質

- 新增 `test_chat_project.py`：启动自愈（记录 / 沙箱 / 记忆三件套）、删除 / 重命名 / 路径占用拒绝、空项目回退 = 沙箱 ≠ 应用根、人设提示词切换与 phase 契约替换、聊天记忆预置且不覆盖用户编辑。

## 0.6.0

本版本引入「项目仪表盘」与「技能自创作 + 审批队列」两大能力：项目文件 / Agent / 会话一目了然，Agent 学会的可复用流程会暂存为草稿等待审批；同时打通桌面端工作区浏览与会话状态展示。

### 新增

- **项目仪表盘**：每个项目新增独立的仪表盘页面（概览 / 文件 / Agent / 记忆 / 会话历史）与后端聚合接口 `GET /projects/{id}/dashboard`；概览展示项目信息、Git 状态（分支 / 变更文件 / 未跟踪）与统计卡片（Agent 数 / 会话数 / 变更文件数），Agent 页展示名册并可一键发起新对话；支持侧栏「打开仪表盘」按钮与 `Cmd+O` 全局快捷键。
- **文件浏览增强**：文件树支持键盘导航与 ARIA 可访问性；文件预览支持代码高亮 + 行号（复用 Shiki CodeBlock）、CSV / XLSX 虚拟表格滚动、可拖拽分栏，并可直接用系统应用外部打开文件。
- **技能自创作（self-calibration）**：使用过工具的任务结束后，后台「Hermes 式」审查会判断是否存在值得沉淀的可复用流程（尤其当用户纠正了 Agent 的做法时），并据此生成 SKILL.md 草稿提案；Agent 永远不会自行启用技能，所有草稿都进入审批队列等待人工确认。
- **技能审批队列**：`install_skill` / `skill_manage` 的写入统一走同一审批通道——需审批时先暂存为草稿（`<user_skills>/.pending/`，不注入任何上下文），批准后新技能移入正式目录、替换类草稿覆盖原技能；设置页新增「待处理」面板，可查看、编辑、批准 / 驳回草稿。
- **自动技能设置**：新增积极度（active / cautious / passive）与「人工审核」开关；关闭免审后技能创建 / 更新立即生效。
- **会话状态指示器**：侧栏会话列表新增状态徽章（进行中 > 待审批 > 出错 > 未读红点），未读简化为红点，并支持标记已读。

### 改動

- **音效语义化**：`attention` 拆分为 `card_popup`（审批卡弹出，成功音）与 `user_pause`（用户暂停 / 停止，失败音），并新增审批拒绝 / 关闭提示音。
- **导航收敛**：移除侧栏 memory 视图按钮与 `Cmd+4` 快捷键（记忆入口移至设置 → Runtime → Open Memory），设置页加入品牌标记；仪表盘移除 tools 标签页，并同步移除概览页的能力 chips（memory / web / browser）与工具统计卡片。
- **桌面构建脚本**：抽离 `scripts/build-pybackend.sh` / `check-deps.sh` / `check-node-deps.sh`，统一 macOS / Windows 打包路径。
- **侧栏打磨**：会话时间移入「更多」触发器，hover 显示图标，激活态高亮减淡，工作区与会话列表样式统一。

### 修復

- **根治 vLLM 'No user query' 400**：连续多次 HITL 续跑可能恢复出空会话，导致严格 provider（vLLM / Qwen）收到 `messages=[]`；新增 `EnsureUserMessageMiddleware`，在模型边界检测到空会话时自动从会话存储重放最近对话。
- **中间旁白语言跟随**：任务进行中的旁白 / 步骤说明改为跟随用户消息语言，中文对话不再中英混排。
- **输入框常驻聚焦**：修复需要二次点击才能聚焦的问题，并移除 focus 高亮。
- **技能面板细节**：待审核角标实时刷新；前端对 SKILL.md 冒号描述解析容错。
- **音效修复**：审批拒绝 / 关闭有提示音、防重复播放；预加载改用 OfflineAudioContext 解码，避免 Chromium 拒绝恢复被挂起的 AudioContext 导致本会话首个提示音丢失。
- **文件预览修复**：docx 预览白底；旧版表格格式（xlc / xls / xlsb）归类为 Office 并支持外部打开。

### 品質

- 新增 `test_dashboard.py`、`test_skill_self_authoring.py`、`test_message_processor.py`、`test_context_trim.py`：覆盖仪表盘数据装配、技能自创作草稿暂存与审批流转、空会话重放守卫、上下文裁剪。

## 0.6.1

本版本聚焦於 MCP 面板與連線穩定性的全面加固：新增 Streamable HTTP 傳輸、CSRF 保護、並發 OAuth 支援，並重構了 MCP 模組的錯誤處理與工具索引。同時大幅改進了聊天輸入框的鍵盤體驗。

### 新增

- **Streamable HTTP 傳輸支援**：MCP 面板新增 `Streamable HTTP (remote)` 傳輸選項，並補上對應的 i18n 翻譯。
- **MCP 工具描述命令（describeCommand）i18n**：為工具調用補齊 `describeCommand`、`describeToolCall` 等 40+ 條翻譯（git / pip / docker / 文件操作 / 網頁搜索等），覆蓋所有本地語言。
- **MCP 工具索引 O(1) 查找**：`McpToolMiddleware` 改用 `_all_tools_map` 進行工具名稱查找，替代之前的線性遍歷。

### 改動

- **MCP 模組重構與工具抽離**：將 `mcp_session.py` 與 `mcp_test.py` 中重複的 `_flatten_exceptions` 與 `_friendly_error` 抽離為共享工具模組 `mcp_utils.py`，並納入 `__all__` 導出。
- **MCP 傳輸校驗強化**：`normalize_transport` 不再對未知傳輸靜默回退為 stdio，改為拋出 `ValueError`；`build_connection` 對 timeout 增加正數與 3600 秒上限校驗。
- **MCP 更新校驗順序調整**：`McpManager.update_server` 改為先收集新值再校驗傳輸，避免切換傳輸時舊字段干擾校驗。
- **MCP 中間件避免阻塞**：移除 `_overrides` 中同步調用的 `ensure_connected`（圖編譯時已預熱會話），避免每次模型調用阻塞事件循環。
- **聊天輸入框免點擊聚焦**：重寫 `ChatInput` 的聚焦策略，改為「掛載即聚焦 → 可打印字符自動聚焦 → Escape 移出焦點」，與 opencode 的聚焦模型對齊；支持 `[data-prevent-autofocus]` 區域跳過自動聚焦；鼠標選擇 / 複製文字不再觸發聚焦。
- **MCP 面板表單驗證**：新增 timeout 無效時的錯誤提示；刪除服務後自動返回列表視圖。
- **MCP 刪除冪等**：刪除不存在的 MCP 服務時忽略 404 錯誤。
- **Electron 主進程更新**：同步 MCP 配置與工作區相關變更。

### 修復

- **MCP OAuth CSRF 保護**：`LoopbackCallbackServer` 新增 CSRF state 驗證（RFC 6749 Section 10.12）與 iss 校驗（RFC 9207），支援並發 OAuth 流程。
- **MCP 探針迴圈資源洩漏**：探針階段创建的 loopback 在所有錯誤路徑下正確關閉，防止文件描述符洩漏。
- **MCP 連接超時任務清理**：`wait_for` 超時後正確取消並 await 被 shield 的連接任務，避免任務懸掛。
- **MCP 會話關閉順序**：stdio 傳輸的 owner 任務 shielded teardown 正確處理子進程清理（close stdin → wait → SIGTERM → SIGKILL）。

### 品質

- 新增 MCP 審計修復方案文檔 `docs/task/mcp-audit-fix-plan.md`。

## 0.6.2

本版本聚焦於弱網路環境下的連線穩定性與效能：HTTP/2 多路複用、60 秒 keep-alive 長連接、MCP 協議分頁拉取全部工具、idempotent 工具自動重試；同時強化了上下文預算——主動裁剪陳舊的大工具結果，避免長工具迴圈重複上傳已消耗的 megabytes 級輸出。

### 新增

- **HTTP/2 傳輸調優**：`model_defaults` 新增 `_build_tuned_async_httpx_client`，當 `h2` 包可用且端點為 HTTPS 時啟用 HTTP/2 多路複用（同一 TCP 連接並行多個模型呼叫）；keep-alive 池過期從 5 秒提升至 60 秒，避免思考/工具間隙觸發重新 TCP+TLS 握手；connect timeout 從 5 秒放寬至 10 秒。
- **弱網路預發提示**：訊息气泡在空狀態（尚未收到第一個 token）顯示「思考中」點陣提示，與 opencode 的即時反饋對齊。
- **上下文 S0 主動裁剪**：`ContextGuardMiddleware` 新增 `_window_stale_tool_results`，當請求已佔用 50% 窗口時，主動裁剪超過 4 個最新工具消息之外的陳舊大工具結果（保留 `TOOL_RESULT_KEEP_CHARS` 字元），避免長工具迴圈每輪重複上傳已消耗的 megabytes 級輸出。
- **會話切換防覆蓋**：切回會話時區分 in-flight 流與 loaded 半截記錄，避免 running 氣泡被已完成的半截記錄替換。
- **MCP 模板新增**：Git 模板改用 `uvx` 替代 `npx`；新增 Fetch（網頁內容轉 Markdown）與 Time（時間/時區）模板；filesystem 模板自動填充當前用戶 home 目錄。
- **MCP 品牌圖標**：模板圖標從內聯 SVG 改為 PNG 品牌圖（`assets/mcp-logo/`），單色圖標在暗色模式下自動反白。

### 改動

- **MCP 協議分頁**：`mcp_session` 改用 `_list_all_tools` 分頁拉取全部工具，替代單頁拉取；協議版本改用 `LATEST_PROTOCOL_VERSION` 替代硬編碼。
- **MCP 重試安全**：新增 `_retry_safe` 方法，僅對 idempotent / read-only 工具自動重試（避免 idempotent 未聲明時重試已執行服務端操作的危險行為）；工具註解默認 `destructive=True`。
- **MCP 禁用工具**：`_server_disabled_tools` 支持按服務禁用工具。
- **MCP 探針回退修復**：需要認證的探針 loopback 不再帶入活會話，防止 double-close。
- **LLM 流超時**：`stream_chunk_timeout` 默認從 600 秒降至 120 秒，匹配 opencode 的 fail-fast 行為。
- **運行時重試收縮**：移除 app-level 重試（由 SDK `max_retries=2` 處理），`RETRY_RETRIES` 從 2 降至 1（僅 overflow/image-limit 重試）。
- **MCP 面板**：表單驗證、刪除冪等、探針迴圈資源洩漏修復、連接超時任務清理、會話關閉順序修復。

### 修復

- **MCP OAuth CSRF 保護**：`LoopbackCallbackServer` 新增 CSRF state 驗證與 iss 校驗，支援並發 OAuth 流程。
- **MCP 探針迴圈資源洩漏**：探針階段創建的 loopback 在所有錯誤路徑下正確關閉。
- **MCP 連接超時任務清理**：`wait_for` 超時後正確取消並 await 被 shield 的連接任務。
- **MCP 會話關閉順序**：stdio 傳輸的 owner 任務 shielded teardown 正確處理子進程清理。

### 品質

- 新增 `test_http_transport_tuning.py`、`test_mcp_protocol.py`：覆蓋 HTTP 傳輸調優與 MCP 協議分頁。

## 0.6.3

本版本引入 ClawHub 技能市場瀏覽與安裝能力：點擊卡片查看完整 SKILL.md 後再安裝，支援多作者技能歧義解析（自動匹配或手動選擇作者），並修復市場技能名稱不合法導致安裝失敗的問題。

### 新增

- **技能市場瀏覽**：設置頁新增「市場」面板，支持按熱門 / 趨勢 / 搜索瀏覽 ClawHub 技能；點擊卡片彈出詳情模態框，展示完整 SKILL.md 內容（含 frontmatter 與 body），確認後再安裝。
- **歧義 slug 解析**：當 ClawHub 技能 slug 對應多個作者時，詳情接口返回 `409` 與候選列表；前端自動嘗試匹配點擊卡片的顯示名稱，若找到唯一匹配則自動選擇作者，否則展示作者選擇器供手動選擇。
- **市場技能溯源標記**：安裝的技能在 frontmatter 中注入 `provenance: market` 字段，便於區分市場安裝與本地創建的技能。

### 修復

- **歧義 slug 404**：修復候選作者解析時使用完整引用（如 `@owner/skill`）導致 ClawHub 返回 404 的問題，改為使用純 owner handle（如 `owner`）。
- **詳情模態框空白**：修復歧義解析卡死狀態（`autoResolving` 未清除）與全局 `fetchDone` 緩存導致切換技能卡片時顯示過期內容的問題，改為按 `source:slug:owner` 鍵控緩存。
- **安裝作者錯失**：修復點擊安裝時使用卡片原始 owner（可能為空）而非詳情模態框中選擇的作者的問題。
- **市場技能名稱安裝失敗**：修復部分 ClawHub 技能 frontmatter `name` 為人類可讀標題（如 `Self-Improving + Proactive Agent`）不滿足本地 `^[a-z0-9]+(-[a-z0-9]+)*$` 校驗導致安裝報錯的問題；安裝時自動將 name slugify 歸一化並回寫 frontmatter，確保後續掃描不再報錯。
- **詳情請求超時**：將 Electron 後端請求超時從 10 秒提升至 60 秒，適應詳情流程中多個順序 ClawHub 請求與速率限制重試。

### 改動

- **i18n**：新增 `skills.market_ambiguous_owner` 與 `skills.market_body_empty` 翻譯鍵，覆蓋全部 11 種語言。

## Unreleased

（尚未發布內容記錄於此，發版時將本區段改名為對應版本號，例如 `## x.x.x` ）
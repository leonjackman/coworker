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

## Unreleased

（尚未發布內容記錄於此，發版時將本區段改名為對應版本號，例如 `## 0.5.2`。）

# Coworker Agent

> 本地优先的 AI 编程助手桌面应用 — 用对话式界面与你的代码一起工作，支持任意 AI 语言模型。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=for-the-badge)](LICENSE)
[![CI](https://img.shields.io/badge/CI-passing-238636?style=for-the-badge)](https://github.com/leonjackman/coworker/actions)
[![GitHub release](https://img.shields.io/github/v/release/leonjackman/coworker?style=for-the-badge)](https://github.com/leonjackman/coworker/releases/latest)
[![macOS](https://img.shields.io/badge/platform-macOS-006600?style=for-the-badge&logo=apple)](#install)
[![Windows](https://img.shields.io/badge/platform-Windows-0078D6?style=for-the-badge&logo=windows)](#install)
[![Linux](https://img.shields.io/badge/platform-Linux-333?style=for-the-badge&logo=linux)](#install)

[English](README.md) · [简体中文](README.zh-CN.md)

<p align="center">
  <img src="https://github.com/leonjackman/coworker/raw/dev/docs/screenshots/banner-logo.png" alt="Coworker" width="100%">
</p>

---

### 截图

<p align="center">
  <img src="https://github.com/leonjackman/coworker/raw/dev/docs/screenshots/welcome-dark.png" width="100%" alt="Coworker - 欢迎页">
</p>

<p align="center">
  <img src="https://github.com/leonjackman/coworker/raw/dev/docs/screenshots/chat-light.png" width="100%" alt="Coworker - 对话页">
</p>

---

## 功能特点

| 功能 | 说明 |
| --- | --- |
| 🗨️ **流式对话** | 基于 SSE 的实时 Agent 响应，支持心跳保活与多会话并行流式输出 |
| 📥 **消息排队 & 插話** | Agent 回覆期間仍可繼續輸入，消息按会话入队，當前流結束後逐條自動發送；亦可對佇列中任一消息「插話」(↳)，在不中斷當前流的前提下引導回覆 |
| 🔌 **多模型支持** | 内置 32 个模型厂商预设 —— OpenAI、Anthropic、Google Gemini、DeepSeek、Qwen / DashScope、Moonshot (Kimi)、Zhipu (GLM)、Doubao、Minimax、Cohere、Groq、xAI、Mistral、Ollama、vLLM、OpenRouter、SiliconFlow 等 —— 以及任意 OpenAI 兼容自定义端点，支持上下文窗口在线探测 |
| 🧠 **长程记忆** | 按 Agent / 项目隔离的 Markdown 记忆库，LLM 自动提取，支持 zip 导入导出、回收站与跨目录迁移 |
| 👥 **多 Agent 团队** ⚠️ | 创建团队与部门，Agent 之间可互相委派任务。**实验性能力** — 见下方说明 |
| 👤 **子代理 (Sub-Agent)** | 单 Agent 模式下可派出独立子代理并行或串行执行任务，每个子代理拥有独立的 LLM 运行环境和受限工具集 |
| 🎯 **目标模式 (Goal Mode)** | 用 `/goal` 设定持久目标，Agent 自动连续多轮推进直至完成或受阻，并配有常驻进度卡片与暂停 / 恢复 / 清除控制 |
| 🔄 **MCP 集成** | Model Context Protocol 支持 — stdio / HTTP / SSE / WebSocket / Streamable HTTP 传输，OAuth 2.1 + PKCE、模板发现、持久会话 |
| 📦 **技能系统** | SKILL.md 标准技能，支持市场浏览与一键安装（SkillHub · ClawHub），Agent 可在对话中直接安装新技能 |
| 🌐 **网页搜索 & 抓取** | 基于 [Tavily](https://tavily.com) 的网页搜索（`web_search`）与网页抓取（`web_fetch`），支持深度搜索、结果数配置、Cloudflare 自动重试；API Key 安全存储于系统钥匙串 |
| 🖥️ **内置浏览器** | 应用内嵌 Chromium 浏览器视图，Agent 可操控它导航、点击、输入、滚动、截图、执行 JS 并读取 DOM；用户也可右键捕获页面元素或截图发送到对话中 |
| 🔒 **人机协作审查** | 命令执行、文件修改、MCP 调用需确认，支持 supervised / guarded / autonomous 三级自主度 |
| 📓 **变更追踪** | 每次文件修改记录 before/after 差异；编辑 / 重新生成 / 撤销可恢复至修改前状态 |
| 🖥️ **内置终端** | 底部面板内置交互式 PTY 终端，并实时展示工具审计日志 |
| 🔎 **审计与追踪** | 工具审计日志与 Agent 追踪记录，支持导出、清空与保留上限配置 |
| ✏️ **消息编辑** | 可编辑或重新生成任意用户消息，下游代码改动自动回滚且可恢复 |
| 📎 **文件附件** | 对话中可发送文本或二进制文件附件（默认上限 25MB，可配置 1–1024MB），Agent 可直接读取内容 |
| 🔗 **会话交叉引用** | 在对话中粘贴其他会话 ID，Agent 可读取被引用的会话上下文 |
| 🛡️ **安全保护** | 禁止读取 `.env`、`.pem`、`id_rsa` 等敏感文件；文件写入严格限制在工作区目录内 |
| 📋 **计划 / 执行双模式** | "Plan" 为只读规划阶段（Agent 只能查看和列计划），切换到 "Build" 后解锁全部写入与执行能力 |
| 🌎 **国际化** | 支持 11 种语言 — 英文、中文（简 / 繁 / 港）、日文、韩文、法文、德文、西班牙文、葡萄牙文、俄文 |
| 🎨 **主题** | 10 套精心设计的 OKLCH 色彩预设（矿物、赫耳墨斯、余烬、Sage、石墨、蔚蓝、夜曲、Solarized、Monokai、紫罗兰），各含明暗双配色，支持自定义强调色 |
| 🔊 **声音通知** | Agent 回复完成、错误、提醒等事件触发提示音，支持全局开关 |
| 📊 **上下文窗口指示器** | 实时显示 Token / 字符消耗量，追踪上下文压缩状态，帮你掌控预算 |
| 🔄 **自动更新** | 支持预发布通道，带进度条、版本跳过、错误分类与本地版本通知 |

> ⚠️ **多 Agent（实验性）** — 多 Agent 团队、部门与任务委派属于实验性能力，仍在积极开发中：功能尚未完善，行为可能随版本变化，且项目模式创建后不可更改。日常使用建议采用单 Agent 模式。

---

## 关于 Coworker

**Coworker 是一款正在快速迭代开发中的全新产品。** 新功能持续交付，其间可能包含破坏性变更，部分功能仍在完善中或可能存在尚未发现的缺陷。我们鼓励你试用、探索、报告问题并提供反馈 — 你的意见将直接塑造产品的方向。

新版本不定期发布。请 Star 仓库或关注 Releases 以获取最新动态。

---

## 产品定位

Coworker 是一款本地优先、功能全面的 AI 编程助手。与传统云端编程助手不同，它的核心价值在于：

- **隐私第一** — 所有数据（代码、记忆、聊天记录）完全存储在本地，不经过任何第三方服务器
- **模型中立** — 不绑定任何厂商，支持 OpenAI 兼容 API、Ollama 及自定义端点，按需切换
- **全能 Agent** — 从网页搜索、浏览器操作到文件管理、子代理并行、MCP 扩展与技能市场
- **人机协作** — 内置人机协作审查机制，所有关键操作都需要你的确认
- **可追溯** — 完整的变更追踪、审计日志、Agent 轨迹导出、回滚到任意状态
- **可扩展** — MCP 协议 + SKILL.md 技能系统 + 市场生态，持续扩展新能力

---

## 安装

> **前置预览版** — Coworker 正在积极开发中，功能持续迭代完善。下载试用，发现 bug 并报告，帮助我们共同打造更好产品。

### 从 Releases 下载安装（推荐）

预构建安装包可从 [GitHub Releases](https://github.com/leonjackman/coworker/releases) 下载：

| 平台 | 安装方式 |
| --- | --- |
| **macOS (Apple Silicon)** | [下载 .dmg](https://github.com/leonjackman/coworker/releases) |
| **Windows 10+** | [下载 .exe](https://github.com/leonjackman/coworker/releases) |
| **Linux (x64)** | [下载 .AppImage](https://github.com/leonjackman/coworker/releases) |

> macOS 构建未签名/未公证。首次运行可能需要 `xattr -d com.apple.quarantine /Applications/Coworker.app`。

### 从源码安装

克隆仓库后用开发环境运行（见下方[开发](#development)）。

---

## 快速开始

### 桌面应用 (macOS, 从源码运行)

```bash
./coworker_desktop.command
```

该脚本会自动安装依赖、构建前端、启动后端（FastAPI）并打开 Electron 应用。

免打开桌面的冒烟测试：

```bash
COWORKER_SKIP_DESKTOP=1 ./coworker_desktop.command
```

所有平台均可通过 [GitHub Releases](https://github.com/leonjackman/coworker/releases) 下载预构建安装包使用。

### 配置模型

在应用的 **设置 → 提供商** 中添加 AI 语言模型：

- 从 32 个内置厂商预设中选择（OpenAI、Anthropic、Google Gemini、DeepSeek、Qwen、Moonshot、Zhipu 等），或填写自定义 OpenAI 兼容端点
- 为所选厂商设置基础 URL、模型名称、API 密钥
- 密钥会存入系统 Keychain（macOS）或 0600 权限文件（备用）
- 使用内置 "测试" 按钮检查连接

使用本地模型 Ollama：

```text
基础 URL: http://localhost:11434/v1
模型: 你的模型名称
```

---

## 开发

### 后端

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 9527
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

### 完整开发模式

```bash
# 终端 1 — 启动 FastAPI
cd backend && source venv/bin/activate && uvicorn main:app --host 127.0.0.1 --port 9527

# 终端 2 — 启动 Vite 开发服务器
cd frontend && npm run dev

# 终端 3 — 用 Vite 开发服务器启动 Electron
NODE_ENV=development npx electron . --no-sandbox
```

---

## Coworker 与众不同的地方

| 传统编程助手 | Coworker |
| --- | --- |
| 依赖云端，隐私风险 | **真正本地优先** — 所有数据保存在本地机器上 |
| 绑定单一厂商 | **多模型中立** — 任意 OpenAI 兼容 API、Ollama、自定义端点 |
| 会话级记忆，无跨会话持久化 | **长程记忆** — 自动从交互中提取，跨会话持久化 |
| 无法控制 Agent 行为 | **默认 HITL** — 手动审批每条命令和文件修改 |
| 无审计追踪 | **完整可追溯** — 可导出 Agent 日志、工具审计日志、回滚到任意状态 |
| 盲盒式工具执行 | **透明化** — 每次改动都有 before/after 对比，以可读格式记录 |
| 工具能力有限 | **丰富工具生态** — 网页搜索、内置浏览器、子代理并行、MCP 扩展、SKILL.md 技能市场 |

---

## 长程记忆（Long-Term Memory）

Coworker 在 `~/Library/Application Support/Coworker/memory/`（macOS；目录跟随
`COWORKER_DATA_DIR`）维护按 Agent、按项目隔离的 Markdown 记忆库，全部是普通
可编辑 Markdown 文件。

```
memory/
├── MEMORY.md · USER.md · AGENT.md      # 系统级事实（用户维护）
└── <项目>/                             # 每个项目一个时间戳命名目录
    ├── BASE/                           # 用户维护的项目事实
    └── <agent>/
        ├── BASE/                       # SOUL · AGENT · MEMORY.md（agent 事实）
        │   └── DREAMS.md               # 自动提取的复盘日记
        └── SESSIONS/<日期>.md          # 每日会话笔记（自动 + 手动）
```

### 工作方式

- **读取（注入）**：每轮对话把相关记忆文件注入系统提示词（上限
  `memory.char_limit`，默认 2000 字符）。额外文件与会话笔记通过 `memory_read`
  工具按需读取。
- **手动写入**：agent 通过 `memory` 工具把持久事实写入自己的
  `BASE/MEMORY.md`（或主题文件 / `SESSIONS/<日期>.md`），自动去重，受控模式
  需审批。
- **自动提取（dream）**：每 N 轮对话后（由 `COWORKER_MEMORY_NUDGE_INTERVAL` 控制，默认 10），后台任务用 LLM 审查最近对话：
  1. 提取持久事实并合并进 agent 的 `MEMORY.md`；
  2. 向 `SESSIONS/<日期>.md` 追加一条简短会话笔记（每天一次）；
  3. 在 `DREAMS.md` 记一行（如 `consolidated · new 3`）。

自动提取使用记忆设置里配置的模型（`memory_extract_model`，为空时回退默认
Provider），由 `COWORKER_MEMORY_ENABLED` 和 `COWORKER_MEMORY_AUTO_EXTRACT`
控制（默认都开启）。

### 如何保持文件有界

记忆文件全部"写时治理"（随 dream 懒执行，无后台任务）：

- **`MEMORY.md` / `USER.md`**：合并把文件控制在注入预算内（默认 4000
  字符）；append-only 回退路径同样按该预算做 FIFO 裁剪（先丢最旧，最新
  事实永不丢失）。
- **`DREAMS.md`**：活文件只保留当月条目，旧月移到
  `ARCHIVE/DREAMS-YYYY-MM.md`。
- **`SESSIONS/<日期>.md`**：早于当月的日期文件合并进
  `ARCHIVE/SESSIONS-YYYY-MM.md` 后删除。

归档统一放 `<agent>/ARCHIVE/`——不注入提示词，仍可经 `memory_read` 按需读取。

### 如何验证

1. 在对话中说出明确持久事实，如"我偏好中文回复""本项目后端端口是 9527"。
2. 停止对话，等几个轮次（或等待 nudge 间隔）。
3. 查看 `memory/<项目>/default_agent/BASE/`：
   - `MEMORY.md` 出现新条目；
   - `DREAMS.md` 显示 `new N · consolidated`（而不是 `new 0`）；
   - `SESSIONS/<今天日期>.md` 生成会话笔记。
4. 想看过程：以 `COWORKER_LOG_LEVEL=DEBUG` 重启，在 `app.log` 中搜索
   `dream done ... added=N transcript=… chars`。

运行记忆自检：

```
cd backend && ./venv/bin/python coworker/memory/selftest.py
```

---

## 消息排队（流中排队）

Agent 回覆期間不必乾等——你可以繼續輸入並立即發送下一條消息。

- **流進行中輸入框仍可編輯** —— 回覆進行時輸入框不會被鎖定，僅「連接中」階段禁用。
- **發送即入隊** —— 流式回覆進行中按 Enter / 發送鍵並**不會**並發啟動新回合，而是把消息追加到該会话的 FIFO 佇列，待當前流結束（`done` / error / stopped）後自動作為下一個請求逐條送出。輸入框為空時顯示「停止」鍵，仍可中斷正在執行的任務。
- **佇列訊息可見** —— 每條待發送消息在 composer 上方的任務卡中逐條陳列（一行一條），並帶「插話」(↳) 與 ✕ 移除按鈕，不會藏在某個圖示後面。
- **編輯與重生成保持序列化** —— 每個 session 同時只有一條流；佇列消息絕不會與其等待的流並發競爭。

### 被中斷的回合如何乾淨地重啟

若串流被中斷（停止、客戶端斷線），後端會把該 session 標記為「已中斷」。下一次 `/chat/stream` 會丟棄髒的 runtime checkpoint，改從 session history 重建，因此新的（或佇列中的首條）消息會成為當前指令，而不是讓模型沿用舊中斷點繼續執行原本任務。

### 插話（引導進行中的任務）

Agent 流式回覆期間，從佇列中任選一條消息點「插話」(↳)，即可**在不停止、不重啟當前流的同時**引導回覆走向。

- **在當前回合內引導** —— 插話不會中止在飛的 stream，而是把該消息交給運行中的 agent，折入其**下一次模型呼叫**，讓 LLM 後續的輸出與工具步驟遵循你的引導。
- **以「收到插話」card 呈現** —— 插話文字會以 assistant 氣泡內的 notice（card）顯示，而非額外的用戶泡泡，保持對話流乾淨。
- **late-steer 不遺失** —— 若任務在插話被消費前已結束，該插話會自動續跑為下一輪，不會被丟棄。

> 佇列消息會在當前流結束後逐條自動送出；若想讓引導**在當前運行中**立即生效，請用「插話」而非等待流結束。

---

## 日志与可观测性

所有日志默认落盘到应用数据目录（macOS：`~/Library/Application Support/Coworker`，可用 `COWORKER_DATA_DIR` 覆盖）。

| 文件 | 内容 | 治理 |
| --- | --- | --- |
| `app.log` + `app.log.N` | 运行时主日志（默认 JSON，逐行一条记录） | 按大小轮转（默认 10 MB × 5 份） |
| `agent_trace.jsonl` | Agent 活动轨迹（事件/状态/上下文） | 滚动保留，默认最近 100 条（可在设置中调整） |
| `tool_audit.jsonl` | 工具调用审计 | 滚动保留，默认最近 100 条（可在设置中调整） |
| `command_approvals.json` | 命令审批记录 | 保留最近 100 条 + 保底 25 条 |
| `worker_events/<run>.jsonl` | 子 Agent 运行流事件（可重放） | 保留最近 200 个运行 / 总大小 ≤ 100 MB |

控制方式：

- **环境变量**：`COWORKER_LOG_LEVEL`（级别）、`COWORKER_LOG_MAX_BYTES`、`COWORKER_LOG_BACKUP_COUNT`、`COWORKER_JSON_LOG`、`COWORKER_HTTP_LOG`（请求日志开关）、`COWORKER_WORKER_EVENTS_MAX_RUNS` / `COWORKER_WORKER_EVENTS_MAX_BYTES`。
- **运行时 API**：`POST /settings/log-level`（切换级别并持久化）、`POST /settings/log-config`（级别/轮转/JSON/请求日志，立即生效并持久化）、`POST /settings/truncate-log`（清空或保留末尾 N 字节）、`GET /settings/log-file`（分页读取）。
- **请求日志**：每条 HTTP 请求以 `coworker.http` 记录 method/path/status/耗时与 `request_id`；健康检查与日志读取端点自动跳过；查询串中的 token/key 等敏感参数会被打码。
- **会话关联**：`app.log` 的 JSON 记录会自动带上 `session_id`（会话相关请求及 `/chat/stream` / `/chat/interject` 全程），可直接 `grep '"session_id":"<会话ID>"'` 拉出单个会话的运行时日志。

---

## 目标模式（Goal Mode）

用 `/goal <目标>` 设定一个持久目标，Agent 会在**无需额外输入**的情况下连续多轮推进，直到将目标标记为 `complete`（完成）或 `blocked`（受阻）。任务面板中会常驻一张进度卡片（目标、状态、已用时间、token 预算进度条）。

- **输入框 Stop** 停止当前任务；目标保持 `active`，需一次触发（新消息 / 恢复 / 重开会话）才能继续。
- **TodoBlock 暂停 / 恢复 / 清除** 控制目标本身 —— 暂停会让当前轮跑完后停止后续轮次，但不中止正在进行的运行。
- 目标**严格按会话隔离**，绝不影响其他会话。
- 若运行中触发人机协作审批或提问，循环会暂停，在你批准后续跑。

---

## 技术栈

| 层级 | 技术 |
| --- | --- |
| **桌面端** | Electron 43 · contextBridge · 系统托盘 · electron-updater |
| **前端** | React 19 · Vite 8 · Zustand · xterm.js · Tailwind · Shiki |
| **后端** | Python 3 · FastAPI · Uvicorn · Pydantic · SQLite · LangGraph |
| **Agent 运行时** | LangChain 1.3 · LangGraph 1.2 · 多级审查中间件 |
| **模型支持** | OpenAI 兼容 API · Ollama · 自定义基础 URL |
| **MCP** | MCP 1.29 · langchain-mcp-adapters 0.3 · 五种传输协议 |
| **网页搜索** | Tavily API（可配置 provider、搜索深度、结果数量） |
| **技能扩展** | MCP 服务器 · SKILL.md 技能 · SkillHub / ClawHub 技能市场 |
| **国际化** | en · zh · zh-TW · zh-HK · ja · ko · fr · de · es · pt-BR · ru |

---

## 参与贡献

> **Bug 报告 & 反馈** — Coworker 是持续迭代的项目。遇到问题请 [提交 Issue](https://github.com/leonjackman/coworker/issues) 并附上复现步骤，我们会修复。

欢迎贡献！提交 PR 前请先阅读 [CONTRIBUTING](CONTRIBUTING.zh-CN.md) · [Contributing](CONTRIBUTING.md)。

- [报告 Bug](https://github.com/leonjackman/coworker/issues) · [功能建议](https://github.com/leonjackman/coworker/issues)

---

## 链接

- [GitHub Releases](https://github.com/leonjackman/coworker/releases) · [macOS / Windows / Linux 安装包](https://github.com/leonjackman/coworker/releases)
- [GitHub Issues](https://github.com/leonjackman/coworker/issues) · [报告 Bug 和功能建议](https://github.com/leonjackman/coworker/issues)
- [CONTRIBUTING.md](CONTRIBUTING.md) · [如何参与贡献](CONTRIBUTING.md)

---

## 开源许可

MIT — 见 [LICENSE](LICENSE)。

由 [Coworker Contributors](https://github.com/leonjackman/coworker) 构建。

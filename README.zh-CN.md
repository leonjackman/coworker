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
| 🔌 **多模型支持** | OpenAI、Ollama 及任意 OpenAI 兼容 API，支持上下文窗口在线探测 |
| 🎯 **目标模式** | 多轮自主执行，支持暂停、恢复、编辑、停止、轮次上限与待办追踪 |
| 🧠 **长程记忆** | 按 Agent / 项目隔离的 Markdown 记忆库，LLM 自动提取，支持 zip 导入导出 |
| 👥 **多 Agent 团队** ⚠️ | 创建团队与部门，Agent 之间可互相委派任务。**实验性能力** — 见下方说明 |
| 🔒 **人机协作审查** | 命令执行、文件修改、MCP 调用需确认，支持 supervised / guarded / autonomous 三级自主度 |
| 🔄 **MCP 集成** | Model Context Protocol 支持 — stdio / HTTP / SSE / WebSocket、OAuth 2.1 + PKCE、模板发现、持久会话 |
| 📦 **技能系统** | SKILL.md 标准技能，支持市场浏览与一键安装（SkillHub · ClawHub） |
| 📓 **变更追踪** | 每次文件修改记录 before/after 差异；编辑 / 重新生成 / 撤销可恢复到任意历史状态 |
| 🖥️ **内置终端** | 底部面板内置交互式 PTY 终端，并实时展示工具审计日志 |
| 🔎 **审计与追踪** | 工具审计日志与 Agent 追踪记录，支持导出、清空与保留上限配置 |
| ✏️ **消息编辑** | 可编辑或重新生成任意用户消息，下游代码改动自动回滚且可恢复 |
| 🌎 **国际化** | 支持 11 种语言 — 英文、中文（简 / 繁）、日文、韩文、法文、德文、西班牙文、葡萄牙文、俄文 |
| 🎨 **主题** | 明暗 / 跟随系统模式，自定义强调色 |

> ⚠️ **多 Agent（实验性）** — 多 Agent 团队、部门与任务委派属于实验性能力，仍在积极开发中：功能尚未完善，行为可能随版本变化，且项目模式创建后不可更改。日常使用建议采用单 Agent 模式。

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

- 设置基础 URL、模型名称、API 密钥
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
- **自动提取（dream）**：每轮结束约 30 秒后，后台任务用 LLM 审查最近对话：
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
2. 停止对话等约 30 秒。
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

## 技术栈

| 层级 | 技术 |
| --- | --- |
| **桌面端** | Electron 43 · contextBridge · 系统托盘 · electron-updater |
| **前端** | React 19 · Vite 8 · Zustand · xterm.js · Tailwind · Shiki |
| **后端** | Python 3 · FastAPI · Uvicorn · Pydantic · SQLite · LangGraph |
| **Agent 运行时** | LangChain · LangGraph · HumanInTheLoopMiddleware |
| **模型支持** | OpenAI 兼容 API · Ollama · 自定义基础 URL |
| **可扩展性** | MCP 服务器 · SKILL.md 技能 · SkillHub / ClawHub 技能市场 |
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

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
| 🗨️ **流式对话** | Agent 实时响应代码问题 |
| 🔌 **多模型支持** | OpenAI、Ollama 及任意 OpenAI 兼容 API |
| 🎯 **目标模式** | 多轮自主执行，支持暂停和恢复 |
| 🧠 **长程记忆** | 项目级和用户级记忆库，LLM 自动提取建议 |
| 🔒 **人机协作审查** | 命令执行、文件修改、MCP 调用需确认 |
| 🔄 **MCP 集成** | Model Context Protocol 支持（自动发现、持久会话） |
| 📦 **技能系统** | SKILL.md 标准技能，支持市场浏览与安装 |
| 📓 **变更追踪** | 每次文件修改记录完整审计信息，支持历史回滚 |
| 🌎 **国际化** | 完整的中文 / 英文界面 |
| 🎨 **主题** | 明暗模式，自定义强调色 |

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

## 技术栈

| 层级 | 技术 |
| --- | --- |
| **桌面端** | Electron 43 · contextBridge · 系统托盘 · electron-updater |
| **前端** | React 19 · Vite 8 · Zustand · assistant-ui · Tailwind · Shiki |
| **后端** | Python 3 · FastAPI · Uvicorn · Pydantic · SQLite |
| **Agent 运行时** | LangChain · LangGraph · HumanInTheLoopMiddleware |
| **模型支持** | OpenAI 兼容 API · Ollama · 自定义基础 URL |
| **可扩展性** | MCP 服务器 · SKILL.md 技能 · 技能市场 |
| **国际化** | 英文 / 中文 (zh) |

---

## 参与贡献

> **Bug 报告 & 反馈** — Coworker 是持续迭代的项目。遇到问题请 [提交 Issue](https://github.com/leonjackman/coworker/issues) 并附上复现步骤，我们会修复。

欢迎贡献！提交 PR 前请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

- [报告 Bug](https://github.com/leonjackman/coworker/issues) · [功能建议](https://github.com/leonjackman/coworker/issues)
- [当前开发计划](docs/tasklist/DEV-TASKS.md)

---

## 链接

- [GitHub Releases](https://github.com/leonjackman/coworker/releases) · [macOS / Windows / Linux 安装包](https://github.com/leonjackman/coworker/releases)
- [GitHub Issues](https://github.com/leonjackman/coworker/issues) · [报告 Bug 和功能建议](https://github.com/leonjackman/coworker/issues)
- [CONTRIBUTING.md](CONTRIBUTING.md) · [如何参与贡献](CONTRIBUTING.md)

---

## 开源许可

MIT — 见 [LICENSE](LICENSE)。

由 [Coworker Contributors](https://github.com/leonjackman/coworker) 构建。

# Coworker Agent

> 本地优先的 AI 编程助手桌面应用 — 用对话式界面与你的代码一起工作，支持任意 AI 语言模型。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/leonjackman/coworker/actions/workflows/release.yml/badge.svg)](https://github.com/leonjackman/coworker/actions/workflows/release.yml)
[![GitHub release](https://img.shields.io/github/v/release/leonjackman/coworker?style=flat-square)](https://github.com/leonjackman/coworker/releases/latest)

[English](README.md) · [简体中文](README.zh-CN.md)

![Coworker Banner](https://github.com/leonjackman/coworker/raw/dev/docs/screenshots/banner-logo.png)

---

### 截图

![Coworker - 欢迎页](https://github.com/leonjackman/coworker/raw/dev/docs/screenshots/welcome-dark.png)

![Coworker - 对话页](https://github.com/leonjackman/coworker/raw/dev/docs/screenshots/chat-light.png)

---

## 功能特点

- **流式对话** — Agent 实时响应代码问题
- **多模型支持** — OpenAI、Ollama 及任意 OpenAI 兼容 API
- **目标模式** — 多轮自主执行，支持暂停和恢复
- **长程记忆** — 项目和用户级记忆库，LLM 自动提取建议
- **人机协作审查** — 命令执行、文件修改、MCP 调用均需确认
- **MCP 集成** — Model Context Protocol 支持（自动发现、持久会话）
- **技能系统** — SKILL.md 标准技能，支持市场浏览与安装
- **变更追踪** — 每次文件修改记录完整审计信息，支持历史回滚
- **国际化** — 完整的中文 / 英文界面
- **主题** — 明暗模式，自定义强调色

## 安装

> **前置预览版** — Coworker 正在积极开发中，功能持续迭代完善。下载试用，发现 bug 并报告，帮助我们共同打造更好产品。

预构建安装包可从 [GitHub Releases](https://github.com/leonjackman/coworker/releases) 下载：

| 平台 | 下载 |
|---|---|
| **macOS (Apple Silicon)** | [Coworker-*.dmg](https://github.com/leonjackman/coworker/releases) — 通用构建 (ARM64) |
| **Windows 10+** | [Coworker Setup *.exe](https://github.com/leonjackman/coworker/releases) — x64 NSIS 安装器 |
| **Linux (x64)** | [Coworker-*.AppImage](https://github.com/leonjackman/coworker/releases) — 需要 FUSE |

> macOS 构建未签名/未公证。首次运行可能需要 `xattr -d com.apple.quarantine /Applications/Coworker.app`。

## 快速开始

### 桌面应用 (macOS)

项目提供了一个 macOS 启动器脚本，可从源码运行应用：

```bash
./coworker_desktop.command
```

该脚本会自动安装依赖、构建前端、启动后端（FastAPI）并打开 Electron 应用。

免打开桌面的冒烟测试：

```bash
COWORKER_SKIP_DESKTOP=1 ./coworker_desktop.command
```

启动器目前仅支持 macOS。Windows 和 Linux 可能可用但不保证。

**所有平台均可**通过 [GitHub Releases](https://github.com/leonjackman/coworker/releases) 下载预构建安装包使用。

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

## 技术栈

| 层级 | 技术 |
|---|---|
| **桌面端** | Electron 43 · contextBridge · 系统托盘 · electron-updater |
| **前端** | React 19 · Vite 8 · Zustand · assistant-ui · Tailwind CSS · Shiki |
| **后端** | Python 3 · FastAPI · Uvicorn · Pydantic · SQLite |
| **Agent 运行时** | LangChain · LangGraph · HumanInTheLoopMiddleware |
| **模型支持** | OpenAI 兼容 API · Ollama · 自定义基础 URL |
| **可扩展性** | MCP 服务器 · SKILL.md 技能 · 技能市场 |
| **国际化** | 英文 / 中文 (zh) |

## 参与贡献

> **Bug 报告 & 反馈** — Coworker 是持续迭代的项目。遇到问题请 [提交 Issue](https://github.com/leonjackman/coworker/issues) 并附上复现步骤，我们会修复。

欢迎贡献！提交 PR 前请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

- [反馈 Bug](https://github.com/leonjackman/coworker/issues) / [功能建议](https://github.com/leonjackman/coworker/issues)
- 查看 [docs/tasklist/DEV-TASKS.md](docs/tasklist/DEV-TASKS.md) 了解当前开发计划

## 开源许可

MIT — 见 [LICENSE](LICENSE)。

由 [Coworker Contributors](https://github.com/leonjackman/coworker) 构建。

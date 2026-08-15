# Coworker Agent

> 本地优先的 AI 编程助手桌面应用 — 多模型支持、单 Agent、HITL 人机协作、长程记忆、可扩展技能。

| macOS | Windows | Linux |
|---|---|---|
| [下载 .dmg](https://github.com/leonjackman/coworker/releases) | [下载 .exe](https://github.com/leonjackman/coworker/releases) | [下载 .AppImage](https://github.com/leonjackman/coworker/releases) |

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/leonjackman/coworker/actions/workflows/release.yml/badge.svg)](https://github.com/leonjackman/coworker/actions/workflows/release.yml)
[![npm](https://img.shields.io/badge/dynamic/json?label=github&url=https%3A%2F%2Fapi.github.com%2Frepos%2Fleonjackman%2Fcoworker%2Freleases%2Flatest&query=%24.tag_name&style=flat-square)](https://github.com/leonjackman/coworker/releases/latest)

[English](README.md) · [简体中文](README.zh-CN.md)

![Coworker Banner](docs/screenshots/banner-logo.png)

---

### 截图

| 深色主题 | 浅色主题 |
|---|---|
| ![欢迎页 (Dark)](docs/screenshots/welcome-dark.png) | ![对话页 (Light)](docs/screenshots/chat-light.png) |

---

## 功能特点

| 类别 | 说明 |
|---|---|
| 🧠 **长程记忆** | 项目级和用户级记忆库，LLM 自动提取建议（人机确认）。原子写入 + 漂移检测 + 冲突合并。 |
| 🔄 **目标模式** | 持久化多轮自主执行：支持暂停、恢复、编辑、停止。Agent 可循环执行数千次工具调用。 |
| 🔒 **默认 HITL** | 命令执行、文件写入、MCP 调用、记忆修改前都需要人类确认。三种自主级别：监督、保护、完全自动。 |
| 🔌 **MCP + 技能系统** | 完整的 Model Context Protocol 支持（自动发现、持久会话、OAuth）。技能市场（腾讯 SkillHub / ClawHub）。 |
| 🌐 **多模型支持** | 支持任意 OpenAI 兼容 API、Ollama、自定义端点 — 不绑定任何单一厂商。 |
| 📦 **真正的本地优先** | 所有数据存储在本地。API 密钥存入系统 Keychain。无云依赖。MIT 开源许可。 |
| 📓 **变更追踪与回滚** | 每次文件修改都会记录完整的 before/after。可回滚到任意历史消息状态，冲突安全。 |
| 🎨 **国际化 + 主题** | 中文/英文界面。明暗主题 + 自定义强调色 + 毛玻璃效果。 |

---

## 安装

> **前置预览版** — Coworker 正在积极开发中，功能持续迭代完善。下载试用，发现 bug 并报告，帮助我们共同打造更好产品。

预构建安装包可从 [GitHub Releases](https://github.com/leonjackman/coworker/releases) 下载。

| 平台 | 下载文件 | 说明 |
|----------|-----------------|-------|
| **macOS (Apple Silicon)** | `Coworker-*.dmg` | 通用构建 (ARM64)。未签名/未公证。首次运行可能需要 `xattr -d com.apple.quarantine /Applications/Coworker.app`。 |
| **Windows 10+** | `Coworker Setup *.exe` | x64 NSIS 安装器。可能触发 SmartScreen 警告，待添加代码签名后会消失。 |
| **Linux (x64)** | `Coworker-*.AppImage` | 需要 FUSE。先设置可执行权限 (`chmod +x`)。 |

---

## 快速开始

### 桌面应用

```bash
./coworker_desktop.command
```

启动脚本会自动完成以下操作：

1. 创建或复用 `backend/venv`
2. 安装 Python 和 Node 依赖
3. 构建 Vite 前端
4. 启动 FastAPI 服务 (127.0.0.1:9527)
5. 启动 Electron 应用

免打开桌面的冒烟测试：

```bash
COWORKER_SKIP_DESKTOP=1 ./coworker_desktop.command
```

### 配置模型

在应用的 **设置 → 提供商** 中添加 OpenAI 兼容的 AI 服务商：

- 设置基础 URL、模型名称、API 密钥
- 密钥会存入系统 Keychain（macOS）或 0600 权限文件（备用）
- 使用内置 "测试" 按钮检查连接

使用本地模型 Ollama：

```bash
# 在设置 > 提供商中填写：
基础 URL: http://localhost:11434/v1
模型: 你的模型名称
```

---

## 开发环境

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

## 验证

```bash
cd frontend && npx tsc --noEmit
backend/venv/bin/python -m compileall backend/main.py backend/coworker
backend/venv/bin/python -m coworker.memory.selftest
COWORKER_SKIP_DESKTOP=1 ./coworker_desktop.command
```

---

## 技术栈

| 层级 | 技术 |
|-------|-------------|
| **桌面端** | Electron 43 · contextBridge · 系统托盘 · electron-updater |
| **前端** | React 19 · Vite 8 · Zustand · assistant-ui · Tailwind CSS 4 · Shiki · xterm.js |
| **后端** | Python 3 · FastAPI · Uvicorn · Pydantic · SQLite |
| **Agent 运行时** | LangChain · LangGraph · SqliteSaver · HumanInTheLoopMiddleware |
| **模型支持** | OpenAI 兼容 API · Ollama · 自定义基础 URL |
| **可扩展性** | MCP 服务器 (stdio/HTTP/SSE/WebSocket) · SKILL.md 技能 · 技能市场 |
| **国际化** | 英文 / 中文 (zh) |

---

## 参与贡献

> **Bug 报告 & 反馈** — Coworker 是持续迭代的项目。遇到问题请 [提交 Issue](https://github.com/leonjackman/coworker/issues) 并附上复现步骤，我们会修复。

欢迎贡献！提交 PR 前请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

- [反馈 Bug](https://github.com/leonjackman/coworker/issues) / [功能建议](https://github.com/leonjackman/coworker/issues)
- 查看 [docs/tasklist/DEV-TASKS.md](docs/tasklist/DEV-TASKS.md) 了解当前开发计划

---

## 开源许可

MIT — 见 [LICENSE](LICENSE)。

由 [Coworker Contributors](https://github.com/leonjackman/coworker) 构建。

# Coworker Agent

> Local-first AI coding assistant desktop app — chat with your code, powered by any LLM.

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

### Screenshots

<p align="center">
  <img src="https://github.com/leonjackman/coworker/raw/dev/docs/screenshots/welcome-dark.png" width="100%" alt="Coworker - Welcome">
</p>

<p align="center">
  <img src="https://github.com/leonjackman/coworker/raw/dev/docs/screenshots/chat-light.png" width="100%" alt="Coworker - Chat">
</p>

---

## Features


| Feature | Description |
| --- | --- |
| 🗨️ **Streaming Chat** | Real-time agent responses via SSE |
| 🔌 **Multi-Provider** | OpenAI, Ollama, and any OpenAI-compatible API |
| 🎯 **Goal Mode** | Multi-turn autonomous sessions with pause and resume |
| 🧠 **Long-Term Memory** | Project and user scopes with LLM auto-extract proposals |
| 🔒 **Human-In-The-Loop** | Approves commands, file writes, MCP tools before they run |
| 🔄 **MCP Integration** | Model Context Protocol with auto-discovery and persistent sessions |
| 📦 **Skills** | SKILL.md-based skills with marketplace browsing |
| 📓 **Change Tracking** | Every file change logged with rollback to any past state |
| 🌎 **i18n** | Full Chinese / English interface |
| 🎨 **Theme** | Dark / light with custom accent colors |

---

## Install

> **Pre-release** — Coworker is under active development. Features are added and improved regularly. Download to try it out, report bugs, and help shape the road ahead.

### Install from Releases (Recommended)

Pre-built desktop installers are available from [GitHub Releases](https://github.com/leonjackman/coworker/releases):

| Platform | Install |
| --- | --- |
| **macOS (Apple Silicon)** | [Download .dmg](https://github.com/leonjackman/coworker/releases) |
| **Windows 10+** | [Download .exe](https://github.com/leonjackman/coworker/releases) |
| **Linux (x64)** | [Download .AppImage](https://github.com/leonjackman/coworker/releases) |

> macOS builds are unsigned / un-notarized. May require `xattr -d com.apple.quarantine /Applications/Coworker.app` on first launch.

### Install from Source

Clone the repository and run the development environment (see [below](#development)).

---

## Quick Start

### Desktop App (macOS, from source)

```bash
./coworker_desktop.command
```

This script installs dependencies, builds the frontend, starts the backend (FastAPI), and launches the Electron app.

For smoke-testing without opening the desktop window:

```bash
COWORKER_SKIP_DESKTOP=1 ./coworker_desktop.command
```

For all platforms, download the pre-built installer from [GitHub Releases](https://github.com/leonjackman/coworker/releases).

### Configure an LLM Provider

Open **Settings → Providers** in the app UI:

- Set the base URL, model name, and API key
- Keys are stored in the macOS Keychain (or a 0600-protected file)
- Use the built-in "Test" button to verify connectivity

For local models via Ollama:

```text
Base URL: http://localhost:11434/v1
Model: your_model_name
```

---

## Development

### Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 9527
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Full Dev Mode

```bash
# Terminal 1 — FastAPI backend
cd backend && source venv/bin/activate && uvicorn main:app --host 127.0.0.1 --port 9527

# Terminal 2 — Vite dev server
cd frontend && npm run dev

# Terminal 3 — Electron (points to Vite dev server)
NODE_ENV=development npx electron . --no-sandbox
```

---

## What Makes Coworker Different

| Traditional coding assistants | Coworker |
| --- | --- |
| Cloud-dependent, data leaks via API | **True local-first** — all data stays on your machine |
| Tied to one provider or vendor | **Multi-provider** agnostic — any OpenAI-compatible API, Ollama, custom |
| Basic chat with no memory beyond session | **Long-term memory** — auto-extract from interactions, persist across sessions |
| No control over agent actions | **HITL by default** — manually approve every command and file change |
| No audit trail | **Full trace** — export agent traces, tool audit logs, rollback to any state |
| Black-box tool execution | **Transparent** — every change logged with before/after diffs in a human-readable format |

---

## Technology Stack

| Layer | Technologies |
| --- | --- |
| **Desktop** | Electron 43 · contextBridge · tray · electron-updater |
| **Frontend** | React 19 · Vite 8 · Zustand · assistant-ui · Tailwind · Shiki |
| **Backend** | Python 3 · FastAPI · Uvicorn · Pydantic · SQLite |
| **Agent** | LangChain · LangGraph · HumanInTheLoopMiddleware |
| **Models** | OpenAI-compatible APIs · Ollama · custom base URLs |
| **Extensibility** | MCP servers · SKILL.md skills · Skill marketplace |
| **i18n** | English / Chinese (zh) |

---

## Contributing

> **Bug reports & feedback** — Coworker is an evolving project. If something doesn't work, please [open an issue](https://github.com/leonjackman/coworker/issues) with steps to reproduce. We'll fix it.

Contributions are welcome! Please read [CONTRIBUTING](CONTRIBUTING.md) · [贡献指南](CONTRIBUTING.zh-CN.md).

- [Report bugs](https://github.com/leonjackman/coworker/issues) · [Request features](https://github.com/leonjackman/coworker/issues)

---

## Links

- [GitHub Releases](https://github.com/leonjackman/coworker/releases) · [Installers for macOS, Windows, Linux](https://github.com/leonjackman/coworker/releases)
- [GitHub Issues](https://github.com/leonjackman/coworker/issues) · [Report bugs and request features](https://github.com/leonjackman/coworker/issues)
- [CONTRIBUTING.md](CONTRIBUTING.md) · [How to contribute](CONTRIBUTING.md)

---

## License

MIT — see [LICENSE](LICENSE).

Built by [Coworker Contributors](https://github.com/leonjackman/coworker).

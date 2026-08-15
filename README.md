# Coworker Agent

> Local-first AI coding assistant desktop app — chat with your code, powered by any LLM.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/leonjackman/coworker/actions/workflows/release.yml/badge.svg)](https://github.com/leonjackman/coworker/actions/workflows/release.yml)
[![GitHub release](https://img.shields.io/github/v/release/leonjackman/coworker?style=flat-square)](https://github.com/leonjackman/coworker/releases/latest)

[English](README.md) · [简体中文](README.zh-CN.md)

![Coworker Banner](https://github.com/leonjackman/coworker/raw/dev/docs/screenshots/banner-logo.png)

---

### Screenshots

![Coworker - Welcome](https://github.com/leonjackman/coworker/raw/dev/docs/screenshots/welcome-dark.png)

![Coworker - Chat](https://github.com/leonjackman/coworker/raw/dev/docs/screenshots/chat-light.png)

---

## Features

- **Streaming chat** with real-time agent responses
- **Multi-provider** — OpenAI, Ollama, and any OpenAI-compatible API
- **Goal mode** — multi-turn autonomous sessions with pause and resume
- **Long-term memory** — project and user scopes with LLM auto-extract proposals
- **Human-in-the-loop** — approves commands, file writes, MCP tools before they run
- **MCP integration** — Model Context Protocol with auto-discovery and persistent sessions
- **Skills** — SKILL.md-based skills with marketplace browsing
- **Change tracking** — every file change logged with rollback to any past state
- **i18n** — full Chinese / English interface support
- **Theme** — dark / light mode with custom accent colors

## Install

> **Pre-release** — Coworker is under active development and improvements are added regularly. Download to try it out, report bugs, and shape the road ahead.

Pre-built desktop installers are available from [GitHub Releases](https://github.com/leonjackman/coworker/releases):

| Platform | Download |
|---|---|
| **macOS (Apple Silicon)** | [Coworker-*.dmg](https://github.com/leonjackman/coworker/releases) — Universal build (ARM64) |
| **Windows 10+** | [Coworker Setup *.exe](https://github.com/leonjackman/coworker/releases) — x64 NSIS installer |
| **Linux (x64)** | [Coworker-*.AppImage](https://github.com/leonjackman/coworker/releases) — Requires FUSE |

> macOS builds are unsigned / un-notarized. First launch may need `xattr -d com.apple.quarantine /Applications/Coworker.app`.

## Quick Start

### Desktop App (macOS)

The project includes a launcher script for macOS to run the app from source:

```bash
./coworker_desktop.command
```

This script installs dependencies, builds the frontend, starts the backend (FastAPI), and launches the Electron app.

For smoke-testing without opening the desktop:

```bash
COWORKER_SKIP_DESKTOP=1 ./coworker_desktop.command
```

The launcher currently supports macOS only. Windows and Linux may work but are not guaranteed.

**For all platforms**, download the pre-built installer from [GitHub Releases](https://github.com/leonjackman/coworker/releases).

### Configure an LLM Provider

Open **Settings → Providers** in the app UI to add an AI model:

- Set the base URL, model name, and API key
- Keys are stored in the macOS Keychain (or a 0600-protected file)
- Use the built-in "Test" button to verify connectivity

For local models via Ollama:

```text
Base URL: http://localhost:11434/v1
Model: your_model_name
```

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

## Technology Stack

| Layer | Technologies |
|---|---|
| **Desktop** | Electron 43 · contextBridge · tray · electron-updater |
| **Frontend** | React 19 · Vite 8 · Zustand · assistant-ui · Tailwind CSS · Shiki |
| **Backend** | Python 3 · FastAPI · Uvicorn · Pydantic · SQLite |
| **Agent** | LangChain · LangGraph · HumanInTheLoopMiddleware |
| **Models** | OpenAI-compatible APIs · Ollama · custom base URLs |
| **Extensibility** | MCP servers · SKILL.md skills · Skill marketplace |
| **i18n** | English / Chinese (zh) |

## Contributing

> **Bug reports & feedback** — Coworker is an evolving project. If something doesn't work, please [open an issue](https://github.com/leonjackman/coworker/issues) with steps to reproduce. We'll fix it.

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md).

- Report bugs and request features on [Issues](https://github.com/leonjackman/coworker/issues)
- Check [docs/tasklist/DEV-TASKS.md](docs/tasklist/DEV-TASKS.md) for the current development plan

## License

MIT — see [LICENSE](LICENSE).

Built by [Coworker Contributors](https://github.com/leonjackman/coworker).

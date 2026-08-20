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
| 🗨️ **Streaming Chat** | Real-time agent responses via SSE with keep-alive heartbeats; multiple sessions stream in parallel |
| 🔌 **Multi-Provider** | OpenAI, Ollama, and any OpenAI-compatible API, with live context-window discovery |
| 🎯 **Goal Mode** | Multi-turn autonomous sessions — pause, resume, edit, stop, round caps, and to-do tracking |
| 🧠 **Long-Term Memory** | Per-agent / per-project markdown memory with LLM auto-extract and zip export / import |
| 👥 **Multi-Agent Teams** ⚠️ | Create teams & departments and let agents delegate tasks to each other. **Experimental** — see note below |
| 🔒 **Human-In-The-Loop** | Approves commands, file writes, and MCP tools before they run — with supervised / guarded / autonomous levels |
| 🔄 **MCP Integration** | Model Context Protocol — stdio / HTTP / SSE / WebSocket transports, OAuth 2.1 + PKCE, template discovery, persistent sessions |
| 📦 **Skills** | SKILL.md-based skills with marketplace browsing and one-click install (SkillHub · ClawHub) |
| 📓 **Change Tracking** | Every file change logged with before/after diffs; edit / regenerate / revert restores the state before changes |
| 🖥️ **Integrated Terminal** | Interactive PTY shell in the bottom panel, plus a live tool-audit feed |
| 🔎 **Audit & Traces** | Tool-audit log and agent traces with export, clear, and retention caps |
| ✏️ **Message Editing** | Edit or regenerate any user message — downstream code changes are rolled back and can be restored |
| 🌎 **i18n** | 11 languages — English, Chinese (Simplified / Traditional), Japanese, Korean, French, German, Spanish, Portuguese, Russian |
| 🎨 **Theme** | Dark / light / system with custom accent colors |

> ⚠️ **Multi-Agent (Experimental)** — Multi-agent teams, departments, and delegation are an experimental capability still under active development: the feature set is not yet complete, behavior may change, and the project mode is immutable after creation. Prefer single-agent mode for daily work.

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

## Long-Term Memory

Coworker keeps a per-agent, per-project Markdown memory library at
`~/Library/Application Support/Coworker/memory/` (macOS; the data dir follows
`COWORKER_DATA_DIR`). All files are plain, human-editable Markdown.

```
memory/
├── MEMORY.md · USER.md · AGENT.md      # system-level facts (user-maintained)
└── <project>/                          # one timestamp-named dir per project
    ├── BASE/                           # user-maintained project facts
    └── <agent>/
        ├── BASE/                       # SOUL · AGENT · MEMORY.md (agent facts)
        │   └── DREAMS.md               # auto-extraction review diary
        └── SESSIONS/<date>.md          # per-day session notes (auto + manual)
```

### How it works

- **Injection (read)** — each turn the relevant memory files are injected into
  the system prompt (bounded by `memory.char_limit`, default 2000 chars).
  Extra files and session notes are read on demand with the `memory_read` tool.
- **Manual write** — the agent uses the `memory` tool to write durable facts to
  its own `BASE/MEMORY.md` (or a topic file / `SESSIONS/<date>.md`), deduplicated
  and approval-gated in supervised mode.
- **Auto-extract (dream)** — every N turns (controlled by `COWORKER_MEMORY_NUDGE_INTERVAL`, default 10), a background
  pass reviews the recent transcript with an LLM and:
  1. extracts durable facts and consolidates them into the agent's `MEMORY.md`;
  2. appends a short session note to `SESSIONS/<date>.md` (once per day);
  3. logs a line in `DREAMS.md` (e.g. `consolidated · new 3`).

Auto-extract uses the provider configured under memory settings
(`memory_extract_model`; falls back to the default provider). It is controlled
by `COWORKER_MEMORY_ENABLED` and `COWORKER_MEMORY_AUTO_EXTRACT` (both default
on).

### How it stays bounded

Memory files are governed lazily (at write time, no background job):

- **`MEMORY.md` / `USER.md`** — consolidation keeps the file under the injection
  budget (default 4000 chars); the append-only fallback is FIFO-capped at the
  same budget (oldest entries are dropped first, the newest fact is never lost).
- **`DREAMS.md`** — only the current month's entries stay in the live file;
  older months are moved to `ARCHIVE/DREAMS-YYYY-MM.md`.
- **`SESSIONS/<date>.md`** — day files older than the current month are merged
  into `ARCHIVE/SESSIONS-YYYY-MM.md` and removed.

Archives live under `<agent>/ARCHIVE/` — they are not injected into the prompt
and stay readable on demand via `memory_read`.

### How to verify

1. In a chat, state a durable fact, e.g. "I prefer replies in Chinese" or
   "this project's backend binds port 9527".
2. Stop chatting for a few turns (or wait for the nudge interval).
3. Check `memory/<project>/default_agent/BASE/`:
   - `MEMORY.md` gains a new entry;
   - `DREAMS.md` shows `new N · consolidated` (not `new 0`);
   - `SESSIONS/<today>.md` contains a session note.
4. For detail, restart with `COWORKER_LOG_LEVEL=DEBUG` and watch `app.log` for
   `dream done ... added=N transcript=… chars`.

Run the memory self-checks with:

```
cd backend && ./venv/bin/python coworker/memory/selftest.py
```

---

## Technology Stack

| Layer | Technologies |
| --- | --- |
| **Desktop** | Electron 43 · contextBridge · tray · electron-updater |
| **Frontend** | React 19 · Vite 8 · Zustand · xterm.js · Tailwind · Shiki |
| **Backend** | Python 3 · FastAPI · Uvicorn · Pydantic · SQLite · LangGraph |
| **Agent** | LangChain · LangGraph · HumanInTheLoopMiddleware |
| **Models** | OpenAI-compatible APIs · Ollama · custom base URLs |
| **Extensibility** | MCP servers · SKILL.md skills · SkillHub / ClawHub marketplaces |
| **i18n** | en · zh · zh-TW · zh-HK · ja · ko · fr · de · es · pt-BR · ru |

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

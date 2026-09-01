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
| 💬 **Built-in Chat Project** | A system-reserved "Chat" project ships on first launch — start a casual conversation without creating a project. It lives in its own sandbox folder, is pinned to the top of the sidebar, and can't be deleted or renamed; the agent there adopts a relaxed **Lazzzy Boy** persona and only touches files / commands when you explicitly ask |
| 📥 **Message Queue & Interject** | Keep typing while the agent works — sends queue up per session and auto-send one-by-one when the stream finishes; interject (↳) any queued message to steer the running reply without interrupting it |
| 🔌 **Multi-Provider** | 32 built-in provider presets — OpenAI, Anthropic, Google Gemini, DeepSeek, Qwen / DashScope, Moonshot (Kimi), Zhipu (GLM), Doubao, Minimax, Cohere, Groq, xAI, Mistral, Ollama, vLLM, OpenRouter, SiliconFlow and more — plus any OpenAI-compatible custom endpoint, with live context-window discovery |
| 🧠 **Long-Term Memory** | Per-agent / per-project markdown memory with LLM auto-extract, zip export / import, trash recovery, and cross-directory migration |
| 👥 **Multi-Agent Teams** ⚠️ | Create teams & departments and let agents delegate tasks to each other. **Experimental** — see note below |
| 👤 **Sub-Agent Workers** | Spawn independent sub-agents in single-agent mode for parallel or sequential tasks, each with its own LLM graph, memory, and restricted toolset |
| 🎯 **Goal Mode** | Set a persistent `/goal` and let the agent drive multi-round autonomous execution until complete or blocked, with a pinned progress card and pause / resume / clear controls |
| 🔄 **MCP Integration** | Model Context Protocol — stdio / HTTP / SSE / WebSocket / Streamable HTTP transports, OAuth 2.1 + PKCE, template discovery, persistent sessions |
| 📦 **Skills** | SKILL.md-based skills with marketplace browsing, one-click install (SkillHub · ClawHub), and in-chat install via the agent — plus **self-authoring**: the agent captures repeatable procedures as draft skills that wait in a review queue for your approval |
| 🌐 **Web Search & Fetch** | Web search powered by [Tavily](https://tavily.com) (`web_search`) and web page fetching (`web_fetch`), with configurable search depth, result count, Cloudflare retry, and secure keychain storage for your API key |
| 🖥️ **Built-in Browser** | Embedded Chromium view the agent can drive — navigate, click, type, scroll, screenshot, evaluate JS, and read DOM; right-click to capture elements or the whole page into your chat |
| 🔒 **Human-In-The-Loop** | Approves commands, file writes, and MCP tools before they run — with supervised / guarded / autonomous levels |
| 📓 **Change Tracking** | Every file change logged with before/after diffs; edit / regenerate / revert restores the state before changes |
| 🖥️ **Integrated Terminal** | Interactive PTY shell in the bottom panel, plus a live tool-audit feed |
| 🔎 **Audit & Traces** | Tool-audit log and agent traces with export, clear, and retention caps |
| ✏️ **Message Editing** | Edit or regenerate any user message — downstream code changes are rolled back and can be restored |
| 📎 **File Attachments** | Send text or binary files in chat messages (default 25 MB limit, configurable 1–1024 MB); the agent reads content directly |
| 🔗 **Session Cross-Reference** | Paste a session ID in chat so the agent can read context from other sessions |
| 🛡️ **Sensitive File Protection** | Blocked reads of `.env`, `.pem`, `.key`, `id_rsa`, etc. and enforced workspace boundary for all file writes |
| 📋 **Plan / Build Work Mode** | "Plan" is read-only — the agent can only view, search, and create plans. Switch to "Build" to unlock full write and execute capabilities |
| 🌎 **i18n** | 11 languages — English, Chinese (Simplified / Traditional / HK), Japanese, Korean, French, German, Spanish, Portuguese, Russian |
| 🎨 **Theme** | 10 curated OKLCH presets (Mineral, Hermes, Ember, Sage, Graphite, Azure, Nocturne, Solarized, Monokai, Violet), each with light/dark palettes, plus custom accent colors |
| 🔊 **Sound Notifications** | Audio feedback on agent reply done, errors, and attention events, with a global toggle |
| 📊 **Context Budget Indicator** | Live context-window usage bar showing token / character consumption and compaction tracking, so you always know your budget |
| 🔄 **Auto-Update** | Supports pre-release channels, progress bar, version skip, error classification, and local version notifications |
| 📊 **Project Dashboard** | Per-project overview page — files, agents, git status, and session history at a glance, with a keyboard-navigable file tree, rich file previews (code highlight, CSV / XLSX tables), and open-in-external-app |

> ⚠️ **Multi-Agent (Experimental)** — Multi-agent teams, departments, and delegation are an experimental capability still under active development: the feature set is not yet complete, behavior may change, and the project mode is immutable after creation. Prefer single-agent mode for daily work.

---

## About Coworker

**Coworker is a new product actively under rapid development.** New features are shipped frequently and breaking changes may happen. Some features are still in progress or may contain bugs. We encourage you to try it out, explore, report issues, and give feedback — your input directly shapes the direction of this project.

New releases are published irregularly as the team moves quickly. Star the repo or watch releases to stay up to date.

---

## Product Positioning

Coworker is a local-first, fully-featured AI coding assistant. Unlike traditional cloud-based coding tools, Coworker's core value proposition is:

- **Privacy first** — all data (code, memory, chat logs) stays on your machine; nothing touches a third-party server
- **Model agnostic** — no vendor lock-in; use OpenAI-compatible APIs, Ollama, or any custom endpoint
- **Full-powered Agent** — web search, browser automation, file management, sub-agents, MCP extensions, and a skill marketplace
- **Human-in-the-loop** — built-in review mechanism so you approve every critical action
- **Fully traceable** — complete audit trails, agent trace export, rollback to any state
- **Extensible** — MCP protocol + SKILL.md skill system + marketplace ecosystem

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

- Pick from 32 built-in provider presets (OpenAI, Anthropic, Google Gemini, DeepSeek, Qwen, Moonshot, Zhipu, and more), or add a custom OpenAI-compatible endpoint
- Set the base URL, model name, and API key for the chosen provider
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

## Chat Project (Built-in Casual Chat)

Coworker ships with a system-reserved **Chat** project pinned to the top of the
sidebar, so you can start a casual conversation right away — no need to create a
project first.

- **Zero-setup chat** — on first launch the onboarding screen offers "Start
  chatting"; the global new-chat action falls back to the Chat project when you
  have no real projects yet.
- **Always there & safe** — it is created automatically at startup and
  self-heals if its folder is deleted; it cannot be deleted or renamed. Its
  workspace is a system-designated sandbox folder (`COWORKER_DATA_DIR/chat`),
  fully isolated from your real projects.
- **Lazzzy Boy persona** — the agent in the Chat project adopts the relaxed
  "lazy boy" personality of the [Lazzzy Boy](https://lazzzyboy.com) studio:
  casual, concise, and smart. Unless you explicitly ask, it won't read or edit
  files, run commands, or call search / MCP tools — it just talks. (Tools remain
  available, so it can still work inside the sandbox on request.)
- **Localized name** — the project name follows your UI language
  (Chat / 聊天 / チャット …).

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
| Limited tool capabilities | **Rich tool ecosystem** — web search, built-in browser, sub-agents, MCP extensions, SKILL.md skill marketplace |

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

## Message Queue (Queue While Streaming)

While the agent is replying you don't have to wait — keep typing and send your
next message right away.

- **Composer stays editable during streaming** — the input box is never locked
  while a reply is in progress; only the "connecting" phase disables it.
- **Sending queues the message** — pressing Enter / the send button while a
  stream is running does *not* start a parallel turn. The message is appended to
  a per-session FIFO queue and auto-sends as the next request once the current
  stream finishes (`done` / error / stopped). Send without content shows the
  Stop button instead, so you can still interrupt the running task.
- **Queued messages are visible** — each pending message is listed in the
  task-list card above the composer (one row per message) with an **Interject now**
  (↳) action and a **✕** remove action, so nothing is hidden behind an icon.
- **Edits & regenerations stay serialized** — only one stream runs per session;
  queued messages never race the stream they are waiting for.

### How interrupted turns restart cleanly

If a stream is aborted (Stop, client disconnect), the backend marks the session
as interrupted. The **next** `/chat/stream` forgets the dirty runtime checkpoint
and rebuilds from the session history, so the new (or first queued) message
becomes the active instruction instead of the model continuing the original task
from a stale mid-task checkpoint.

### Interject (guide a running task)

While the agent is streaming, pick any queued message and hit **Interject now**
(↳) to steer the reply **without stopping or restarting it**.

- **Guides within the current run** — interjecting does not abort the in-flight
  stream. The message is handed to the running agent and folded into its **next
  model call**, so the LLM's subsequent output and tool steps follow your steer.
- **Shown as a "Steered" card** — the interjected text appears as a notice
  inside the assistant bubble (not as a separate user bubble), keeping the
  transcript clean.
- **Never lost on a late steer** — if the task already finished before the steer
  could be consumed, the interjection is automatically continued as the next
  turn instead of being dropped.

> Queued messages auto-send one-by-one once the stream settles; use **Interject
> now** when you want guidance to take effect *within* the current run instead
> of waiting for it to finish.

---

## Goal Mode

Set a persistent objective with `/goal <objective>` and the agent keeps working
across multiple rounds **without further input** until it marks the goal
`complete` or `blocked`. A progress card (objective, status, elapsed time,
token-budget bar) is pinned in the task panel.

- **Input-box Stop** stops the current task; the goal stays `active` and needs a
  kick (new message / resume / reopen) to continue.
- **TodoBlock pause / resume / clear** control the goal itself — pausing lets the
  current round finish, then stops further rounds without aborting the run.
- Goals are **strictly per-session** and never affect other sessions.
- If a human-in-the-loop approval or question is raised mid-run, the loop pauses
  and resumes after you approve.

---

## Project Dashboard

Every project has a read-only dashboard that makes its files, agents, git
status and session history visible at a glance.

- **Five tabs** — Overview (project info, git status, and stats) · Files · Agents · Memory · Session history.
- **File explorer** — a keyboard-navigable file tree (ARIA accessible) with
  rich previews: syntax-highlighted code with line numbers, virtual-scrolled
  CSV / XLSX tables, and a draggable split pane.
- **Open externally** — any file can be opened in your system's default app
  right from the dashboard.
- **Quick access** — open it from the sidebar or with the `Cmd+O` shortcut.

---

## Logging & Observability

All logs are written to the app data directory by default (macOS:
`~/Library/Application Support/Coworker`; override with `COWORKER_DATA_DIR`).

| File | Contents | Governance |
| --- | --- | --- |
| `app.log` + `app.log.N` | Main runtime log (JSON, one record per line) | Rotated by size (default 10 MB × 5) |
| `agent_trace.jsonl` | Agent activity trace (events / state / context) | Rolling retention, default last 100 (adjustable in settings) |
| `tool_audit.jsonl` | Tool-call audit | Rolling retention, default last 100 (adjustable in settings) |
| `command_approvals.json` | Command approval records | Keeps last 100 + guaranteed 25 |
| `worker_events/<run>.jsonl` | Sub-agent run stream events (replayable) | Last 200 runs / total ≤ 100 MB |

Controls:

- **Env vars**: `COWORKER_LOG_LEVEL`, `COWORKER_LOG_MAX_BYTES`,
  `COWORKER_LOG_BACKUP_COUNT`, `COWORKER_JSON_LOG`, `COWORKER_HTTP_LOG`
  (request log toggle), `COWORKER_WORKER_EVENTS_MAX_RUNS` /
  `COWORKER_WORKER_EVENTS_MAX_BYTES`.
- **Runtime API**: `POST /settings/log-level` (switch level, persists),
  `POST /settings/log-config` (level / rotation / JSON / request log, applies
  immediately and persists), `POST /settings/truncate-log` (clear or keep last N
  bytes), `GET /settings/log-file` (paginated read).
- **Request log**: every HTTP request is recorded via `coworker.http`
  (method / path / status / latency / `request_id`); health-check and log-read
  endpoints are skipped; token / key query params are redacted.
- **Session correlation**: JSON records in `app.log` automatically carry
  `session_id` (session-related requests and the whole `/chat/stream` /
  `/chat/interject` flow), so you can `grep '"session_id":"<id>"'` to pull a
  single session's runtime log.

---

## Technology Stack

| Layer | Technologies |
| --- | --- |
| **Desktop** | Electron 43 · contextBridge · tray · electron-updater |
| **Frontend** | React 19 · Vite 8 · Zustand · xterm.js · Tailwind · Shiki |
| **Backend** | Python 3 · FastAPI · Uvicorn · Pydantic · SQLite · LangGraph |
| **Agent** | LangChain 1.3 · LangGraph 1.2 · Multi-level review middlewares |
| **Models** | OpenAI-compatible APIs · Ollama · custom base URLs |
| **MCP** | MCP 1.29 · langchain-mcp-adapters 0.3 · Five transport protocols |
| **Web Search** | Tavily API (configurable provider, search depth, result count) |
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

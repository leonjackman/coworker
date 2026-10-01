# Coworker Feature Guide

> Detailed descriptions of every feature. For a quick overview, see the [README](README.md).

[English](FEATURES.md) · [简体中文](FEATURES.zh-CN.md)

---

## Chat & Sessions

| Feature | Description |
| --- | --- |
| 🗨️ **Streaming Chat** | Real-time agent responses over SSE with keep-alive heartbeats; multiple sessions can stream in parallel. |
| 💬 **Built-in Chat Project** | A system-reserved "Chat" project ships on first launch — start a casual conversation without creating a project. It lives in its own sandbox folder, is pinned to the top of the sidebar, and can't be deleted or renamed; the agent there adopts a relaxed **Lazzzy Boy** persona and only touches files / commands when you explicitly ask. |
| 📥 **Message Queue & Interject** | Keep typing while the agent works — sends queue up per session and auto-send one-by-one when the stream finishes; interject (↳) any queued message to steer the running reply without interrupting it. |
| ✏️ **Message Editing** | Edit or regenerate any user message — downstream code changes are rolled back and can be restored. |
| 🔗 **Session Cross-Reference** | Paste a session ID in chat so the agent can read context from other sessions. |
| 🧠 **Long-Term Memory** | Per-agent / per-project Markdown memory with LLM auto-extract, zip export / import, trash recovery, and cross-directory migration. |

## Agent & Models

| Feature | Description |
| --- | --- |
| 🔌 **Multi-Provider** | 32 built-in provider presets — OpenAI, Anthropic, Google Gemini, DeepSeek, Qwen / DashScope, Moonshot (Kimi), Zhipu (GLM), Doubao, Minimax, Cohere, Groq, xAI, Mistral, Ollama, vLLM, OpenRouter, SiliconFlow and more — plus any OpenAI-compatible custom endpoint, with live context-window discovery. |
| 👥 **Multi-Agent Teams** ⚠️ | Create teams and departments and let agents delegate tasks to each other. **Experimental** — the feature set is not yet complete, behavior may change, and the project mode is immutable after creation. Prefer single-agent mode for daily work. |
| 👤 **Sub-Agents** | Spawn independent sub-agents in single-agent mode for parallel or sequential tasks, each with its own LLM graph, memory, and restricted toolset. |
| 🎯 **Goal Mode** | Set a persistent `/goal` and let the agent drive multi-round autonomous execution until complete or blocked, with a pinned progress card and pause / resume / clear controls. |
| 📋 **Plan / Build Modes** | "Plan" is read-only — the agent can only view, search, and create plans. Switch to "Build" to unlock full write and execute capabilities. |
| 📊 **Context Budget** | Live context-window usage bar showing token / character consumption and compaction tracking, so you always know your budget. |

## Automation & Workflows

| Feature | Description |
| --- | --- |
| ⚙️ **Workflow Studio (Beta)** | Visual editor for versioned YAML workflows — a drag-and-drop canvas with multi-select, undo/redo and auto layout; typed inputs / outputs, error strategies, human-in-the-loop gates, a capability registry with static validation, import / export / rollback, and a standalone editor window. Trigger workflows in chat with `/workflow`. |
| 🔄 **MCP Integration** | Model Context Protocol — stdio / HTTP / SSE / WebSocket / Streamable HTTP transports, OAuth 2.1 + PKCE, template discovery, and persistent sessions. |
| 📦 **Skills** | SKILL.md-based skills with marketplace browsing, one-click install (SkillHub · ClawHub), and in-chat install via the agent — plus **self-authoring**: the agent captures repeatable procedures as draft skills that wait in a review queue for your approval. |
| 🌐 **Web Search & Fetch** | Web search powered by [Tavily](https://tavily.com) (`web_search`) and web page fetching (`web_fetch`), with configurable search depth, result count, Cloudflare retry, and secure keychain storage for your API key. |
| 🖥️ **Built-in Browser** | Embedded Chromium view the agent can drive — navigate, click, type, scroll, screenshot, evaluate JS, and read the DOM; right-click to capture elements or the whole page into your chat. Persistent profile (cookies / logins), bookmarks, history, a download manager, per-site permissions, optional tab restore (off by default), and an **OS-keychain password manager** the agent can use to sign in (with your approval). |
| 🧭 **Desktop Control** | Desktop-scale control — screenshots plus global clicks / drag / scroll / typing in any native app. Master switch off by default; every mutating action asks under default permission (Shift+⌘+Esc pauses). **Currently macOS only.** |
| 🍎 **macOS App Scripting** | `run_applescript` drives Pages / Numbers / Keynote / Finder deterministically and works **independently of the Desktop Control master switch** (execute-phase + HITL gated; excluded from sub-agents). |
| 📄 **Office & PDF Documents** | Read / create / edit / convert `docx`, `xlsx`, `pptx`, `pdf` through four unified tools (atomic writes, revertable changes); attached Office/PDF files are text-extracted instead of dumped as base64; PDF supports merge / split / rotate / delete / encrypt and Office→PDF. |

## Safety & Control

| Feature | Description |
| --- | --- |
| 🔒 **Human-in-the-Loop** | Approves boundary-crossing file writes, memory updates, and MCP tools before they run — with default / full permission levels. |
| 📓 **Change Tracking** | Every file change logged with before/after diffs; edit / regenerate / revert restores the state before changes. |
| 🔎 **Audit & Traces** | Tool-audit log and agent traces with export, clear, and retention caps. |
| 🛡️ **Sensitive File Protection** | Blocked reads of `.env`, `.pem`, `.key`, `id_rsa`, etc. and an enforced workspace boundary for all file writes. |
| 🖥️ **Integrated Terminal** | Interactive PTY shell in the bottom panel, plus a live tool-audit feed. |

## Experience

| Feature | Description |
| --- | --- |
| 📎 **File Attachments** | Send text or binary files in chat messages (default 25 MB limit, configurable 1–1024 MB); the agent reads content directly. |
| 🌎 **i18n** | 11 languages — English, Chinese (Simplified / Traditional / HK), Japanese, Korean, French, German, Spanish, Portuguese, Russian. |
| 🎨 **Themes** | 10 curated OKLCH presets (Mineral, Hermes, Ember, Sage, Graphite, Azure, Nocturne, Solarized, Monokai, Violet), each with light/dark palettes, plus custom accent colors. |
| 🔊 **Sound Notifications** | Audio feedback on agent reply done, errors, and attention events, with a global toggle. |
| 🔄 **Auto-Update** | Supports pre-release channels, progress bar, version skip, error classification, and local version notifications. |
| 📊 **Project Dashboard** | Per-project overview page — files, agents, git status, and session history at a glance, with a keyboard-navigable file tree, rich file previews (code highlight, CSV / XLSX tables), and open-in-external-app. |

---

- Back to [README](README.md) · [中文功能说明](FEATURES.zh-CN.md)

# Coworker MVP Summary

## Current Implementation Status

### Backend

- [x] FastAPI server with `/health`, `/config`, `/chat`, and `/chat/stream`.
- [x] LangChain non-streaming single-agent runtime.
- [x] LangGraph streaming ReAct single-agent runtime.
- [x] Simulated provider for local smoke tests.
- [x] OpenAI-compatible provider support through app settings or environment variables.
- [x] Provider CRUD, default provider selection, model fetching, and connection testing.
- [x] Workspace-restricted file access.
- [x] Agent tools: `search_files` and `read_file` by default, `replace_in_file`, `apply_text_edits`, `write_file`, and `run_command` only in Build + Full Access.
- [x] Local session persistence under the app data directory.
- [x] Local project grouping with standalone sessions and project sessions.
- [x] Workspace tree, directory listing, and file preview APIs.
- [x] Text attachment ingestion into the agent prompt.
- [x] JSONL audit records for writes, exact replacements, atomic structured text edits, and command execution.
- [x] Tool audit API and settings-page recent audit review UI.
- [x] One-time command approval queue for agent and bottom-panel terminal commands.

### Frontend

- [x] Electron-first React/TypeScript chat app.
- [x] Streaming assistant response rendering.
- [x] Markdown rendering with code highlighting and copy actions.
- [x] Lazy-loaded Markdown/Shiki code path to keep the main bundle smaller.
- [x] Composer controls for Plan/Build, Default/Full Access, model selection, send, stop, attachments, new chat, and slash commands.
- [x] Attachment-only sending.
- [x] Session and project sidebar modeled after a local agent workspace.
- [x] Provider settings UI.
- [x] Chinese/English localization.
- [x] Theme presets, custom color settings, light/dark switching, and translucent glass mode.
- [x] Reusable UI primitives for buttons, selects, dropdown menus, toggles, scroll areas, tooltips, and cards.

### Electron Desktop

- [x] Main process owns window creation and startup diagnostics.
- [x] Preload script exposes the renderer-safe IPC API.
- [x] Production mode loads `frontend/dist`.
- [x] System tray keeps the app alive when the main window closes.
- [x] Quitting Coworker stops the backend process.
- [x] Launcher prepares dependencies, builds frontend, starts backend, probes `/health`, and launches Electron.
- [x] Startup failure page replaces silent white-screen failure.

## Runtime Flow

1. Renderer creates a `ChatRequest` with message, session/project, provider/model, work mode, access mode, language, and attachments.
2. Electron IPC forwards the request to the main process.
3. Electron main process calls FastAPI `/chat/stream`.
4. FastAPI loads session history, formats attachments, and invokes the selected single-agent runtime.
5. LangGraph streams model/tool updates.
6. FastAPI emits SSE events.
7. Electron forwards stream events back to the renderer.
8. The renderer updates the running assistant message and persists session/project state through backend APIs.

## What Is Still Not Done

- Multi-agent company mode is not implemented.
- LangGraph checkpoint persistence is not adopted yet; Coworker currently persists chat history itself.
- LangGraph human-in-the-loop tool approval middleware is not adopted yet; Coworker currently owns command approval plus Plan/Build and Default/Full Access gating.
- Native packaging, installer, updater, and release evidence are not complete.
- Local secret storage needs hardening before public distribution.

## How to Run

```bash
./coworker_desktop.command
```

Smoke test without opening Electron:

```bash
COWORKER_SKIP_DESKTOP=1 ./coworker_desktop.command
```

## Verification Commands

```bash
cd frontend && npx tsc --noEmit
cd frontend && npm run build
backend/venv/bin/python -m compileall backend/main.py backend/coworker
node --check electron/preload.js && node --check electron/main.js
git diff --check
COWORKER_SKIP_DESKTOP=1 ./coworker_desktop.command
```

## Next Development Phase

1. Add richer coding tools with multi-file patch/diff support if needed.
2. Add LangGraph checkpoint/resume support for longer tasks.
3. Implement multi-agent runtime modes behind the existing registry boundary.
4. Harden provider secret storage.
5. Build native packaging and release validation.

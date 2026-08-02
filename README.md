# Coworker Agent

Coworker is a local-first Electron desktop agent app for coding assistance. The current product scope is a Codex-style single-agent workflow with local sessions, projects, provider configuration, streaming chat, and workspace-restricted file tools.

## Current Status

Coworker is no longer just a scaffold. The desktop launcher builds the Vite frontend, starts the FastAPI backend on `127.0.0.1:9527`, opens Electron, and stops the backend when the whole app exits. Closing the main window keeps the app alive through the system tray; quitting Coworker exits both frontend and backend.

Multi-agent company mode is explicitly out of scope for the current phase. The current shipped runtime surface is single-agent only.

## Technology Stack

- **Desktop**: Electron main process, preload IPC bridge, system tray, backend process binding
- **Frontend**: React, TypeScript, Vite, Radix/shadcn-style local UI primitives
- **Backend**: Python, FastAPI, Pydantic, JSON-file local stores, SQLite LangGraph checkpoints
- **Agent Runtime**: Coworker LangGraph planner -> executor -> verifier -> summarizer runtime; executor uses LangChain `create_agent` for tool ReAct execution
- **LLM Providers**: OpenAI-compatible providers, including OpenAI, Ollama-compatible `/v1`, and custom base URLs
- **Communication**: Renderer -> Electron IPC -> FastAPI HTTP/SSE -> Renderer stream updates

## Project Structure

```text
coworker/
├── backend/
│   ├── main.py                    # FastAPI API surface
│   └── coworker/
│       ├── agents.py              # LangChain/LangGraph single-agent runtime
│       ├── config.py              # Runtime settings
│       ├── projects.py            # Local project grouping store
│       ├── providers.py           # Provider config store and connection checks
│       ├── sessions.py            # Durable local chat session store
│       └── workspace.py           # Workspace path guard and file preview helpers
├── electron/
│   ├── main.js                    # Window, tray, IPC, backend process lifecycle
│   └── preload.js                 # Renderer-safe API bridge
├── frontend/
│   ├── src/
│   │   ├── App.tsx                # App state, streaming chat, sessions/projects
│   │   ├── components/            # Reusable UI, chat, sidebar, settings, providers
│   │   ├── lib/                   # i18n, theme, provider registry
│   │   └── services/              # Electron/HTTP chat service boundary
│   ├── index.html
│   └── vite.config.ts
├── assets/brand/                  # App and tray icons
├── coworker_desktop.command       # One-click local launcher
├── PROJECT_PLAN.md
└── README.md
```

## One-Click Run

From the project root:

```bash
./coworker_desktop.command
```

The launcher will:

1. Create or reuse `backend/venv`.
2. Install Python and Node dependencies when needed.
3. Build the Vite frontend into `frontend/dist`.
4. Start FastAPI on `127.0.0.1:9527`.
5. Launch the Electron app.
6. Stop the backend when Coworker quits.

For smoke testing without opening the desktop window:

```bash
COWORKER_SKIP_DESKTOP=1 ./coworker_desktop.command
```

## Backend Development

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 9527
```

Without a configured provider, Coworker uses the explicit simulated single-agent provider for local smoke tests. For environment-based OpenAI usage:

```bash
export COWORKER_AGENT_PROVIDER=openai
export OPENAI_API_KEY="your_openai_api_key_here"
```

Provider configuration can also be managed in the app settings. Provider records are stored under the app data directory, not in git.

## Frontend Development

```bash
cd frontend
npm install
npm run dev
```

In development mode, Electron can load the Vite dev server:

```bash
NODE_ENV=development npx electron . --no-sandbox
```

## Implemented Features

- Streaming chat through FastAPI SSE and Electron IPC.
- LangGraph-backed multi-stage single-agent runtime: planner -> executor -> verifier -> summarizer. The executor stage uses LangChain `create_agent` with Pydantic-schema tools: `search_files`, `read_file`, and gated `replace_in_file` / `apply_text_edits` / `write_file` / `run_command`.
- LangGraph SQLite checkpointing owns durable runtime message state for real provider sessions; persisted `SessionStore` remains the UI transcript owner and only bootstraps runtime state when no checkpoint exists.
- LangChain/LangGraph invocations carry standard Runnable `run_name`, `tags`, `metadata`, and `thread_id` config so LangSmith/LangChain tracing can observe the agent run when tracing environment variables are enabled.
- Local Agent trace records for run start/done/error, stage completion, and HITL interrupt state, exposed through the Runtime Observability settings panel.
- JSONL audit records and settings-page review UI for file writes, exact replacements, atomic structured text edits, and command execution.
- LangGraph human-in-the-loop middleware owns agent `run_command` approval and resumes the same checkpoint after approve/reject; bottom-panel terminal commands keep a separate one-time approval queue because they do not run inside the Agent graph.
- Plan/Build toggle and Default/Full Access toggle.
- Provider management with add/edit/delete/default provider, model fetch, and connection test.
- Durable local sessions and project grouping.
- Standalone sessions plus project sessions in the sidebar.
- Markdown rendering, code highlighting, code copy button, and lazy-loaded Shiki highlighter.
- Text attachment ingestion, attachment-only sending, attachment persistence in session history.
- Slash commands: `/help`, `/new`, `/clear`, `/providers`, `/settings`, `/plan`, `/build`.
- Chinese/English i18n.
- Theme settings with presets, user color customization, light/dark mode, and translucent glass effect.
- Electron tray behavior: close window keeps app running; quit exits frontend and backend.
- Startup diagnostics instead of silent white screen.
- Workspace tree, directory listing, and file preview APIs.

## Current Limitations

- Agent mode is currently single-agent only.
- Toolset is intentionally small: search/read files by default; exact replace, atomic structured text edits, full write, and allowlisted command execution only in Build + Full Access.
- Agent command execution pauses at a LangGraph human-in-the-loop interrupt before the process is started; approval resumes the same checkpoint. Bottom-panel terminal command execution still requires a settings-page one-time approval.
- Default provider remains simulated until a real provider is configured.
- Provider secrets are stored locally in the app data directory; production-grade credential storage is still a hardening item.
- Agent trace and tool audit records are append-only local JSONL; retention controls are still missing.
- Packaging/distribution is not complete.
- Plan/Build and Default/Full Access gating is still Coworker-owned runtime policy.

## Verification

Useful local checks:

```bash
cd frontend && npx tsc --noEmit
cd frontend && npm run build
backend/venv/bin/python -m compileall backend/main.py backend/coworker
node --check electron/preload.js && node --check electron/main.js
git diff --check
COWORKER_SKIP_DESKTOP=1 ./coworker_desktop.command
```

## Next Development Phase

1. Add a real multi-file patch/diff tool if exact structured text edits are not enough.
2. Add checkpoint retention/export controls if long-running sessions need operational management.
3. Add retention/export controls for local Agent trace, checkpoints, and tool audit records.
4. Harden local secret storage and provider validation.
5. Add packaging, updater, and release evidence.
6. Optionally migrate to assistant-ui only if it replaces the current frontend chat runtime owner instead of wrapping the existing `App.tsx` message state.

# Coworker Agent

Coworker is a local-first Electron desktop agent app for coding assistance. The current product scope is a Codex-style single-agent workflow with local sessions, projects, provider configuration, streaming chat, workspace-restricted file tools, and a persistent long-term memory system (project/user scopes with optional LLM auto-extract).

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
│       ├── workspace.py           # Workspace path guard and file preview helpers
│       └── memory/                # Long-term memory subsystem
│           ├── memory_manager.py  # Global manager + auto-extract dispatch
│           ├── memory_store.py    # Scope files (project/user) CRUD
│           ├── memory_scanner.py  # Memory file discovery + scan
│           ├── memory_middleware.py # Session read injection into the prompt
│           ├── auto_extract.py    # Phase 2: LLM extraction + proposals
│           └── selftest.py        # 18-check offline test suite
├── electron/
│   ├── main.js                    # Window, tray, IPC, backend process lifecycle
│   └── preload.js                 # Renderer-safe API bridge
├── frontend/
│   ├── src/
│   │   ├── App.tsx                # App state, streaming chat, sessions/projects
│   │   ├── components/            # Reusable UI, chat, sidebar, settings, providers, memory
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
- Slash commands: `/help`, `/new`, `/clear`, `/providers`, `/settings`, `/skills`, `/memory`, `/plan`, `/build`.
- Long-term memory: project/user scopes stored as markdown files, injected into every chat prompt; editable in a Memory panel (`/memory` or sidebar); optional Phase 2 LLM auto-extract proposes memory entries from recent turns, reviewable in the same panel.
- Chinese/English i18n.
- Theme settings with presets, user color customization, light/dark mode, and translucent glass effect.
- Electron tray behavior: close window keeps app running; quit exits frontend and backend.
- Startup diagnostics instead of silent white screen.
- Workspace tree, directory listing, and file preview APIs.
- True background streams: switching sessions keeps the original session's reply streaming to completion; per-session `/clear` never wipes other sessions' in-progress messages.
- Runtime observability retention/export: Agent trace and tool audit logs can be exported, cleared, and their retention (line caps) configured from Settings.
- Provider API keys stored in the macOS Keychain (0600-file fallback), keeping secrets out of plaintext JSON.

## Current Limitations

- Agent mode is currently single-agent only.
- Toolset is intentionally small: search/read files by default; exact replace, atomic structured text edits, full write, and allowlisted command execution only in Build + Full Access.
- Agent command execution pauses at a LangGraph human-in-the-loop interrupt before the process is started; approval resumes the same checkpoint. Bottom-panel terminal command execution still requires a settings-page one-time approval.
- Default provider remains simulated until a real provider is configured.
- Provider API keys are stored in the macOS Keychain (falling back to a 0600 file when Keychain is unavailable); MCP env/header secrets stay in the (0600-protected) local config.
- Agent trace and tool audit logs are rolling JSONL with user-configurable retention (Settings → Runtime Observability), exportable and clearable from the same panel.
- The file-staleness guard ("File changed since it was last read") persists per workspace across turns, sessions and restarts: writing over content the agent has not seen (because the file changed since its last read) is rejected until it re-reads the file.
- Packaging/distribution is not complete.
- Plan/Build and Default/Full Access gating is still Coworker-owned runtime policy.

## Verification

Useful local checks:

```bash
cd frontend && npx tsc --noEmit
cd frontend && npm run build
backend/venv/bin/python -m compileall backend/main.py backend/coworker
backend/venv/bin/python -m coworker.memory.selftest
node --check electron/preload.js && node --check electron/main.js
git diff --check
COWORKER_SKIP_DESKTOP=1 ./coworker_desktop.command
```

## Next Development Phase

1. Add a real multi-file patch/diff tool if exact structured text edits are not enough.
2. Add packaging, updater, and release evidence.
3. Add checkpoint retention/export controls if long-running sessions need operational management.
4. Optionally migrate to assistant-ui only if it replaces the current frontend chat runtime owner instead of wrapping the existing `App.tsx` message state.

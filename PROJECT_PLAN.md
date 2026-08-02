# Coworker Development Plan

## Product Direction

Coworker is a local-first desktop agent app. The current product is a Codex-style single-agent coding assistant with local provider configuration, project/session organization, streaming chat, and guarded workspace tools.

Multi-agent company mode is not part of the current phase. Complete the single-agent product first; keep the runtime boundary clean, but do not spend current implementation work on additional agent modes.

## Current Architecture

### Desktop Layer

- Electron owns the app window, tray, startup diagnostics, IPC bridge, and backend process lifecycle.
- Closing the main window keeps the app available from the tray.
- Quitting the app terminates the backend process started by the launcher.
- Production mode loads `frontend/dist`.

### Frontend Layer

- React + TypeScript + Vite.
- Local Radix/shadcn-style primitives are the reusable UI foundation.
- `App.tsx` owns the current chat/session/project state and streaming message updates.
- `chatService` is the single frontend boundary for Electron IPC and direct HTTP fallback.
- `WorkspaceSidebar` owns standalone sessions and project-grouped sessions.
- `ProvidersPanel` owns provider add/edit/delete/default/test interactions.
- `SettingsView` owns language, preferences, theme presets, custom colors, and translucent glass mode.
- assistant-ui was evaluated for the chat runtime. It is not installed in the current phase because adding it without replacing `App.tsx`, `MessageList`, and `ChatInput` would create a second chat runtime owner. If adopted later, assistant-ui must replace the current chat state/run lifecycle owner in one migration.

### Backend Layer

- FastAPI exposes runtime config, chat, streaming chat, provider, session, project, and workspace APIs.
- `AgentRuntimeRegistry` is the single agent runtime entry.
- `ProviderManager` is the single local provider config owner.
- `SessionStore` and `ProjectStore` persist local JSON state under the app data directory.
- `Workspace` owns path normalization, workspace confinement, file preview, and directory tree traversal.

### Agent Layer

- Non-streaming and streaming calls use the Coworker LangGraph runtime: planner -> executor -> verifier -> summarizer.
- The executor stage uses LangChain `create_agent`, which compiles to a LangGraph tool agent and owns ReAct-style tool execution.
- Tools are intentionally small:
  - Default access: `search_files`, `read_file`
  - Build + Full Access: `search_files`, `read_file`, `replace_in_file`, `apply_text_edits`, `write_file`, `run_command`
- Plan mode always removes write access.
- Attachment content is formatted into the user prompt server-side and capped.
- Real provider sessions use LangGraph SQLite checkpointing as the durable runtime message state owner. `SessionStore` remains the persisted UI transcript owner and only seeds runtime state when no checkpoint exists for a session.
- Agent invocations pass standard LangChain/LangGraph Runnable `run_name`, `tags`, `metadata`, and `thread_id` config for LangSmith/LangChain tracing.
- `AgentTraceStore` owns local runtime observability records for run start/done/error, stage completion, and HITL interrupt state; settings reads it through `/traces/agent`.
- File writes, exact replacements, atomic structured text edits, and command executions append JSONL audit events under the app data directory; `/audit/tool` exposes recent records for the settings UI.
- Agent command execution is interrupted by LangGraph/LangChain human-in-the-loop middleware before the process starts, then resumes from the same checkpoint after approve/reject. Bottom-panel terminal commands keep a one-time approval record because they are outside the Agent graph.

## Implemented Single-Agent Scope

- [x] Electron + React + TypeScript app shell.
- [x] FastAPI backend with `/health` and `/config`.
- [x] Streaming chat through `/chat/stream`.
- [x] LangChain/LangGraph single-agent runtime.
- [x] Simulated provider for local smoke testing.
- [x] OpenAI-compatible provider configuration.
- [x] Provider CRUD, default model, model fetching, and connection test.
- [x] Plan/Build toggle.
- [x] Default/Full Access toggle.
- [x] Model selection.
- [x] Send, stop, new chat, slash commands, and attachments in the composer.
- [x] Attachment-only sending.
- [x] Markdown rendering with code highlighting and copy actions.
- [x] Lazy-loaded Markdown/Shiki rendering boundary.
- [x] Local sessions and projects.
- [x] Sidebar with standalone sessions plus project sessions.
- [x] Workspace file APIs.
- [x] Atomic structured text edit tool.
- [x] LangGraph SQLite runtime checkpointing for real provider sessions.
- [x] LangChain/LangGraph trace config on Agent invocations.
- [x] Local Agent trace API and Runtime Observability settings view.
- [x] Tool audit API and settings-page review UI.
- [x] Command approval API and settings-page approve/deny UI.
- [x] Chinese/English i18n.
- [x] Theme presets, custom theme colors, light/dark switching, and translucent glass mode.
- [x] System tray and frontend/backend process binding.
- [x] Startup diagnostics instead of silent white screen.

## Active Gaps

These are the next implementation areas. Treat them as real product gaps, not documentation polish.

1. **Richer Coding Tools**
   - Add multi-file patch/diff support if exact structured text edits are not enough.
   - Keep all tool access behind the existing work/access policy owner.

2. **Runtime Persistence**
   - Add checkpoint retention/export controls if long-running sessions need operational management.
   - Keep the owner split clear: LangGraph owns runtime message state; `SessionStore` owns the UI transcript.
   - Do not feed full transcript history on every turn once a checkpoint exists for the session.

3. **Human Approval**
   - Agent `run_command` approval now uses LangGraph/LangChain human-in-the-loop middleware as the runtime policy owner.
   - Bottom-panel terminal command approval remains Coworker-owned because it is not executed inside the Agent graph.
   - Expand LangGraph approval to other risky Agent tools if product usage requires it.

4. **Trace And Debug**
   - Agent runs now expose standard LangChain/LangGraph trace metadata for LangSmith/LangChain tracing.
   - Local Agent trace is available in Runtime Observability and remains separate from the safety-focused tool audit.
   - Keep JSONL tool audit as the local safety log owner.
   - Add retention/export controls for local Agent trace if long-running usage requires operational management.

5. **Packaging And Release**
   - Add native packaging.
   - Add installer/update path.
   - Add release evidence commands and checklist.

6. **Security Hardening**
   - Harden provider secret storage.
   - Improve provider validation.
   - Add retention controls for workspace write/command audit records.

7. **Optional assistant-ui Migration**
   - Adopt only if assistant-ui becomes the single frontend chat runtime owner.
   - Replace `App.tsx` message/run lifecycle state, `MessageList`, and `ChatInput` together.
   - Do not add `AssistantRuntimeProvider` as a passive wrapper around the existing local chat state.

## Out Of Current Scope

- Multi-agent company mode.
- Additional runtime modes beyond the existing single-agent boundary.

## Verification Gates

Run these before claiming a development slice is complete:

```bash
cd frontend && npx tsc --noEmit
cd frontend && npm run build
backend/venv/bin/python -m compileall backend/main.py backend/coworker
node --check electron/preload.js && node --check electron/main.js
git diff --check
COWORKER_SKIP_DESKTOP=1 ./coworker_desktop.command
```

## Design Rules

- Keep one owner for each product truth: provider config, session history, project grouping, workspace access, runtime policy, and Electron process lifecycle.
- Prefer replacing the root cause over adding compatibility or fallback layers.
- Do not add a parallel UI state store for conversations unless `App.tsx` state ownership is intentionally migrated.
- Do not introduce login/account scope; Coworker is currently local software.
- Do not implement or claim multi-agent support during the current single-agent completion phase.

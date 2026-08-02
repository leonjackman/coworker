# Coworker Development Plan

## Product Direction

Coworker is a local-first desktop agent app. The current product is a Codex-style single-agent coding assistant with local provider configuration, project/session organization, streaming chat, and guarded workspace tools.

The long-term direction still includes a multi-agent company mode, but that is not part of the current implemented runtime. The active implementation should keep a clean runtime registry boundary so multi-agent orchestration can be added without forking the desktop, provider, session, or workspace layers.

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

### Backend Layer

- FastAPI exposes runtime config, chat, streaming chat, provider, session, project, and workspace APIs.
- `AgentRuntimeRegistry` is the single agent runtime entry.
- `ProviderManager` is the single local provider config owner.
- `SessionStore` and `ProjectStore` persist local JSON state under the app data directory.
- `Workspace` owns path normalization, workspace confinement, file preview, and directory tree traversal.

### Agent Layer

- Non-streaming calls use LangChain `create_agent`.
- Streaming calls use LangGraph `create_react_agent` and `astream`.
- Tools are intentionally small:
  - Default access: `search_files`, `read_file`
  - Build + Full Access: `search_files`, `read_file`, `replace_in_file`, `apply_text_edits`, `write_file`, `run_command`
- Plan mode always removes write access.
- Attachment content is formatted into the user prompt server-side and capped.
- File writes, exact replacements, atomic structured text edits, and command executions append JSONL audit events under the app data directory; `/audit/tool` exposes recent records for the settings UI.
- Command execution requires a one-time approval stored under the app data directory before the process starts.

## Implemented MVP

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
   - Evaluate LangGraph checkpointing for resumable long-running tasks.
   - Do not duplicate session truth: either checkpoint is integrated as runtime state, or local `SessionStore` remains the chat-history owner.

3. **Human Approval**
   - Command execution now has Coworker-owned one-time approval.
   - Evaluate LangGraph/LangChain human-in-the-loop middleware only if it becomes the runtime policy owner.
   - Expand approval to other risky tools if product usage requires it.

4. **Multi-Agent Foundation**
   - Add new runtime modes behind `AgentRuntimeRegistry`.
   - Keep provider/session/workspace APIs shared.
   - Avoid adding a second conversation store or provider store.

5. **Packaging And Release**
   - Add native packaging.
   - Add installer/update path.
   - Add release evidence commands and checklist.

6. **Security Hardening**
   - Harden provider secret storage.
   - Improve provider validation.
   - Add retention controls for workspace write/command audit records.

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
- Do not claim multi-agent support until a real runtime exists behind the registry.

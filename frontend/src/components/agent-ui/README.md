# Agent UI Integration

Coworker uses shadcn/ui generated components for general UI primitives and chat message structure. This keeps components local and themeable while avoiding fully hand-written menus, selects, and message primitives.

## Current Library Boundary

- `src/components/ui/*`: shadcn/Radix primitives (select, dropdown-menu, button, toggle-group, tooltip, etc.).
- `src/components/MarkdownContent.tsx`: `react-markdown` + `remark-gfm` + `shiki` renderer with code-block syntax highlighting and a copy button. Used by the chat stream.
- `src/components/MessageList.tsx`: chat-stream style layout (user bubble + assistant content rows).
- Existing Coworker theme variables remain the single source of visual truth.

## Streaming

- Backend exposes `POST /chat/stream` (SSE, `text/event-stream`), driven by the Coworker LangGraph planner -> executor -> verifier -> summarizer runtime. Current events: `start`, `stage`, `delta`, `approval_required`, `done`, `error`.
- `src/services/chatService.ts` implements `sendMessageStream` for both HTTP (fetch + reader) and Electron IPC (`streamChatMessage` → `start-chat-stream` handler in `electron/main.js`).
- `App.tsx` consumes SSE frames and updates the running assistant message incrementally.

## Sessions & Projects

- Sessions persist under the app data dir (`data_dir/sessions/*.json`) via `backend/coworker/sessions.py`. Each new session must have a `project_id`; older standalone sessions can still be opened and moved into a project for migration.
- Projects persist in `data_dir/projects.json` via `backend/coworker/projects.py`. A project owns both its display name and `workspace_path`; this is the source of truth for the agent's working directory.
- `backend/coworker/workspace_controller.py` resolves `project_id`/`session_id` to a `Workspace` and is the only runtime workspace resolver. Frontend flows pass project/session context, not raw cwd.
- Sidebar: top "新对话" opens the unified new-session flow; project rows open that project's draft workspace. Project dropdown supports new chat / rename / delete (delete cascades its sessions).
- Workspace file preview APIs (`GET /workspace/tree`, `/workspace/dir`, `/workspace/file`) and bottom-panel commands accept `project_id` so they run against the selected project workspace.

## assistant-ui

assistant-ui was evaluated as a possible runtime migration target. Its `LocalRuntime` owns messages, threads, branching, editing, regeneration, cancellation, and run lifecycle; `ExternalStoreRuntime` is for apps that intentionally keep their own store. Coworker currently has a product-specific local chat surface with session/project ownership, attachments, slash commands, model/provider controls, HITL approval projection, and custom theme primitives.

Decision for the current single-agent phase: do not install assistant-ui as a passive dependency and do not wrap the existing chat with an `AssistantRuntimeProvider`. That would create a second chat runtime owner while `App.tsx` still owns messages and streaming. If adopted later, assistant-ui must replace the current `App.tsx` message/run owner and the visible `MessageList`/`ChatInput` surface together, not layer over them.

Required migration shape if adopted:

- Replace `App.tsx` message and run lifecycle state with an assistant-ui runtime.
- Map Coworker `ChatMessage`/attachment/session records into assistant-ui thread messages through one adapter boundary.
- Move send/stop/regenerate/edit behavior into the assistant-ui runtime contract.
- Rebuild Plan/Build, Default/Full Access, provider/model, slash commands, and approval UI as assistant-ui composer/tool surfaces.
- Delete the old local chat runtime once assistant-ui owns the surface.

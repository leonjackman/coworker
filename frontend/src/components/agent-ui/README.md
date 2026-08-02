# Agent UI Integration

Coworker uses shadcn/ui generated components for general UI primitives and chat message structure. This keeps components local and themeable while avoiding fully hand-written menus, selects, and message primitives.

## Current Library Boundary

- `src/components/ui/*`: shadcn/Radix primitives (select, dropdown-menu, button, toggle-group, tooltip, etc.).
- `src/components/MarkdownContent.tsx`: `react-markdown` + `remark-gfm` + `shiki` renderer with code-block syntax highlighting and a copy button. Used by the chat stream.
- `src/components/MessageList.tsx`: chat-stream style layout (user bubble + assistant content rows).
- Existing Coworker theme variables remain the single source of visual truth.

## Streaming

- Backend exposes `POST /chat/stream` (SSE, `text/event-stream`), driven by `langgraph`'s `create_react_agent` + `astream`. Events: `start`, `delta`, `tool_call`, `tool_result`, `done`, `error`.
- `src/services/chatService.ts` implements `sendMessageStream` for both HTTP (fetch + reader) and Electron IPC (`streamChatMessage` → `start-chat-stream` handler in `electron/main.js`).
- `App.tsx` consumes SSE frames and updates the running assistant message incrementally.

## Sessions & Projects

- Sessions persist under the app data dir (`data_dir/sessions/*.json`) via `backend/coworker/sessions.py`. Each session has a `project_id` (empty = standalone session).
- Projects persist in `data_dir/projects.json` via `backend/coworker/projects.py`. A project groups its sessions.
- Sidebar: top "会话" section lists standalone sessions; the "项目" section lists projects, each expandable to show that project's sessions. Session dropdown supports open / move into or out of a project / delete. Project dropdown supports new chat / rename / delete (delete cascades its sessions).
- Workspace file preview APIs (`GET /workspace/tree`, `/workspace/dir`, `/workspace/file`) remain available on the backend but are not surfaced in the sidebar.

## assistant-ui

assistant-ui is a possible future migration for moving thread/message state out of `App.tsx` into an assistant runtime. Do not add it as a passive dependency; migrate intentionally if adopted.

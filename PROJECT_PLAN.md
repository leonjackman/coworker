# Agent Assistant Project Plan

## Vision
Build a local desktop application that integrates:
1. **Single Agent Mode**: Codex-like coding assistant for quick queries and code generation/modification
2. **Multi-Agent Mode**: Self-organizing "agent company" that automatically decomposes tasks into role-based collaboration (PM, Architect, Developer, Tester, etc.)

Both modes share the same backend services and can be switched seamlessly in the chat interface.

## Technology Stack Decisions

### Frontend
- **Framework**: Electron + React + TypeScript
- **UI Library**: Ant Design (for consistent, professional components)
- **Chat Interface**: Inspired by opencode's terminal-style interaction but adapted to modern GUI:
  - Chat history panel with syntax-highlighted code blocks
  - Bottom input area with mode toggle switch (⚡ Single Agent / 🏢 Multi-Agent Company)
  - Top status bar showing current working directory and agent state
  - Optional right-side collapsible panel for file tree or task progress
- **Communication**: Electron main process launches Python backend via HTTP API; renderer process communicates via IPC to main process which proxies to backend

### Backend
- **Framework**: Python + FastAPI
- **Core Orchestration**: LangGraph (for both single agent and future multi-agent workflows)
- **Tool Ecosystem**: LangChain tools (file operations, code search, command execution, sandbox)
- **LLM Adapters**: Unified interface supporting OpenAI, Anthropic, Ollama
- **Data Storage**: SQLite for session history, local filesystem for workspace caching
- **Security**: Sandboxed tool execution (Docker or restricted environment), file access limited to workspace, command whitelist

## MVP Scope (Single Agent Focus)
**Goal**: Deliver a minimally usable coding assistant that can:
- Answer programming questions
- Generate basic code snippets
- Perform simple file operations (read/write)
- Execute limited development commands in sandbox
- Maintain context of current workspace

### MVP Features
1. **Electron Desktop Application**
   - Main window with resizable chat interface
   - System tray integration (optional)
   
2. **Chat Interface**
   - Virtualized message list for performance
   - Support for Markdown rendering and code syntax highlighting (Prism.js or Highlight.js)
   - Input area: multi-line text box (Shift+Enter for newline, Enter to send)
   - Send button with loading state
   - Mode toggle switch (visible but disabled in MVP, reserved for future multi-agent)
   
3. **Python Backend (FastAPI)**
   - `/chat` endpoint: accepts user message, returns streaming agent response
   - `/tools` endpoint: executes registered tools (file read, write, etc.)
   - `/health` endpoint: service health check
   - Stateless design with session context passed in requests
   
4. **Single Agent Implementation**
   - Based on LangGraph simple state machine: think → act → observe → (repeat or finish)
   - Integrated LLM via LangChain adapters
   - Core tools:
     - File read (with path validation)
     - File write (with backup option)
     - Basic command execution (whitelisted: ls, grep, git status, etc.)
     - Code sandbox execution (Python in restricted mode)
   
5. **Context Management**
   - Track current workspace directory
   - Optional: track currently opened file for focused assistance
   
6. **Modular Design Principles**
   - Clear separation between:
     - UI layer (Electron/React)
     - Communication layer (IPC ↔ HTTP)
     - Agent layer (LangGraph-based)
     - Tool layer (pluggable interface)
     - LLM adapter layer
   - Interfaces defined via TypeScript (frontend) and Python protocols/backend interfaces
   - Dependency injection for easy replacement (e.g., swap LangGraph for AutoGen later)

## Future Extension Points (for Multi-Agent)
Designed in MVP:
- **Mode Switch Backend Endpoint**: `/mode` to toggle between agent types
- **Workflow Engine Interface**: Abstract base class for agent orchestration (single vs multi)
- **Role Plugin System**: Define interfaces for different agent roles (PM, Architect, etc.)
- **Communication Bus**: Abstract message passing between agents
- **Supervisor/Controller Interface**: For task progression and quality control
- **Workflow Definition Language**: YAML/JSON schema ready for future implementation

## Development Roadmap

### Phase 1: MVP - Single Agent (2-3 weeks)
- [x] Setup Electron + React + TypeScript project
- [x] Implement basic FastAPI service
- [x] Establish main process ↔ backend communication
- [x] Build chat UI with message display and input
- [x] Add one-click desktop launcher
- [x] Add backend-owned simulated provider for smoke testing
- [x] Add file read/write tools for the real single-agent provider
- [ ] Implement LLM streaming response
- [ ] Enable robust code generation and Q&A with persistent context

### Phase 2: Enhanced Single Agent (2-3 weeks)
- [ ] Add advanced tools (precise edit, code search, sandbox execution)
- [ ] Optimize prompts for coding tasks
- [ ] Implement basic context tracking (current file/workspace)
- [ ] Add error handling and retry mechanisms

### Phase 3: Multi-Agent Foundation (3-4 weeks)
- [ ] Define role interfaces and basic implementations
- [ ] Implement simple task decomposition
- [ ] Build agent communication bus
- [ ] Create basic workflow executor
- [ ] Test with simple collaborative tasks

### Phase 4: Advanced Multi-Agent Company (3-4 weeks)
- [ ] Implement full role set (PM, Architect, Developer, Tester, DevOps)
- [ ] Add workflow definition language support
- [ ] Build supervisor with quality gates
- [ ] Integrate human-in-the-loop checkpoints
- [ ] Add automated testing and validation

### Phase 5: Polish and Release (2 weeks)
- [ ] Performance optimization (caching, lazy loading)
- [ ] Comprehensive logging and error reporting
- [ ] User documentation and tutorial
- [ ] Security audit and hardening
- [ ] Packaging and distribution

## Immediate Next Steps (Post Read-Only Mode)
1. Create technology validation prototype:
   - Minimal Electron window
   - Simple FastAPI health endpoint
   - Main process launching backend and proxying IPC→HTTP
2. Verify LangGraph basic agent loop works in prototype
3. Finalize UI layout and component choices

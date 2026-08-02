# Coworker Agent

A local desktop application that integrates AI agents for coding assistance.

## Vision
Build a local desktop application that integrates:
1. **Single Agent Mode**: Codex-like coding assistant for quick queries and code generation/modification
2. **Multi-Agent Mode**: Self-organizing "agent company" that automatically decomposes tasks into role-based collaboration (PM, Architect, Developer, Tester, etc.)

Both modes share the same backend services and can be switched seamlessly in the chat interface.

## Current Status
This is a minimum viable product (MVP) focused on single agent mode. The desktop launcher builds the frontend, starts the FastAPI backend, and opens the Electron window. Multi-agent company mode is intentionally left as a future runtime mode behind the same backend boundary.

## Technology Stack
- **Frontend**: Electron + React + TypeScript
- **Backend**: Python + FastAPI
- **Agent Framework**: LangChain/LangGraph (planned for integration)
- **Communication**: Electron main process ↔ Python backend (HTTP API) ↔ Renderer process (IPC)

## Project Structure
```
coworker/
├── backend/          # Python FastAPI service
│   ├── main.py       # Backend API
│   ├── requirements.txt
│   └── workspace/    # Directory for file operations
├── electron/         # Electron main process
│   ├── main.js       # Electron main process
│   └── preload.js    # Preload script for renderer process
├── frontend/         # React + TypeScript application
│   ├── src/
│   │   ├── App.tsx   # Main application component
│   │   ├── main.tsx  # Entry point
│   │   └── services/ # Services (e.g., chatService)
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts
├── PROJECT_PLAN.md   # Detailed project plan
└── README.md         # This file
```

## Setup Instructions

### Prerequisites
- Node.js (v18+ recommended)
- Python (v3.11+ recommended)
- Git

### One-Click Desktop Launcher
From the project root:
```bash
./coworker_desktop.command
```

The launcher prepares Python and Node dependencies, builds the Vite frontend, starts the backend on `127.0.0.1:8000`, opens Electron, and stops the backend when the desktop app exits.

### Backend Setup
1. Navigate to the backend directory:
   ```bash
   cd backend
   ```
2. Create a virtual environment:
   ```bash
   python3 -m venv venv
   ```
3. Activate the virtual environment:
   ```bash
   # On macOS/Linux:
   source venv/bin/activate
   # On Windows:
   venv\Scripts\activate
   ```
4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
5. Set up environment variables (optional for MVP):
   ```bash
   # For full functionality with OpenAI models, set:
   export OPENAI_API_KEY="your_openai_api_key_here"
   ```
   Note: without an OpenAI key, the backend runs with the explicit `simulated` single-agent provider.

6. Start the backend server:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000
   ```
   The server will be available at http://localhost:8000

### Frontend Setup
1. Navigate to the frontend directory:
   ```bash
   cd frontend
   ```
2. Install dependencies:
   ```bash
   npm install
   ```
3. Start the development server:
   ```bash
   npm run dev
   ```
   The frontend will be available at http://localhost:3000 (or another port if 3000 is in use)

### Electron Setup
1. From the project root directory, start the Electron application:
   ```bash
   # In development mode (loads frontend from dev server)
   NODE_ENV=development npx electron . --no-sandbox
   ```
   Note: The `--no-sandbox` flag is used for simplicity in development. For production, you should remove it and properly configure the sandbox.

## How It Works
1. `coworker_desktop.command` builds the frontend and starts the Python backend.
2. Electron loads the production Vite output from `frontend/dist`.
3. The React app requests runtime config from the backend through Electron IPC.
4. Chat messages go Renderer → Electron IPC → FastAPI `/chat` → single-agent runtime.
5. The backend owns the active provider:
   - `COWORKER_AGENT_PROVIDER=simulated` for local MVP smoke tests
   - `COWORKER_AGENT_PROVIDER=openai` with `OPENAI_API_KEY` for the real OpenAI-backed single agent

## Features in MVP
- Basic chat interface with message history
- Input area with Shift+Enter for newline, Enter to send
- Status bar showing workspace and agent mode
- Backend-owned simulated provider for local smoke testing
- OpenAI-backed single-agent runtime when configured
- File read/write tools available to the real agent
- Workspace-based file access restriction for security
- Modular frontend components and swappable chat service boundary
- Modular backend runtime registry prepared for future multi-agent mode

## Next Steps (Post-MVP)
1. Integrate LangGraph for proper agent orchestration
2. Add more sophisticated tools (code search, code execution sandbox, etc.)
3. Enhance chat UI to render markdown and code blocks properly
4. Implement mode toggle for single agent vs multi-agent company
5. Develop multi-agent company with role-based agents (PM, Architect, Developer, Tester)
6. Add workflow execution and task decomposition capabilities
7. Implement persistence for chat sessions and workspace state
8. Add performance optimizations (caching, lazy loading)
9. Comprehensive logging and error reporting
10. Security audit and hardening
11. Packaging and distribution

## Troubleshooting
- If the Electron app fails to load the frontend, check that the Vite dev server is running on the expected port.
- If backend communication fails, verify that the backend is running on port 8000 and accessible.
- For file operation errors, ensure the workspace directory exists and is writable.

## License
MIT

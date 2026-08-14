# Contributing to Coworker

Thank you for your interest in contributing to Coworker! This project is MIT-licensed and welcomes contributions from everyone.

## Quick Start

1. Fork the repository
2. Clone your fork: `git clone https://github.com/<your-username>/coworker.git`
3. Run the dev environment: `./coworker_desktop.command`

## Development Setup

See the [README](README.md) for full development instructions.

### Prerequisites

- Node.js 20+ (for frontend)
- Python 3.11+ (for backend)
- Electron (installed via npm)

### Key Directories

| Directory | Purpose |
|-----------|---------|
| `frontend/` | React UI, chat, settings, and all renderer code |
| `backend/` | FastAPI server, agent runtime, memory system |
| `electron/` | Mac / Electron main process, preload, tray, updater |
| `assets/` | App icons, logo assets |
| `docs/` | Design docs, task tracking |
| `.github/` | CI/CD workflows |

## Workflow

1. **Discuss first** — Open an issue for new features or if you're unsure about the approach. Small fixes (typos, bugfixes) can go directly to PR.
2. **Branch and create** — Use a descriptive branch name: `fix/tray-icon`, `feat/skill-market`, `docs/readme-update`.
3. **Test** — Make sure everything still works:
   ```bash
   cd frontend && npx tsc --noEmit
   cd frontend && npm run build
   backend/venv/bin/python -m compileall backend/main.py backend/coworker
   backend/venv/bin/python -m coworker.memory.selftest
   ```
4. **Commit** — Use conventional commit messages (e.g., `feat: add skill market tab`, `fix: resolve session leak`).
5. **Push and open PR** — Provide a brief description of what changed and why.

## Code Style

- JavaScript/TypeScript: Prettier defaults (2-space indent, trailing commas)
- Python: Ruff formatting / linting if available
- Keep changes focused — one PR, one purpose

## Documentation

- Update README if you change install steps, features, or workflows
- Add comments in non-obvious logic
- Chinese/English bilingual READMEs — if you update English README, also update `README.zh-CN.md`

## Security

- Never commit API keys, tokens, or secrets
- Use environment variables or the app's Keychain integration for sensitive data
- Report security issues privately

## What to Work On

- [Open issues](https://github.com/leonjackman/coworker/issues)
- [Development task list](docs/tasklist/DEV-TASKS.md)

## Need Help?

Open an issue or leave a message in an existing discussion. We'll help however we can.

Thank you for making Coworker better!

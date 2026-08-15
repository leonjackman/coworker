# 为 Coworker 做贡献

感谢你对 Coworker 做出贡献的兴趣！本项目采用 MIT 许可，欢迎所有人的参与。

## 快速上手

1. Fork（复刻）这个仓库
2. Clone 你的 fork：`git clone https://github.com/<your-username>/coworker.git`
3. 运行开发环境：`./coworker_desktop.command`

## 开发环境配置

详细说明见 [README](README.md)。

### 前置条件

- Node.js 20+（前端）
- Python 3.11+（后端）
- Electron（通过 npm 安装）

### 主要目录

| 目录 | 用途 |
|-----------|---------|
| `frontend/` | React UI、聊天、设置以及所有渲染器代码 |
| `backend/` | FastAPI 服务器、Agent 运行时、记忆系统 |
| `electron/` | Electron 主进程、preload、系统托盘、更新器 |
| `assets/` | 应用图标、Logo 资源 |
| `docs/` | 设计文档、任务追踪 |
| `.github/` | CI/CD 工作流 |

## 工作流

1. **先讨论** — 新功能或不确定的实现方案请先开 Issue。小修小补（错别字、bug 修复）可以直接提交 PR。
2. **创建分支** — 使用有描述性的分支名，例如 `fix/tray-icon`、`feat/skill-market`、`docs/readme-update`。
3. **测试** — 确保一切正常工作：
   ```bash
   cd frontend && npx tsc --noEmit
   cd frontend && npm run build
   backend/venv/bin/python -m compileall backend/main.py backend/coworker
   backend/venv/bin/python -m coworker.memory.selftest
   ```
4. **提交** — 使用规范的提交信息（例如 `feat: 新增技能市场标签页`，`fix: 修复会话内存泄漏`）。
5. **推送并开 PR** — 提供简要描述，说明你改了什么以及为什么。

## 代码规范

- JavaScript/TypeScript：Prettier 默认格式（2 空格缩进、尾逗号）
- Python：如可用则使用 Ruff 格式化和检查
- PR 尽量聚焦 — 一个 PR 只做一件事

## 文档

- 如果改了安装步骤、功能或工作流，请更新 README
- 在不明显的逻辑处添加注释
- README 为中英双语 — 更新英文 README 时也要同步更新 `README.zh-CN.md`

## 安全

- 不要把 API key、token 或密钥写入代码并提交
- 敏感数据使用环境变量或应用的 Keychain 存储
- 安全问题请私下报告

## 需要帮助？

开一个 Issue 或在现有讨论中留言。我们会尽力帮助。

感谢让 Coworker 变得更好！

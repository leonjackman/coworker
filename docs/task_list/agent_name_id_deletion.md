# Agent 名/id 分离 + 改名实时刷新 + 级联删除 + default_agent 保护 — 开发任务清单

目标：在已完成的侧栏会话三级化 / 多 Agent 组织（`.org.json` 单一真相源）之上，落地四个正交能力：

1. **名/id 概念分离**：磁盘路径与 `rel` 一律用 **agent id**；前端记忆树显示 **agent 显示名（name）**。`discover` 对每个 agent 同时输出 `id` 与 `name`（`name` 取自 `.org.json`，缺省 fallback 到 id）。改名只改显示名，id / 记忆目录 / 会话绑定全部不变。
2. **改名实时刷新**：团队面板（OrgSettingsPanel）每次 org 变更成功后触发 `onChanged` → App `refreshProjects()`，让侧栏 AgentGroup 标题、composer agent 下拉、全量会话页 agent 标题同步新名，无需手动刷新。
3. **删除项目级联清记忆**：`DELETE /projects` 在删除项目记录/会话/checkpoint/change 之外，把整个项目 `memory_dir` 移入 OS 回收站（可找回）。
4. **删除 agent 级联 + 组织保护（硬阻断）**：`DELETE /api/org/agent` 与删项目同构——删除 org 条目 + 绑定该 agent 的全部会话（含 checkpoint / change_store）+ 记忆目录进回收站；`default_agent` 不可删除；是他人上级 / team lead 时硬阻断，要求先手动改派（不自动改派）。

关键设计决策（已确认）：
- 不做向后兼容：`AgentView.name` 语义直接改为「显示名」，不再保留「name=id」的旧含义；未解析到时 **fallback 到 id**。
- 删除 agent 会话：**连同会话一起删除**（与删项目同构）。
- 组织上下交接断裂：**保持硬阻断**（`remove_agent` 已有 parent/lead 校验），不自动改派。
- 回收站：复用 `coworker.memory.trash.send_to_trash`（macOS OS 回收站，回退隐藏 `.trash/`），与删记忆/删 agent 目录一致。

---

## 阶段 A — agent 名/id 分离（记忆树显示 name，路径/rel 用 id）

方向：`AgentView` 拆出 `id`（= 目录名）与 `name`（= 显示名，可 fallback id）；`injected()` 与迁移回填改按 `id` 匹配，杜绝显示名污染路径/匹配逻辑。

- [x] **`backend/coworker/memory/memory_discovery.py`**：
  - [x] `AgentView`（:103）加 `id: str` 字段；`name` 字段语义改为「显示名」（无旧 name=id 兼容）
  - [x] `to_dict()`（:112）输出 `id` 与 `name` 两个字段
  - [x] `_scan_agent`（:446）：`id=agent_dir.name`；`name = agent_name_resolver(agent_dir.name) if resolver else agent_dir.name`
  - [x] `MemoryScanner.__init__`（:228）加 `agent_name_resolver: Callable[[str], str] | None`（复用 `project_name_resolver` 可变属性模式，支持动态绑定）
  - [x] **`injected()`（:208）改 `a.id == agent`**（`agent` 参数是 id；用显示名匹配会导致注入失效）
  - [x] 搜索标签（:309）用 `aview.name`（显示名）——保留，不改
- [x] **`backend/main.py`**：
  - [x] scanner 装配 `agent_name_resolver`（:103 `project_name_resolver` 旁）：`lambda agent_id: <从 org_store 加载该项目 org，members_for 建 id→name 映射，缺失回退 agent_id>`
  - [x] **迁移回填（:591-595）改用 `aview.id` 作 id、`aview.name` 作显示名**（`name` 现在是显示名，不能当 id 用）
- [x] **`backend/coworker/memory/selftest.py:349-350`**：迁移回填模拟改用 `aview.id`
- [x] **`frontend/src/types.ts`**：`MemoryAgentView` 加 `id: string`
- [x] **`frontend/src/components/MemoryPanel.tsx`**：
  - [x] `:1116` agentKey 改用 `agent.id`（改名不崩折叠态）
  - [x] `:1128` label 用 `agent.name`（显示名）
  - [x] `:369` move 目标 label 用 `agent.name`、value 用 `agent.rel`（含 id）——已正确，确认即可

## 阶段 B — 改名实时刷新（onChanged → refreshProjects）

方向：团队面板所有成功变更都通知 App 刷新 `/projects`，roster 随之更新，各处 agent 名实时同步。

- [x] **`frontend/src/components/settings/OrgSettingsPanel.tsx`**：
  - [x] props 加 `onChanged?: () => void`
  - [x] 加 `notifyChanged()` 辅助（`onChanged?.()` 空安全调用）
  - [x] 在成功路径末尾调用：`createAgent` / `updateAgent` / `submitRename` / `deleteAgent` / `createTeam` / `deleteTeam` / `saveConfig`
- [x] **`frontend/src/components/settings/OrgSettingsPage.tsx`**：props 加 `onChanged?: () => void`，透传给 `OrgSettingsPanel`
- [x] **`frontend/src/App.tsx`**：渲染 `OrgSettingsPage` 时传 `onChanged={() => void refreshProjects()}`

## 阶段 C — 删除项目级联清记忆（回收站）

方向：删项目在既有清理链（project 记录、session、checkpoint、change_store）基础上，补上整个项目 memory_dir 的回收。

- [x] **`backend/main.py` `delete_project`（:2650-2658）**：
  - [x] 顺序重构：先取 `memory_dir`（`project_store.require(project_id).memory_dir`，空则 `memory_dir_for`）→ 删 project 记录 → 清 session/checkpoint/change → 移 memory_dir 进回收站
  - [x] 回收实现：`from coworker.memory.trash import send_to_trash, system_trash_dir`；目标 `memory_manager.root / memory_dir`；`dest_dir = system_trash_dir() or (memory_manager.root / ".trash")`
  - [x] 回收失败仅 `logger.warning`，不阻断 `{"status":"ok"}`（与 org_delete_agent 一致）

## 阶段 D — 删除 agent 级联 + 硬阻断

方向：删除 agent 与删项目同构，且 `default_agent` 与组织上下级受保护。

- [x] **`backend/coworker/sessions.py`**：
  - [x] 新增 `delete_by_agent(project_id: str, agent_id: str) -> int`：`list_sessions(project_id)` 中 `session["agent_id"] == agent_id` 逐个 `delete()`
- [x] **`backend/main.py` `org_delete_agent`（:1198）重构**：
  - [x] 开头保护：`if request.id == DEFAULT_AGENT: raise HTTPException(400, "default_agent cannot be deleted")`
  - [x] `org_store.remove_agent` 保留（已有硬阻断：不存在 / 是他人上级 parent / 是 team lead）
  - [x] 会话级联：
    ```python
    for session in session_store.list_sessions(request.project_id):
        if session.get("agent_id") != request.id:
            continue
        await asyncio.to_thread(agent_registry.forget_runtime_checkpoint, session["id"])
        agent_registry.change_store.delete_session(session["id"])
    session_store.delete_by_agent(request.project_id, request.id)
    ```
  - [x] 记忆目录进回收站逻辑保留（已有 `store.remove_file(f"{project_dir}/{request.id}")`）
  - [x] 顺序：`remove_agent`（硬阻断）→ 级联会话 → 回收记忆目录（各自 try/except，回收失败不阻断）
- [x] **`frontend/src/components/settings/OrgSettingsPanel.tsx`**：
  - [x] `deleteAgent`（:111）确认文案升级为「删除该成员将连同其全部会话与记忆目录一并删除（记忆移入回收站）」
  - [x] 成员行删除按钮：`agent.id === 'default_agent'` 时 `disabled` + title 提示

## 阶段 E — 文案与测试

方向：补齐多语言文案；后端 selftest/stress 与前端类型/构建/HTTP 冒烟全绿。

- [x] **locales（zh/zh-TW/zh-HK/en/ja/ko/fr）**：
  - [x] 更新 `settings.org_delete_agent_confirm`（含会话+记忆回收站说明）
  - [x] 新增 `settings.org_default_agent_protected`（default_agent 不可删除提示）
- [x] **`backend/coworker/memory/selftest.py`**：
  - [x] discover agent 输出 `id`+`name`（改名后 name=新名、id 不变、fallback 正确）
  - [x] `injected()` 按 id 匹配（改名后注入仍命中）
  - [x] `delete_by_agent` 只删对 agent 的会话
- [x] **`backend/coworker/memory/stress_test.py`**：HTTP 冒烟增补
  - [x] 建项目 → 建 coder + 用 coder 发会话 → `DELETE /api/org/agent{id:coder}` → org 无 coder、coder 会话全删、`.../{project}/coder` 进回收站
  - [x] `DELETE /api/org/agent{id:default_agent}` → 400
  - [x] 删项目 → 整 memory_dir 进回收站
- [x] **全量验证**：
  - [x] `cd backend && ./venv/bin/python -c "import main"` + `py_compile` 相关文件
  - [x] `./venv/bin/python coworker/memory/selftest.py`（全绿）
  - [x] `./venv/bin/python coworker/memory/stress_test.py`（全绿）
  - [x] `export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH" && cd frontend && npx tsc --noEmit`
  - [x] `npm run build`
  - [x] `node --check ../electron/main.js` / `../electron/preload.js`（如涉 IPC，本计划不涉及）

## 关键文件清单

| 文件 | 改动 |
|---|---|
| `backend/coworker/memory/memory_discovery.py` | AgentView.id、agent_name_resolver、injected() 按 id、搜索标签用 name |
| `backend/main.py` | discover 装 resolver、迁移回填用 aview.id、delete_project 回收 memory_dir、org_delete_agent 级联 + default_agent 保护 |
| `backend/coworker/sessions.py` | `delete_by_agent` |
| `backend/coworker/memory/selftest.py` | 新增/调整用例 |
| `backend/coworker/memory/stress_test.py` | 级联删除 HTTP 冒烟 |
| `frontend/src/types.ts` | MemoryAgentView.id |
| `frontend/src/components/MemoryPanel.tsx` | key 用 id、label 用 name |
| `frontend/src/components/settings/OrgSettingsPanel.tsx` | onChanged、删除确认文案、default_agent 禁用 |
| `frontend/src/components/settings/OrgSettingsPage.tsx` | onChanged 透传 |
| `frontend/src/App.tsx` | onChanged → refreshProjects |
| `frontend/src/locales/*.json`（7） | 确认文案 + 保护提示 |

## 明确不做（本轮边界）
- 不自动改派上级/lead（保持硬阻断，需用户手动）
- 不做 agent 删除的 App 内撤销/还原（回收站可手动找回，App 不提供 undo）
- 不做 `AgentView.name` 旧语义向后兼容（明确放弃，fallback id 即可）
- 不改 electron IPC（本计划纯 HTTP + 前端状态，无新 IPC）

## 完成记录

全部阶段已实施并验证通过。

- 阶段 A（名/id 分离）：`AgentView` 拆出 `id`（=目录名）与 `name`（显示名，可 fallback id）；`MemoryScanner` 加 `agent_name_resolver(project_dir, agent_id)`；`injected()` 与迁移回填改按 `id` 匹配；`MemoryAgentView.id` + MemoryPanel key 用 id / label 用 name。
- 阶段 B（改名实时刷新）：`OrgSettingsPanel` 加 `onChanged`（createAgent/updateAgent/submitRename/deleteAgent/createTeam/deleteTeam/saveConfig 成功路径触发）→ `OrgSettingsPage` 透传 → App `refreshProjects()`。
- 阶段 C（删项目清记忆）：`delete_project` 把整项目 memory_dir 移入 OS 回收站（`send_to_trash`，失败仅 warning）。
- 阶段 D（删 agent 级联 + 保护）：`org_delete_agent` 硬阻断 default_agent（400）+ 上级/lead 阻断保留；级联删除绑定会话（checkpoint/change_store/`delete_by_agent`）+ 记忆目录进回收站；前端 default_agent 删除按钮禁用 + 提示。
- 阶段 E（文案/测试）：7 语言更新 `org_delete_agent_confirm`、新增 `org_default_agent_protected`；selftest 新增 discover id/name、injected 按 id、rename fallback、`delete_by_agent` 用例；stress 增 HTTP 级联冒烟。

验证结果：selftest 101→111 全绿；stress 99 全绿；`import main`/py_compile OK；tsc --noEmit + vite build（node 22）+ electron node --check 通过。
HTTP 冒烟（9533）：discover agent 显示 `id`+`name`（改名后 name=老板、id=default_agent）；删 coder → 会话级联 + 记忆目录移除；删 default_agent → 400；删项目 → 整 memory_dir 进 OS 回收站。

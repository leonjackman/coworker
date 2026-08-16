# 多 Agent 团队组织 — 开发任务清单

目标：将「每项目 = 一个大 agent（`default_agent` 硬编码）」演进为「**项目 = 组织容器**」的多 agent 团队形态，支持部门分组、层级委派、并行执行与各自独立记忆。与远期「组织/项目/公司」形态保持一致（单一形态：项目即组织）。

形态定义（最终态）：
- 创建项目 → 立即实体化 1 个 `default_agent`（supervisor，首个成员），不再懒创建。
- 用户可在项目设置 UI，或让 agent 通过工具 `create_team_member`/`create_team`，扩成多 agent 团队 + 部门（teams）。
- 每个 agent 有独立身份（SOUL/AGENT/MEMORY）与记忆目录；项目级（组织级）目标/上下文注入所有成员；组级记忆注入本组成员；成员间**不互读**记忆（只读名册：name+role+team）。
- 层级委派：任意 agent 可 `delegate_task`（串行）或 `delegate_parallel`（并行，本轮实现）给活跃成员；深度受 `max_depth` 限制；只有 supervisor（顶层成员）向用户回复（单响应原则）。
- 项目设置可锁定 `mode: single`（单 agent），关闭 `allow_agent_creation`。

---

## 记忆布局（最终形态）

```
{DATA_DIR}/memory/
├── MEMORY.md / USER.md / AGENT.md            # 系统级（用户维护）
├── <memory_dir>/                             # = 项目 = 组织容器（秒级时间戳）
│   ├── .org.json                             # 组织注册表（dotfile，规避扫描/导出/注入）
│   ├── BASE/ + BASE/PROJECT/                 # 组织级目标/上下文（注入所有成员）
│   ├── <agent>/                              # 成员：BASE/{SOUL,AGENT,MEMORY}.md + SESSIONS/
│   └── teams/<team_id>/                      # 部门容器
│       ├── GOALS.md / CONTEXT.md             # 组级记忆（注入本组成员）
│       └── MEMORY.md                         # 组级共享记忆
```

`.org.json` 结构（单一真相源，`Project` dataclass 不再单独加字段）：
```json
{
  "version": 1,
  "mode": "multi",
  "max_depth": 3,
  "max_concurrent": 3,
  "allow_agent_creation": true,
  "agents": [
    {"id": "default_agent", "name": "default_agent", "role": "team lead",
     "description": "", "parent": "", "team_id": "", "status": "active", "created_at": "..."}
  ],
  "teams": [
    {"id": "backend", "name": "后端组", "lead": "coder", "parent_team_id": "", "status": "active"}
  ]
}
```
校验规则（`OrgStore` 落盘前强制）：agent id 全局唯一、`team_id` 必须存在、`lead` 必须是本组 agent、parent/parent_team 无环、status ∈ {active, disabled}、depth(agent) ≤ `max_depth`。

---

## 阶段 0 — 实体层：OrgStore 与 .org.json

方向：新增独立的组织注册表模块，作为所有成员/部门操作的唯一入口；接入项目创建与记忆发现。

- [x] **新增 `backend/coworker/org.py`**：
  - [x] `Agent` / `Team` dataclass（字段见上 `.org.json` 结构）
  - [x] `OrgStore(data_dir)`：构造 `{memory_dir}/.org.json` 路径（`<project_memory_dir>`，非 data_dir 根）
    - [x] `load(memory_dir) -> Org`（文件缺失/损坏 → 回退默认结构并重建；`Org` 含 `agents`/`teams` + 配置）
    - [x] `save(memory_dir, org)`：原子写（先写临时文件再 rename）+ 锁（复用 `memory_store` 的锁/原子写模式）
    - [x] `default_org()`：mode=multi、max_depth=3、max_concurrent=3、allow_agent_creation=true、空 agents/teams
    - [x] `upsert_agent(memory_dir, agent)` / `remove_agent(memory_dir, agent_id)` / `upsert_team` / `remove_team`（带校验）
    - [x] 校验：`_validate(org)`（唯一性、引用存在、无环、深度上限），违反抛 `ValueError`
  - [x] 辅助方法：`agents_depth(org, agent_id) -> int`（沿 parent 链）、`team_ancestors(org, team_id) -> list[str]`（含自身）、`roster(org) -> list[{name,role,team}]`（供名册注入）
- [x] **`backend/main.py` 接入创建**：
  - [x] `POST /projects`（~line 2256）：`ensure_project` 后追加 `ensure_agent(project_dir, DEFAULT_AGENT)` + `org_store.save(project_dir, default_org() + default_agent)`，全部包在现有 `try/except`（骨架失败不阻塞建项目）
- [x] **`POST /api/memory/register-agent` 升级**（main.py:965-972）：调用方保留，行为改为「写 `.org.json`（`upsert_agent`）+ 骨架」
- [x] **存量项目迁移**：`GET /api/memory/discover` 里若 `project_dir` 存在但 `.org.json` 缺失 → 自动回填：扫描已存在的 agent 目录（`scanner` 的 agents）逐个 `upsert_agent`（parent 空、team 空、status=active），`mode=multi`
- [x] **新增组织端点**（`backend/main.py`，挂在 `/api/org/*`）：
  - [x] `GET /api/org?project_id=` → 返回 `{agents, teams, config, roster}`
  - [x] `POST /api/org/agent {project_id, name, role, description, parent, team_id}` → 创建 agent（建 `.org.json` + `ensure_agent` 骨架 + `SESSIONS/`）；校验 parent 存在、depth 不超
  - [x] `PATCH /api/org/agent {project_id, id, role?, description?, parent?, team_id?, status?}`（编辑，校验同上）
  - [x] `DELETE /api/org/agent {project_id, id}`（校验：不能删自己/被引用为 lead/有子 agent；删除目录放回收站，复用 `trash.py`）
  - [x] `POST /api/org/team {project_id, id, name, lead, parent_team_id}`；`PATCH /api/org/team`；`DELETE /api/org/team`（仅空组可删）
  - [x] `PATCH /api/org/config {project_id, mode?, max_depth?, max_concurrent?, allow_agent_creation?}`（校验 mode ∈ {single, multi}）

## 阶段 1 — agent 绑定：谁是当前 agent

方向：把运行时的 `DEFAULT_AGENT` 硬编码改成「会话/请求级 agent」绑定，记忆注入与记忆工具随之作用到该 agent。

- [x] `backend/coworker/sessions.py`：`Session` 加 `agent_id: str = ""` 字段；`from_dict`/`to_dict` 同步（向后兼容，缺省 ""）；`SessionMessage` 加 `agent_id: str = ""`
- [x] `backend/main.py` `ChatRequest`（line 361）加 `agent: Optional[str] = None`（None → 默认 `default_agent`）；`/chat`（~647）与 `/chat/stream`（~1053）把解析出的 agent 传入 runtime
- [x] `backend/coworker/agents.py` 替换 4 处 `DEFAULT_AGENT_NAME` 硬编码：
  - [x] `OpenAICompatibleSingleAgentRuntime.__init__` / `OpenAICompatibleStreamRuntime.__init__` 加 `agent: str = DEFAULT_AGENT_NAME` 参数
  - [x] `_memory` property（sync ~2257 / stream ~2373）：`for_project(project_dir, self.agent)` + `agent_rel = f"{project_dir}/{self.agent}/BASE/MEMORY.md"`
  - [x] `AgentRuntimeRegistry.get_runtime`/`get_stream_runtime`（agents.py:3181/3189）透传 `agent`
  - [x] 保持 `DEFAULT_AGENT_NAME` 为缺省值（存量不破坏）
- [x] **记忆注入适配**：`memory_manager.for_project`（memory_manager.py:120）默认 `bound_agent=DEFAULT_AGENT` 保留；runtime 显式传 agent 后覆盖
- [x] **前端**：`frontend/src/types.ts` `ChatRequest` 加 `agent?: string`；`chatService` 透传；`App.tsx` 会话建立时记录/显示当前 agent（聊天头部），消息气泡带 agent 归属（`SessionMessage.agent_id`）

## 阶段 2 — 团队指挥：串行 + 并行 delegate

方向：给每个 agent 注入委派工具，工具内执行「目标 agent 的有界单 turn 子图」，支持串行（`delegate_task`）与并行（`delegate_parallel`，asyncio.gather）。子 agent 用自身记忆 view + 缩小工具集 + 层级系统提示；结果回传调用方，不直接回用户。

- [x] **`backend/coworker/agents.py` 新增工具定义**（`build_workspace_tools` ~437 内）：
  - [x] `delegate_task(agent, task, context="")`：串行。校验活跃 + 深度不超 → 执行有界子 turn → 返回结果文本
  - [x] `delegate_parallel(tasks: list[{agent, task, context}], max_concurrent=3)`：校验全部目标 + 不重复 + 深度不超 → `asyncio.gather`（并发上限 `min(requested, org.max_concurrent)`）→ 返回 per-agent 结果摘要
  - [x] 只有 `org_store` 提供且 `mode=multi` 时才挂载这两个工具（single 模式不暴露）
- [x] **子 agent 执行器**（新函数，如 `_run_delegated_turn(agent, task, context, ...)`）：
  - [x] 解析目标 agent：`org_store` 查 `team_id`；构造 `for_project(project_dir, target_agent)` 记忆 view
  - [x] **缩小工具集**：基础工具（read/search/git/run_command 只读）+ 写工具按角色门控（默认给，便于干活；`role` 含 reviewer/auditor 等 → 只读）+ `delegate_*`/`create_*` 在深度将超时移除 + 移除 `ask_user`
  - [x] **系统提示**：`你是 {name}（{role}），隶属 {project} 团队{team}。你的上级/委派者是 {parent}。本次任务：{task}。上下文：{context}。完成后将结果回传给 {parent}，不要直接向用户汇报。`
  - [x] 有界执行：独立 thread（不污染 supervisor 会话的 checkpoint）+ `recursion_limit`（沿用现 `agent_run_config`）+ 超时
  - [x] HITL：子 agent 不产生 `ask_user` 中断（工具已移除）；写操作走既有 `command_approval_middleware`，但 approval 需求回传 supervisor 摘要，不直接弹用户
- [x] **SSE 帧**（`_stream` ~2418 与 `goal_stream` 复用点）：
  - [x] `{"type":"delegate_start","agent":<当前>, "to":<目标列表>, "tasks":<摘要>}`
  - [x] 串行：子 agent 逐 token 不做转发（内部执行），完成时 `{"type":"delegate_progress","from":<目标>,"status":"done","chars":N}`
  - [x] 并行：`{"type":"delegate_start","agents":[...]}` → 每个完成发 `delegate_progress` → 全部结束 `{"type":"delegate_end","agents":[...],"ok":K,"failed":[...]}`
  - [x] 前端忽略未知帧类型（向后兼容已有渲染逻辑）
- [x] **名册注入**：`MemoryMiddleware`（memory_middleware.py:30-69）渲染段前，追加「团队成员：name(role) — team」，来源 `org_store.roster()`；`injected()` 保持只注入本成员核心 + 本组及祖先组组级记忆（memory_discovery.py:166-182 扩展 teams）
- [x] **组级记忆注入**：`memory_discovery.py` `injected()`：当有 `team_id` 时，追加 `teams/{team_id}` 及祖先组的 GOALS/CONTEXT/MEMORY.md（按 `team_ancestors`）
- [x] **记忆落盘**：nudge / auto-extract 沿用 `bound_agent` 机制 → 子 agent 的 nudge 写入**自身** `BASE/MEMORY.md`（`memory_manager.after_turn` 已按 bound 解析，确认无 DEFAULT_AGENT 泄漏）

## 阶段 3 — 创建 agent 与团队（工具 + 设置）

方向：agent 能通过工具创建新成员/部门（受 `allow_agent_creation` 门控）；前端提供团队管理面板与 single/multi 开关。

- [x] **`backend/coworker/agents.py` 新增工具**：
  - [x] `create_team_member(name, role, description, superior)`：受门控（`org.allow_agent_creation` 为 false → 报「项目已禁止 agent 自建成员」）；superior 缺省=调用者；调 `POST /api/org/agent` 同源逻辑（直接调 `org_store` + `ensure_agent`）；返回新成员名/角色
  - [x] `create_team(name, lead, parent_team_id)`：同样门控；调 org_store 校验（lead 必须是已存在 agent）
  - [x] 两个工具随 `delegate_*` 同条件挂载（multi 模式）
- [x] **前端项目设置团队面板**（`frontend/src/components/settings/`）：
  - [x] 新组件 `OrgSettingsPanel.tsx`（接入 `SettingsView.tsx`，需 project_id 上下文）
    - [x] 组织树展示（组织 → 部门 → 成员），成员卡片显示 name/role/parent/team/status
    - [x] 新建/编辑 agent：name、role、description、superior（下拉）、所属 team（下拉）
    - [x] 新建/编辑 team：id、name、lead（下拉，限本组 agent）、parent_team（下拉）
    - [x] 停用/删除 agent；删除空 team
    - [x] 配置区：mode 单选（multi/single）、max_depth、max_concurrent、allow_agent_creation 开关
  - [x] `frontend/src/services/chatService.ts`：`getOrg`/`createOrgAgent`/`updateOrgAgent`/`deleteOrgAgent`/`createOrgTeam`/`updateOrgTeam`/`deleteOrgTeam`/`updateOrgConfig`
  - [x] `frontend/src/types.ts`：`Org`/`OrgAgent`/`OrgTeam`/`OrgConfig` 类型
  - [x] `frontend/src/App.css`：org 面板样式（复用 memory-tree 风格）
  - [x] `frontend/src/locales/*.json`（zh/zh-TW/zh-HK/en/ja/ko/fr）：org.* key（面板标题、字段、校验错误、确认文案）
- [x] **聊天委托可视化**（`frontend/src/components/ChatArea.tsx` 或现有消息渲染）：
  - [x] 解析 `delegate_start/progress/end` 帧 → 展示「default_agent → coder 委托中…」活动卡片
  - [x] 消息气泡 agent 归属标签

## 阶段 4 — 记忆面板 teams 节点 + 测试与验收

- [x] `frontend/src/components/MemoryPanel.tsx`：项目节点下渲染 `teams/<id>/` 为「部门」分支（复用 FolderBranch 结构，可浏览/编辑组级 GOALS/CONTEXT/MEMORY.md）
- [x] `frontend/src/types.ts` `MemoryProjectView` 加 `teams`（后端 `discover` 输出对齐）
- [x] **测试**：
  - [x] `backend/coworker/memory/selftest.py` 扩充：
    - [x] org 校验（唯一性、引用存在、无环、depth 超限拒绝、删除被引用 agent 拒绝）
    - [x] 迁移回填（无 `.org.json` 但目录存在 → discover 自动补）
    - [x] 组级记忆注入（`injected()` 带 team_id → 含本组 + 祖先组文件）
    - [x] 名册 `roster()` 输出
    - [x] 绑定：`for_project(project_dir, agent)` 返回各自 view（替代 DEFAULT_AGENT 断言）
  - [x] `backend/coworker/memory/stress_test.py` 扩充：HTTP 冒烟（建 agent/team → `delegate_task`/`delegate_parallel` 往返 → 各自记忆落盘 → 组记忆落盘）
  - [x] `backend/coworker/org.py` 单测（若项目测试约定允许独立测试文件，否则并入 selftest）
- [x] **全量验证**：
  - [x] `backend/venv/bin/python -c "import main"`（后端 import 无错）
  - [x] `backend/venv/bin/python coworker/memory/selftest.py`（全绿）
  - [x] `backend/venv/bin/python coworker/memory/stress_test.py`（全绿）
  - [x] `npx tsc --noEmit`（前端类型无错）
  - [x] `npm run build`（前端构建通过）
  - [x] `node --check electron/main.js` / `electron/preload.js`（如涉 IPC）
  - [x] HTTP 冒烟（临时实例）：建项目 → 立即有 default_agent 实体；建 agent/team；串行委托；并行委托（多 agent 并发）；各自记忆落盘；组记忆注入；`mode=single` 时工具不挂载

## 阶段 5 — 文档

- [x] 更新 `docs/task_list/multi_agent_org.md` 勾选完成项（本文件，按进度打 [x]）
- [x] 若涉及前端 `electron.d.ts`/`preload.js` IPC（委托帧无需 IPC，纯 SSE）→ 无需改 electron；确认无遗漏后记录

## 明确不做（本轮边界）

- 跨 agent/组记忆互读（仅名册 + 本组/祖先组组级记忆注入；成员间记忆隔离）
- 多项目组织（跨 project 的公司）——组织容器 = 单项目，与「1 个形态」一致
- 全 token 级并行流合并（并行期间仅发完成摘要，不交错逐 token）
- 拖拽移动（记忆面板 ③ 遗留项，另行迭代）

## 关键文件清单

| 文件 | 改动 |
|---|---|
| `backend/coworker/org.py` | **新增**：Agent/Team dataclass、OrgStore、校验、roster、team_ancestors |
| `backend/coworker/sessions.py` | Session/SessionMessage 加 `agent_id` |
| `backend/coworker/agents.py` | 4 处 DEFAULT_AGENT 替换、delegate_task/delegate_parallel/create_team_member/create_team、子 turn 执行器、系统提示、SSE 帧 |
| `backend/coworker/memory/memory_discovery.py` | teams 发现、injected() 组级记忆、roster 段 |
| `backend/coworker/memory/memory_manager.py` | for_project/render_for 按 agent（确认无泄漏） |
| `backend/coworker/memory/memory_middleware.py` | 名册注入段 |
| `backend/main.py` | /projects 实体化、register-agent 升级、/api/org/* 端点、ChatRequest.agent、运行时 agent 透传、存量迁移 |
| `backend/coworker/projects.py` | （不改；org 配置收敛到 .org.json） |
| `backend/coworker/memory/selftest.py` / `stress_test.py` | 扩充用例 |
| `frontend/src/types.ts` | Org 类型、ChatRequest.agent、MemoryProjectView.teams |
| `frontend/src/services/chatService.ts` | org CRUD、agent 透传 |
| `frontend/src/components/settings/OrgSettingsPanel.tsx` | **新增**：团队管理面板 |
| `frontend/src/components/settings/SettingsView.tsx` | 挂载 OrgSettingsPanel 入口 |
| `frontend/src/components/MemoryPanel.tsx` | teams 部门节点 |
| `frontend/src/components/ChatArea.tsx` | 委托可视化 + agent 归属 |
| `frontend/src/App.tsx` / `electron.d.ts` | 会话 agent 记录/展示（如需） |
| `frontend/src/App.css`、`frontend/src/locales/*.json` | org 样式与文案 |

---

## 完成记录（2026-08-16）

全部阶段已实施并验证通过：

- 实体层：`coworker/org.py`（OrgStore/Agent/Team/校验/roster/team_ancestors）；`POST /projects` 立即实体化 default_agent；register-agent 升级；discover 迁移回填；`/api/org/*` 端点
- agent 绑定：`Session.agent_id`/`SessionMessage.agent_id`、`ChatRequest.agent`、两个 runtime 的 `_memory` 按 `self.agent`、registry 透传 agent
- 团队指挥：`delegation.py`（Delegator：delegate/delegate_parallel/create_agent/create_team + 子图执行 + 层级提示 + 深度/活跃校验 + reviewer 只读）；SSE 帧 `delegate_start/progress/end`；名册 + 组级记忆注入（`render_for` + `injected(team_ids=…)`）
- 前端：`OrgSettingsPanel`（agent/team CRUD + config）、委托可视化 `DelegateBlock`（`PartDelegate`）、记忆面板 teams 部门节点、`MemoryTeamView`
- 测试：selftest 77→93 项、stress 99 项（含 org HTTP 冒烟）全绿；`import main`、tsc、vite build（node 22）、electron node --check 通过
- HTTP 冒烟（临时实例）：建项目→org 默认 default_agent；建 agent/team；团队记忆目录；discover 含 teams；per-agent 记忆隔离；config 切换 single/multi；删除约束正确

### 实现说明 / 偏差
- 委托工具为**同步工具**（LangGraph 在 async 流中自动经 executor 执行），子图用独立 thread id 不污染会话 checkpoint
- 子 agent 默认仅只读工具（read/search/git），reviewer 类角色强制只读；写工具未在子 agent 中启用（避免嵌套 HITL 审批中断），需要写文件的委派由上层 agent 自己执行
- `delegate_parallel` 用线程池并发（工具为同步），SSE 仅发完成摘要不逐 token 交错
- 前端 `exactOptionalPropertyTypes` 下 `PartDelegate` 可空字段显式标 `| undefined`
- 验证需 node ≥ 20（`styleText`），本地用 v22.23.1

# 侧栏会话三级化 + Agent 团队管理移入项目级设置 — 开发任务清单

目标：在已完成的多 Agent 框架（项目 = 组织容器，`.org.json` 为单一真相源）之上，重构前端信息架构：

1. **侧栏会话列表三级化**：把现在「项目 → 平铺会话」改为「**项目 → agent → 会话**」的树形展示；会话归属由 `Session.agent_id` 决定（后端 `Session.public()` 已返回 `agent_id`）。
2. **新会话支持选择 agent**：composer 顶部（workspace 选择器旁）新增 agent 下拉，默认 `default_agent`；提供「项目级 [+]」「agent 级 [+]」「顶部新会话」三种入口，允许用户选定要对话的 agent。
3. **Agent 团队管理移入项目级设置**：从全局设置页（齿轮 → SettingsView 的 org 分组）迁出，改为「项目标题 ⋯ 菜单 → Agent 团队管理」打开项目专属团队页；全局设置回归纯全局项。
4. **支持 agent 更名**（含 `default_agent`）：只改显示 `name`，`id` 不变（id 是记忆目录/会话绑定/委托目标的唯一键），保证记忆、会话、委托不因改名而错乱。

形态定义（最终态）：
```
侧栏：
  ▼ <项目>
    ▼ <agent> · <role> · N 个会话 · <部门>      ← agent 子标题（可折叠，disabled 灰显常驻）
      ├─ <会话标题>                  ⏱ <相对时间>
      └─ [展开更多 ▾]                ← 每个 agent 组内各自 10 条 + 展开更多
    ▸ <agent2> · <role> · M 个会话 · <部门>
```

---

## 关键设计决策（已确认）

| # | 决策 |
|---|---|
| 1 | 新会话加 agent 选择器，默认 `default_agent` 对话 |
| 2 | 项目全量会话页（ProjectSessionList）与侧栏同步三级化 |
| 3 | 入口形式 = 项目标题 `⋯` 菜单加「Agent 团队管理」 |
| 4 | roster 数据源 = 方案 b：后端在 `/projects` 响应里附带各项目 agent 名册（含 status，供侧栏灰显 disabled） |
| 5 | agent 子标题显示「名称 · role · 会话数 · 部门」 |
| 6 | disabled agent 灰显常驻；删除直接撤走；无「隐藏」行为 |
| 7 | 三种新会话入口：顶部新会话 / 项目标题行 [+]/ agent 子标题 [+]，agent 下拉放在 composer 顶部（workspace 选择器旁） |
| 8 | default_agent 允许更名（改 `name`，`id` 不变） |
| 9 | 侧栏分页：每个 agent 组内各自 10 条 + 展开更多 |

数据流说明：
- `GET /projects` → 每个项目附 `roster: [{id, name, role, team, status}]`（含 disabled），未迁移项目返回空 → 前端降级为仅 `default_agent`。
- 会话 `agent_id`（缺失/空 → 按 `default_agent` 归组）→ 经 roster 的 `id → name/role/team/status` 映射成 agent 子标题。
- `ChatRequest.agent` 传 agent id（默认 `default_agent`）→ 后端把会话绑定到该 agent（main.py:716 已支持 `request.agent or DEFAULT_AGENT`；`create_session` 补 `agent_id` 入参让新建会话直接带 agent）。

---

## 阶段 0 — 后端：roster 随项目返回 + 会话创建带 agent + agent 更名

方向：为前端三级树与 agent 选择器提供数据与写接口，全部保持向后兼容（未迁移项目降级为 default_agent）。

- [ ] **`GET /projects` 附带 roster**（`backend/main.py` ~2601 `list_projects`）：
  - [ ] `workspace_controller.public_project`（`backend/coworker/workspace_controller.py:65`）或 `list_projects` 循环里，为每个项目追加 `roster` 字段
  - [ ] roster 来源：`org_store.load(project.memory_dir)` → 输出 `[{id, name, role, team, status}]`（**含 disabled**，供侧栏灰显；与上下文注入用的 active-only `roster()` 不同，这里需要独立方法或带参数）
  - [ ] 实现：`OrgStore`（`backend/coworker/org.py`）新增 `members_for(org) -> list[dict]`（含 status 的全量成员，缺省输出 id/name/role/team/status），或给现有 `roster()` 加 `include_disabled=True` 参数
  - [ ] `.org.json` 缺失（未迁移项目）→ `roster=[]`，不触发 `_ensure_org`（列表页不写盘）；前端降级为 `default_agent`
  - [ ] `POST /projects` 返回的 project 也附 `roster`（新建即 default_agent 实体，roster 含 default_agent）

- [ ] **会话创建支持 agent**（`backend/main.py` 2110 `SessionCreateRequest` / 2141 `create_session`）：
  - [ ] `SessionCreateRequest` 加 `agent_id: str = ""`
  - [ ] `create_session` 把 `request.agent_id` 传给 `session_store.create(..., agent_id=request.agent_id)`（`backend/coworker/sessions.py:191` 已支持该参数）
  - [ ] 空 `agent_id` 时保持现有行为（会话在首条消息绑定 default_agent），不破坏存量

- [ ] **Agent 更名（#8）**（`backend/main.py` 1065 `OrgAgentUpdateRequest` / 1175 `org_update_agent`）：
  - [ ] `OrgAgentUpdateRequest` 加 `name: str | None = None`
  - [ ] `org_update_agent` 加 `if request.name is not None: agent.name = request.name`
  - [ ] 校验：新 name 非空；只允许改显示名，`id` 不可改（id 是记忆目录/会话/委托的唯一键）
  - [ ] 确认 `_ensure_org` 迁移逻辑（main.py:603-614）按 `id` 判断已存在 → default_agent 改名后不会重复添加
  - [ ] 名册注入（`OrgStore.roster()`，org.py:268）取新 `name`，上下文/UI 同步显示新名

- [ ] **后端测试补强**（`backend/coworker/memory/selftest.py`）：
  - [ ] `members_for`（含 disabled）输出正确
  - [ ] PATCH agent `name`：改名后 roster/`_org_public` 显示新名、`id` 不变、记忆目录不变、会话 `agent_id` 不变
  - [ ] 空 name 改名被拒绝

## 阶段 1 — 前端类型与数据接入

方向：让侧栏/全量页/选择器拿得到 agent 归属与 roster。

- [ ] **`frontend/src/types.ts`**：
  - [ ] `SessionSummary` 加 `agent_id?: string`（后端已返回）
  - [ ] `ProjectEntry` 加 `roster?: OrgRosterEntry[]`（复用现有 `OrgRosterEntry`，需确认含 `status`；若无则 `OrgRosterEntry` 加 `status?: OrgAgentStatus`）
  - [ ] `AppView` 加 `'org'`（项目级团队页视图）
  - [ ] `ChatRequest.agent` 已存在，不改

- [ ] **`frontend/src/services/chatService.ts`**：
  - [ ] `listProjects`/`createProject` 返回值类型对齐（roster 透传即可，无需新方法）
  - [ ] `createSession` 请求体支持 `agent_id`（`CreateSessionRequest` 加 `agent_id?: string`）

## 阶段 2 — 侧栏三级化（核心）

方向：`WorkspaceSidebar.tsx` 的 `ProjectRow` 从「平铺会话」改为「按 agent 分组 + 组内折叠 + 组内分页」。

- [ ] **分组逻辑**（`frontend/src/components/WorkspaceSidebar.tsx` `ProjectRow`，104-215）：
  - [ ] 从 `project.roster` 建 `Map<agentId, rosterEntry>`；会话按 `session.agent_id || 'default_agent'` 归组 → `Map<agentId, SessionSummary[]>`
  - [ ] 组排序：`default_agent` 组最前，其余按组内最新 `updated_at` 降序
  - [ ] 无会话但 active 的 agent 也显示（0 会话）；roster 缺该 agent_id 时降级（仅 default_agent / 原样显示 id）
  - [ ] **停用 agent 灰显常驻**：roster 含 disabled → 组标题灰显、可折叠、会话仍归其下；无隐藏行为
- [ ] **Agent 组子标题组件**（新增 `AgentGroup`，或 `ProjectRow` 内联）：
  - [ ] 显示「名称 · role · N 个会话 · 部门名」（部门名来自 roster `team` 字段）
  - [ ] 可折叠（独立 `expandedAgentIds` state）；打开项目时默认展开所有有会话的组
  - [ ] 右侧 **[+] 新会话按钮**（#7）→ `onNewChat(project.id, agentId)`：直接与该 agent 对话
  - [ ] agent 组内**各自 10 条 + 展开更多**（组内分页，#9），沿用现有 `pg-btn`/`sidebar-project__footer` 样式
- [ ] **侧栏项目菜单（#3）**：
  - [ ] 项目标题 `⋯` 下拉（142-156）在「会话历史」下加「👥 Agent 团队管理」→ 新 prop `onOpenOrgSettings(project.id)`
- [ ] **props 扩展**（`WorkspaceSidebarProps`，15-34）：
  - [ ] `onOpenOrgSettings: (projectId: string) => void`
  - [ ] `onNewChat` 签名扩展为 `(projectId?: string, agentId?: string) => void`
  - [ ] `SessionRow`/`ProjectRow` 透传 `onNewChat(projectId, agentId)`

## 阶段 3 — 新会话 agent 选择器 + 三种入口接线

方向：composer 顶部 agent 下拉 + 项目/agent 各自的 [+] 直达。

- [ ] **`frontend/src/components/ChatInput.tsx` — agent 下拉**（图 5）：
  - [ ] 新 props：`agentOptions?: OrgRosterEntry[]`、`activeAgentId?: string`、`onSelectAgent?: (id: string) => void`
  - [ ] `showWorkspacePicker` 区块（557-606）的 workspace 下拉旁加 `👤 agent` 下拉：显示「名称 · role」；disabled 灰显不可选；默认选中 `default_agent`
- [ ] **`frontend/src/App.tsx` 接线**：
  - [ ] `startProjectDraft(projectId?, agentId?)`（1840）：新增 `draftAgentId` state，默认 `'default_agent'`（#1）；agentId 传入时置为该值
  - [ ] `startNewChat(projectId?, agentId?)`（1860）透传；顶部新会话（onNewChat() 无参）→ draftAgentId='default_agent'
  - [ ] 打开现有会话（`openSession`）时清掉 `draftAgentId`（会话自带 agent_id，不覆盖）
  - [ ] 发消息请求（1055-1077）加 `...(draftAgentId ? { agent: draftAgentId } : {})`
  - [ ] composer 传 `agentOptions={activeProject?.roster}`、`activeAgentId={draftAgentId}`、`onSelectAgent={setDraftAgentId}`（2704 附近）
  - [ ] 新建会话（首条消息前的 `createSession`，643）传 `agent_id: draftAgentId`（配合阶段 0 的 `create_session` 入参）

## 阶段 4 — 全量会话页三级化 + 项目级团队页

方向：点项目打开的 ProjectSessionList 与侧栏一致三级化；团队管理从全局设置迁出。

- [ ] **`frontend/src/components/ProjectSessionList.tsx`（#2）**：
  - [ ] 按 `project.roster` + `session.agent_id` 分组（与侧栏同逻辑）
  - [ ] 每组渲染 agent 标题行（名称 · role · 会话数 · 部门，disabled 灰显）+ 组内会话平铺 + 组内「展开更多」
  - [ ] 组标题行右侧 **[+] 新会话** → `onNewChat(project.id, agentId)`
  - [ ] 页眉 action 区（或项目标题行）加「👥 Agent 团队管理」按钮（与侧栏菜单双入口）→ 新 prop `onOpenOrgSettings(project.id)`
- [ ] **移除全局设置里的 Agent 团队**（`frontend/src/components/settings/SettingsView.tsx`）：
  - [ ] 删除 `settingsPage === 'org'` 分支（85-101）
  - [ ] 删除 SettingsList 的 `org` 分组（253-267）
  - [ ] 删除 `OrgSettingsPanel` import；清理 `activeProjectId` prop（App.tsx:2744 同步）
  - [ ] 全局设置回归纯全局项（外观/语言/记忆/审计/关于）
- [ ] **项目级团队页**（新 `frontend/src/components/settings/OrgSettingsPage.tsx` 或复用 WorkspacePage 包装）：
  - [ ] `AppView` 加 `'org'` + App.tsx `orgProjectId` state
  - [ ] `onOpenOrgSettings(projectId)`：`setOrgProjectId(projectId); setActiveView('org')`
  - [ ] 渲染分支（App.tsx:2716 附近）加 `activeView === 'org'` → `<OrgSettingsPage projectId={orgProjectId} onBack={() => setActiveView('chat')} />`
  - [ ] 页面标题 = 「<项目名> · Agent 团队」，返回按钮回 chat；内含现有 `OrgSettingsPanel`（改传显式 projectId）
  - [ ] 侧栏在 `'org'` 视图下保持对应项目标题 active（复用 `activeProjectId === project.id` 逻辑）
- [ ] **`OrgSettingsPanel.tsx` 更名支持（#8）**：
  - [ ] 成员列表每行加「改名」动作（内联输入或轻量弹窗）→ `updateOrgAgent({ id, name })`
  - [ ] 改名后刷新 roster（load() 已有）；确认改 default_agent 后侧栏/全量页显示新名、会话仍归该组

## 阶段 5 — 文案与样式

- [ ] **`frontend/src/locales/*.json`（zh/zh-TW/zh-HK/en/ja/ko/fr）**：
  - [ ] 新增：`sidebar.org_team_manage`（项目菜单）、`sidebar.new_chat_with_agent`、`chat.agent_pick`、`settings.org_rename`、`settings.org_rename_placeholder`、`sidebar.agent_sessions_count` 等
  - [ ] 复用/迁移现有 `settings.org_*` key（面板内部文案不变）
- [ ] **`frontend/src/App.css`**：
  - [ ] agent 组子标题样式（`sidebar-agent` 系列，复用 `sidebar-project` 风格；disabled 灰显类）
  - [ ] composer agent 下拉样式（复用 `composer__ws-chip` 系列或新增 `composer__agent-chip`）
  - [ ] 全量页 agent 分组标题样式；项目级团队页布局（复用 org 面板既有样式）

## 阶段 6 — 验证与验收

- [ ] **后端**：
  - [ ] `cd backend && ./venv/bin/python -c "import main"`（import 无错）
  - [ ] `./venv/bin/python coworker/memory/selftest.py`（全绿，含新增用例）
  - [ ] `./venv/bin/python coworker/memory/stress_test.py`（全绿）
  - [ ] `py_compile` 相关改动文件
- [ ] **前端**：
  - [ ] `export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH" && npx tsc --noEmit`（类型无错）
  - [ ] `npm run build`（构建通过）
  - [ ] `node --check electron/main.js` / `electron/preload.js`（如涉 IPC）
- [ ] **HTTP 冒烟（临时实例 9528/9529）**：
  - [ ] `GET /projects` 每个项目含 `roster`（含 disabled）
  - [ ] 新建项目 → roster 含 default_agent；`POST /sessions` 带 `agent_id=coder` → 会话 agent_id=coder
  - [ ] `PATCH /api/org/agent {id:default_agent, name:"<新名>"}` → roster 新名、id 不变、记忆目录不变
- [ ] **手工 UI 冒烟**：
  - [ ] 侧栏：项目 → agent 组（名称·role·会话数·部门）→ 会话三级展示；组内折叠；组内 10 条 + 展开更多；disabled 灰显
  - [ ] 三种新会话入口：顶部新会话（默认 default_agent）/ 项目 [+]（默认 default_agent）/ agent [+]（直达该 agent）；composer agent 下拉可选、默认 default_agent
  - [ ] 用 coder 发消息 → 会话归入侧栏 coder 组
  - [ ] 项目 ⋯ → Agent 团队管理 → 项目级团队页（改名 default_agent → 侧栏/全量页显示新名）
  - [ ] 全局设置页不再有「Agent 团队」分组

## 明确不做（本轮边界）

- 会话拖拽移动 / 跨 agent 会话迁移（仅按 agent_id 静态归组）
- 会话归属随 agent 更名自动重写（会话 `agent_id` 保持 id，靠 roster 映射显示新名）
- agent 级记忆目录改名（id 不变，目录不动）
- 多项目组织 / 跨项目 roster

## 关键文件清单

| 文件 | 改动 |
|---|---|
| `backend/main.py` | `list_projects`/`POST /projects` 附 roster；`SessionCreateRequest.agent_id`；`OrgAgentUpdateRequest.name` + `org_update_agent` 改 name |
| `backend/coworker/workspace_controller.py` | `public_project` 输出 roster |
| `backend/coworker/org.py` | `members_for`（含 disabled）或 `roster(include_disabled=…)` |
| `backend/coworker/sessions.py` | （`create` 已支持 agent_id，无改动或微调） |
| `backend/coworker/memory/selftest.py` | members_for / 改名 / 空 name 拒绝用例 |
| `frontend/src/types.ts` | `SessionSummary.agent_id`、`ProjectEntry.roster`、`OrgRosterEntry.status`、`AppView` + `'org'`、`CreateSessionRequest.agent_id` |
| `frontend/src/services/chatService.ts` | createSession 透传 agent_id |
| `frontend/src/components/WorkspaceSidebar.tsx` | 三级化分组 + AgentGroup + 组内分页 + 项目菜单「Agent 团队管理」+ props |
| `frontend/src/components/ProjectSessionList.tsx` | 三级化 + 组标题 [+]/团队管理按钮 |
| `frontend/src/components/ChatInput.tsx` | agent 下拉（props + 渲染） |
| `frontend/src/components/settings/SettingsView.tsx` | 移除 org 分组与分支 |
| `frontend/src/components/settings/OrgSettingsPage.tsx` | **新增**：项目级团队页包装 |
| `frontend/src/components/settings/OrgSettingsPanel.tsx` | 更名动作（name 编辑） |
| `frontend/src/App.tsx` | draftAgentId、agent 透传、AppView 'org' + orgProjectId、onOpenOrgSettings、composer/侧栏/全量页接线 |
| `frontend/src/App.css` | sidebar-agent 系列、composer agent chip、全量页分组样式 |
| `frontend/src/locales/*.json`（7 个） | 新增 agent/团队管理相关 key |

---

## 完成记录

（实施完成后在此勾选并记录验证结果）

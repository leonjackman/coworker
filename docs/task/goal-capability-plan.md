# `/goal` 目标能力开发方案（cw 对标 codex）

> 本文档完整分析 codex 的 `/goal`（thread goal）功能，并给出 cw（coworker）的对标实现方案，
> 目标是让 cw 拥有与 codex **一致的实现方式与行为模式**：用户通过 `/goal` 设定持久目标、目标钉在窗口、
> agent 在目标未达成前**无用户输入地自动连续推进多轮**，由模型通过工具声明完成/受阻，状态离开 active 即停止续跑。

---

# 一、codex `/goal` 功能完整分析

## 1.1 功能定位与用户接口

codex 把目标能力称为 **thread goal**（特性开关 `Feature::Goals`）。用户接口是一个斜杠命令：

- TUI：`/goal [<objective>|clear|edit|pause|resume]`（`codex-rs/tui/src/goal_display.rs:5`）。
- 语义：
  - `/goal <目标>` —— 设定并激活一个目标；
  - `pause` / `resume` —— 暂停 / 恢复目标的自动续跑；
  - `clear` —— 清除目标；
  - `edit` —— 修改 objective。

关键点：**目标不是一轮任务的输入，而是绑定 thread 的持久状态**。一轮结束后若目标仍 active，系统自动开下一轮。

## 1.2 数据模型与状态机

`codex-rs/state/src/model/thread_goal.rs:12` 定义 `ThreadGoalStatus`：

```
Active → Paused | Blocked | UsageLimited | BudgetLimited | Complete
```

`ThreadGoal`（`thread_goal.rs:61`）字段：

| 字段 | 含义 |
|---|---|
| `objective` | 用户给定的目标文本 |
| `status` | 状态机当前值 |
| `token_budget` | 可选 token 预算 |
| `tokens_used` | 已消耗 token |
| `time_used_seconds` | 已用时间（秒） |
| `created_at` / `updated_at` | 时间戳 |

持久化在 SQLite state DB 的 goals 表，每个 thread 至多一条活跃目标。

## 1.3 架构分层（四层）

1. **TUI 层**：`tui/src/app_server_session.rs:1330` 的 `thread_goal_set / get / clear` 发 JSON-RPC 给 app-server；`goal_display.rs` 负责渲染钉窗状态条。
2. **app-server 层**：`app-server/src/request_processors/thread_goal_processor.rs` 处理 `thread/goal/set|get|clear`（`message_processor.rs:1186`），校验特性开关、写 state DB、发 `ThreadGoalUpdatedNotification`（协议在 `app-server-protocol/schema/typescript` 的 `ClientRequest` / `ServerNotification`）。
3. **扩展层**：`codex-rs/ext/goal/`（核心引擎）。`GoalExtension`（`extension.rs`）通过 `codex_extension_api` 挂载生命周期钩子。
4. **state 层**：`codex_state::thread_goals()` 做 CRUD + 记账（`state/src/runtime/goals.rs`）。

## 1.4 自动续跑循环（核心机制，逐步骤）

- 钩子：`GoalExtension::on_thread_idle`（`extension.rs:148`）→ `runtime.continue_if_idle()`（`runtime.rs:362`）。
- `continue_if_idle()` 判定：`tools_visible`（enabled 且非子代理）且 goal 状态 == `Active` 且 thread 空闲，则：
  1. 用 `continuation.md` 模板（`steering.rs:45` + `templates/goals/continuation.md`）渲染出一段 **内部上下文片段（`InternalModelContextFragment`，非用户消息、前端不显示）**，内含 objective、token 预算、以及严格的「完成审计 / blocked 审计」指令。
  2. 调用 `thread.start_turn_if_idle(TurnInput::ResponseItem(item))`（`runtime.rs:408`）—— **无用户输入自动开启下一轮 turn**。
- 新一轮 turn 结束 → thread 再次 idle → `on_thread_idle` 再触发 → 再开一轮。如此自延伸，直到 goal 状态不再是 `Active`。

这正是与"单轮一问一答"的本质区别：单轮是用户消息驱动一轮；目标模式是 **active 目标驱动自动连续多轮**。

## 1.5 记账与兜底

`accounting.rs` + 钩子（`extension.rs`）：

- `on_turn_start / on_turn_stop / on_turn_abort`、`on_token_usage`、`on_tool_finish` 累计 `tokens_used` / `time_used_seconds`。
- 超 `token_budget` → `BudgetLimited`，并注入 budget 提示（`steering.rs:37`，`templates/goals/budget_limit.md`），循环停。
- turn 非可重试错误 → `Blocked`；用量超限 → `UsageLimited`；二者都停止循环。

## 1.6 模型侧工具

给模型三个 Responses API 工具（`spec.rs`，仅当 goal 启用时由 `ToolContributor::tools` 注册，`extension.rs:414`）：

- `create_goal(objective, token_budget?)`
- `get_goal()`
- `update_goal(status: complete | blocked)`

关键约束（`spec.rs`）：模型**只能**用 `update_goal` 把状态置 `complete` / `blocked`；`complete` 须通过严格的逐条需求审计；`blocked` 须同一阻塞连续 ≥3 轮；暂停 / 恢复 / 预算由用户 / 系统控制，模型无权改。

## 1.7 钉窗 UI 与通知

`ThreadGoalUpdatedNotification` 推动 TUI 状态条（`goal_display.rs`）：

- `format_goal_elapsed_seconds` —— 已用时间（s/m/h/d）。
- `goal_usage_summary` —— objective + 时间 + token 用量 / 预算。
- `goal_status_label` —— 状态标签：active / paused / stalled / usage limited / limited by budget / complete。

---

# 二、cw 现状对照与缺口

| codex 能力 | cw 现状 | 缺口 |
|---|---|---|
| thread goal 持久化 | `sessions.py` 无 goal 字段 | 需加 `GoalState` |
| `/goal` 命令 + set/clear/pause/resume/get API | 无 | 需加端点 |
| `on_thread_idle` 自动续跑循环 | 无（一轮一请求 `/chat/stream`） | 需加续跑循环 |
| continuation 内部上下文注入 | 有 `steer.py` 插话 inbox + `SteerInjectionMiddleware`（`middleware.py:498`），但仅用于用户插话 | 扩展为 goal 续跑注入 |
| 模型工具 `update_goal` | 无 | 需加 `manage_goal` 工具 |
| token/time 记账 + 预算兜底 | 有 `context_usage` 帧但无 goal 维度 | 需加 goal 记账 |
| 钉窗状态条 + 通知 | 无 | 需加前端 goal bar |
| 严格完成/受阻审计提示词 | 无 | 移植 `continuation.md` |

cw 架构：`backend` FastAPI + LangGraph（`runtime.py` 的 `event_stream()` 一次 `/chat/stream` 跑一轮 turn 后发 `done`）；前端 React，`App.tsx:858 interjectQueuedMessage` 已实现「插话自动续跑」模式（`/chat/interject` 持久化 steer → 再用 `skip_user_append=true` 调 `/chat/stream`）。这是 cw 最接近 codex `start_turn_if_idle` 的现成机制。

---

# 三、cw 对标实现方案（一步到位，行为对齐 codex）

## 3.0 对齐原则

**后端内部多轮循环**（对标 codex `continue_if_idle` 在同一个 thread 内自动起轮），不依赖前端轮询；Stop 仍通过中断 SSE 流实现。一轮 turn 结束后，若 session 有 `active` 目标，后端在同一 `/chat/stream` 连接里 **自动再跑一轮**（内部复用 `skip_user_append` 模式 + 注入续跑上下文），直到目标非 `active` 或用户 Stop。

> 设计取舍说明：备选方案是"前端驱动"（仿现有 interject 自动续跑，前端再调 `/chat/stream`）。本方案选择**后端内部循环**，因为它最忠实对齐 codex 的 `continue_if_idle` 行为，且 Stop 语义更干净（一次中断即终止整个续跑链，无需前端协调多段 SSE）。

## 3.1 数据模型层 — `backend/coworker/sessions.py`

新增：

```python
class GoalState(BaseModel):
    objective: str
    status: Literal[
        "active", "paused", "blocked",
        "complete", "budget_limited", "usage_limited"
    ]
    token_budget: int | None = None
    tokens_used: int = 0
    time_used_seconds: int = 0
    created_at: int = 0
    updated_at: int = 0
```

- `Session` 增加 `goal: GoalState | None = None`。
- `SessionStore` 增加：`get_goal / set_goal / clear_goal / update_goal_status / account_goal_usage`。

## 3.2 后端 API — `backend/main.py`

新增（对齐 `thread_goal_processor.rs`）：

- `POST /goal/set { session_id, objective, token_budget? }` → 置 `active` 并广播 `goal_updated`。
- `POST /goal/clear { session_id }`
- `POST /goal/pause { session_id }`
- `POST /goal/resume { session_id }`
- `POST /goal/edit { session_id, objective }`（仅 active 时可改 objective）
- `GET  /goal?session_id=...`

前端 `/goal` 命令 → 调 `/goal/set`（或 pause / resume / clear / edit）。

**交付口径（双通道）**：空闲态更新（无流式运行时）由端点 HTTP 响应体直接返回更新后的 `GoalState`，前端以响应刷新 GoalBar；流式态更新（模型运行中改状态）通过既有 SSE 事件总线推送 `goal_updated`。两通道落到同一前端 `goal` 状态。

## 3.3 续跑循环（核心落手点）— `main.py` 的 `chat_stream` `event_stream()`

当前 `event_stream` 在 `_handle_event` 收到 `done` 后仅结束（`main.py:1832`）。改造：

- 把单轮 `runtime.stream(...)` 包进 `while True` 外层循环。
- 在非首轮时，构造续跑消息：不从用户消息，而是把 `continuation.md` 风格提示作为 **内部上下文** 注入（见 3.6），并以 `skip_user_append=True` 复用 history。
- 每轮 `done` 后：`if session goal.status == "active" and not stopped: continue`（再跑一轮）；否则跳出。
- `error` / `approval_required` / `question_required` / Stop → 跳出（对标 codex `Blocked` / `UsageLimited` 停循环）。
- 每轮结束做记账（3.5）。

> 复用现有 `skip_user_append` 路径（`main.py:1744`）：首轮由用户消息驱动；续跑轮 `skip_user_append=True`，由注入的 goal 上下文驱动。

### 3.3.1 会话重开 / 前端刷新时恢复续跑（对标 codex `restore_inherited_goal_runtime`）

- 目标持久化在 `Session.goal`，但续跑循环只存在于活跃 `/chat/stream` 内。用户在目标执行中途刷新页面或重开会话会丢失续跑链。
- 解决：前端加载会话时拉 `GET /goal`；若返回 `active` 则渲染 GoalBar，并**自动发一次 `skip_user_append=True` 的 `/chat/stream`** 重启续跑（复用 3.3 同一循环，不新增主链）。若状态为 `paused/complete/blocked/budget_limited/usage_limited` 则只渲染、不重启。以前端「当前无进行中 stream」为防重入闸门。

## 3.4 模型工具 `manage_goal` — `backend/coworker/agent/core.py` + `graph.py`

对齐 `spec.rs`：注册工具：

```
manage_goal(status: "complete" | "blocked")
```

- 仅当 session 有 active goal 时可见（`build_workspace_tools` 按 session goal 开关）。
- 模型调用 → 持久化状态（`update_goal_status`）。
- **严禁**模型置 `paused` / `resume` / `budget`（由用户 / 系统控制）。
- 可选 parity：注册只读 `get_goal` 工具，让模型主动读取 objective / 预算 / 已用（不暴露变更）。
- 提示词约束照搬 `update_goal` 的严格审计描述（完成须逐条需求审计；blocked 须连续 ≥3 轮同一阻塞）。

## 3.5 记账与兜底 — `runtime.py` + `steer.py`

- 每轮结束用 `context_usage` 帧累加 `tokens_used`；用 wall-clock 差累加 `time_used_seconds` 到 `GoalState`（`account_goal_usage`）。
- `token_budget` 超 → 置 `budget_limited` 并通过 `steer_inbox` 注入 budget 提示（对标 `budget_limit.md`），循环停。
- turn 抛错（非可重试）→ `blocked`；用量超限 → `usage_limited`。

### 3.5.1 状态范围界定（避免遗留半成品）

- `usage_limited`：cw 无 org / 账户级用量上限检测，本专项**不自动触发**，仅在 `GoalState` 枚举保留位以对齐 codex schema；未来接入用量上限再补触发器。
- `stalled`：cw 不单独建模「active 但无进展」；续跑中若模型连续多轮无工具调用 / 无文件改动，仍显示为 `active`。stall 检测列为后续增强，不在本专项范围。

## 3.6 续跑上下文注入 — 扩展 `backend/coworker/agent/middleware.py` 的 `SteerInjectionMiddleware`

`SteerInjectionMiddleware.abefore_model`（`middleware.py:582`）当前消费 `steer_inbox`。扩展：

- `SteerEntry` 增加 `kind: "user" | "goal"` 字段（`steer.py:27`）。
- `goal` 类 entry 被渲染为 **内部指令块**（非 HumanMessage 用户泡泡，前端也不显示），内容为续跑 / 预算提示词。
- 续跑时由后端 `event_stream` 在每轮起调前 `steer_inbox.push(session, SteerEntry(kind="goal", content=continuation_prompt))`，下一轮 `abefore_model` 自动折入（`take_all`，`middleware.py:548`）。对标 codex `inject_active_turn_steering`。

## 3.7 continuation 提示词 — 移植 `ext/goal/templates/goals/continuation.md`

原样移植（含 objective、token 预算、完成审计、blocked 审计、fidelity 约束），改为渲染 `objective / tokens_used / token_budget / remaining_tokens` 的 Python 模板（放 `backend/coworker/goal_prompts.py` 或并入 `prompts.py`）。这是行为对齐的关键：保证模型不会"缩小目标"或"伪完成"。

续跑提示词核心要点（来自 codex `continuation.md`）：

- 目标跨轮持久，结束本轮不要求把目标缩到当前能做完的大小。
- 保持完整 objective；做不完好就推进真实终态、留 active，不要重定义成功标准。
- Work from evidence：以当前工作区 / 外部状态为准，先检查现状再依赖记忆。
- 多步任务用 plan 展示进度；平凡单步跳过规划开销。
- **Completion audit**：逐条从 objective / 文件 / 计划推导需求，对每条需求找权威证据（文件、命令输出、测试、运行状态）验证；间接 / 薄弱证据视为未完成，继续工作而非标记完成。
- **Blocked audit**：同一阻塞连续 ≥3 轮才置 `blocked`；达成后用 `update_goal(complete)` 保留用量记账。

## 3.8 钉窗 UI — 前端

- 新增 `goal` store（订阅后端 `goal_updated` SSE 事件）。
- `App.tsx` 顶部加 **GoalBar** 组件：objective、已用时间、token 预算进度条、状态标签（对齐 `goal_status_label`：active / paused / stalled / usage limited / limited by budget / complete）。
- 聊天输入框 `/` 命令卡加 `/goal` 子命令（set / clear / pause / resume），复用现有 skill 命令卡机制（`App.tsx:600`）。
- 自动续跑由后端驱动（前端只需在 `goal` 存在时显示「目标执行中」与 Stop 按钮）；`done` 处理沿用现有 `App.tsx:712` 逻辑，多轮事件顺序到达。

---

# 四、最佳落手点与实施顺序

| 阶段 | 文件 | 内容 |
|---|---|---|
| M1 数据+API | `sessions.py`, `main.py` | `GoalState` + `/goal/*` 端点 + `goal_updated` 广播 |
| M2 续跑循环 | `main.py` `event_stream` | while 多轮 + `skip_user_append` + 退出条件 |
| M3 上下文注入 | `middleware.py` `SteerInjectionMiddleware`, `steer.py` | `kind="goal"` 内部指令块 |
| M4 模型工具+记账 | `core.py`, `graph.py`, `runtime.py` | `manage_goal` 工具 + token/time 记账 + 预算/错误兜底 |
| M5 提示词+UI | `goal_prompts.py`, 前端 GoalBar + `/goal` 命令 | 移植 `continuation.md` + 钉窗 |

**最小可跑闭环**：M1 + M2 + M3 + `goal_prompts.py` 即可让「`/goal X` → 自动连跑多轮 → 目标栏显示进度 → Stop 停」跑通；M4 / M5 补完成审计与 UI 精致度。

---

# 五、行为对齐校验清单（对照 codex）

- [ ] `/goal <obj>` 后目标钉窗显示 active，无需用户再输入即自动开下一轮。
- [ ] 每轮结束若仍 active 则续跑；模型不得自行缩小目标（continuation.md 约束）。
- [ ] 模型只能 `manage_goal(complete|blocked)`；暂停 / 恢复 / 清理由用户命令控制。
- [ ] `complete` 须经逐条需求审计；`blocked` 须连续 ≥3 轮同一阻塞。
- [ ] 超 token 预算 → `budget_limited` 停 + 注入预算提示；turn 报错 → `blocked` 停。
- [ ] `pause` / `resume` 正确挂起 / 恢复续跑；`clear` 立即终止循环并清状态条。
- [ ] Stop 中断当前轮且不再自动续跑。

---

# 附：codex 关键代码索引

| 关注点 | 文件 |
|---|---|
| 状态枚举 / 数据模型 | `codex-rs/state/src/model/thread_goal.rs` |
| TUI 命令定义 | `codex-rs/tui/src/goal_display.rs:5` |
| TUI set/get/clear | `codex-rs/tui/src/app_server_session.rs:1330` |
| 请求处理器 | `codex-rs/app-server/src/request_processors/thread_goal_processor.rs` |
| 扩展生命周期钩子 | `codex-rs/ext/goal/src/extension.rs` |
| 自动续跑引擎 | `codex-rs/ext/goal/src/runtime.rs:362 continue_if_idle` |
| 续跑/预算/目标提示词 | `codex-rs/ext/goal/src/steering.rs` + `templates/goals/*.md` |
| 模型工具定义 | `codex-rs/ext/goal/src/spec.rs` + `tool.rs` |
| 记账 | `codex-rs/ext/goal/src/accounting.rs` |
| state 层 CRUD | `codex-rs/state/src/runtime/goals.rs` |

# 附：cw 关键落手点索引

| 关注点 | 文件 |
|---|---|
| 数据模型 | `backend/coworker/sessions.py` |
| 对话主流程 | `backend/main.py` `chat_stream` / `event_stream`（`main.py:1679` / `:1832`） |
| 插话机制（复用） | `backend/coworker/steer.py` + `agent/middleware.py:498,548,582` |
| 工具注册 | `backend/coworker/agent/core.py` + `graph.py` |
| 运行时 | `backend/coworker/agent/runtime.py` |
| 前端状态/命令 | `frontend/src/App.tsx`（`:600` 命令卡，`:712` done 处理，`:858` interject 续跑） |

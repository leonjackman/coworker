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
| continuation 内部上下文注入 | 有 `steer.py` 插话 inbox + `SteerInjectionMiddleware`（`middleware.py:498`），但仅用于用户插话 | 续跑**直接构造 `system` 首位消息**（见 3.6），不扩展 steer |
| 模型工具 `update_goal` | 无 | 需加 `update_goal` 工具 |
| token/time 记账 + 预算兜底 | 有 `context_usage` 帧但无 goal 维度 | 需加 goal 记账 |
| 钉窗状态条 + 通知 | 无 | 需加前端 goal bar |
| 严格完成/受阻审计提示词 | 无 | 移植 `continuation.md` |

cw 架构：`backend` FastAPI + LangGraph（`runtime.py` 的 `event_stream()` 一次 `/chat/stream` 跑一轮 turn 后发 `done`）；前端 React，`App.tsx:858 interjectQueuedMessage` 已实现「插话自动续跑」模式（`/chat/interject` 持久化 steer → 再用 `skip_user_append=true` 调 `/chat/stream`）。这是 cw 最接近 codex `start_turn_if_idle` 的现成机制。

---

# 三、cw 对标实现方案（一步到位，行为对齐 codex）

## 3.0 对齐原则

**后端内部多轮循环**（对标 codex `continue_if_idle` 在同一个 thread 内自动起轮），不依赖前端轮询；Stop 仍通过中断 SSE 流实现。一轮 turn 结束后，若 session 有 `active` 目标，后端在同一 `/chat/stream` 连接里 **自动再跑一轮**（内部复用 `skip_user_append` 模式 + 注入续跑上下文），直到目标非 `active` 或用户 Stop。

> 设计取舍说明：备选方案是"前端驱动"（仿现有 interject 自动续跑，前端再调 `/chat/stream`）。本方案选择**后端内部循环**，因为它最忠实对齐 codex 的 `continue_if_idle` 行为，且 Stop 语义更干净（一次中断即终止整个续跑链，无需前端协调多段 SSE）。

> **会话锁语义（与 codex 的真实差异，必须显式承认）**：codex 每轮 turn 之间 thread 真正 idle，用户消息可插入（goal 之后继续）。cw 的后端内部循环在**整个 goal 运行期占用单会话 stream 槽**（`_guard_session_not_streaming` + `_stream_tasks` 单任务模型），因此：
> - 用户新消息**只能排队**，且队列自动发送 gated 在"stream settle"（该 stream 要等 goal 完成才 settle）→ 队列消息被憋到 goal 结尾。
> - 编辑 / 重生成会被 409 拒绝。
> - 运行中可介入：**interject（steer）**、**输入框 Stop（停当前任务）**、**TodoBlock 的 goal pause/resume（停/恢复目标作用）**。
> 这是有意的取舍（换取单连接内完成续跑），必须在文档与前端交互文案中写明；若未来需要"轮边界插入普通消息"，在每轮 `done` 后检查 `steer_inbox.has_pending(session_id)` 或排队队列，有则暂停 goal 先答用户（列为后续增强，不在本专项范围）。

> **两个独立控制（用户拍板）**：
> - **输入框 Stop = 停任务**（选项 A）：abort 当前流，整个续跑 loop 随之死亡；goal 状态**保持不变（active）**，续跑需要一次 kick（新消息 / TodoBlock resume / 重开会话）。语义对齐 codex：goal 跨用户回合持续，`/goal set|resume` 或会话加载时再次自动起跑。
> - **TodoBlock goal pause = 停目标作用**：只把 goal 状态置为 `paused`，**不 abort 当前流**，当前轮正常跑完，下一轮边界 `_should_continue_goal` 读到非 active → break；`resume` 把状态置回 `active`。
> - **`/goal clear`（TodoBlock clear 按钮）= 删除目标**：终止一切续跑并清状态。
> - 两者互不替代：Stop 不碰 goal 状态，pause 不碰进行中的任务。

> **goal 严格会话隔离（用户拍板）**：goal 只作用 / 显示于当前 session，不跨会话；TodoBlock 只渲染当前活动会话的 goal，其他会话的 goal 不显示、不影响。

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

**交付口径（双通道）**：空闲态更新（无流式运行时）由端点 HTTP 响应体直接返回更新后的 `GoalState`，前端以响应刷新 TodoBlock goal section；流式态更新（模型运行中改状态）通过既有 SSE 事件总线推送 `goal_updated`。两通道落到同一前端 `goal` 状态。

### 3.2.1 Electron IPC 桥接层（打通前端 → 后端 `/goal`）

前端 `chatService` 全部经 `window.electronAPI.*` 调后端（`electron/preload.js` 暴露、`electron/main.js` 的 `ipcMain.handle` 转发到 `BACKEND_HOST:PORT`）。`/goal/*` 需同步打通该层，否则前端无法调用：

- `electron/preload.js`：暴露 `goalGet / goalSet / goalPause / goalResume / goalClear / goalEdit(session_id, ...)`（命名沿用现有 `getSession`/`stopSessionStream` 风格）。
- `electron/main.js`：对应 `ipcMain.handle('goal-get'|'goal-set'|'goal-pause'|'goal-resume'|'goal-clear'|'goal-edit', ...)`，向本地后端发起 HTTP（复用现有请求封装）。
- `frontend/src/electron.d.ts`：补方法型别声明。
- `frontend/src/services/chatService.ts`：`getGoal / setGoal / pauseGoal / resumeGoal / clearGoal / editGoal`，作为前端唯一入口（与 `openSession` 的 `getSession` 同构）。

该层与 `/goal/*` 端点**同步交付**（Phase 1），避免后端完成但前端无门可入。

## 3.3 续跑循环（核心落手点）— `main.py` 的 `chat_stream` `event_stream()`

当前 `event_stream` 在 `_handle_event` 收到 `done` 后仅结束（`main.py:1832`）。改造：

**循环落地（已拍板：单一生成器内层循环）**：多轮续跑包进**传给 `_publish_turn` 的 `stream_iter` 生成器内部**——每轮调用一次 `runtime.stream(...)`，依序 `yield` 每轮事件；`_publish_turn` / SSE 订阅 / 会话事件总线只建一次，整个 goal 运行期保持单条 SSE 连接。每轮起点**重置 `terminal_sent` / `interrupt_emitted` 等终态旗标**（否则第 2 轮起 `_on_error`/`_on_end` 的「已终态」短路逻辑错乱），每轮收尾 `_persist_assistant` 落一条新 assistant 消息。

- 每轮（含首轮）各调用一次 `runtime.stream(...)` 并依序 `yield`（首轮由用户消息驱动，续跑轮见下）。
- 在非首轮时，构造续跑消息：不从用户消息，而是把 `continuation.md` 风格提示作为 **内部上下文** 注入（见 3.6），并以 `skip_user_append=True` 复用 history。
- 每轮 `done` 后：`if session goal.status == "active" and not stopped: continue`（再跑一轮）；否则跳出。
- `error` → 跳出（对标 codex `Blocked` / `UsageLimited` 停循环）。
- **输入框 Stop** → 客户端 abort / 后端强停，整个流与 loop 一起终止；goal 状态**不变（active）**，后续续跑需 kick（新消息 / resume / 重开会话）。
- **TodoBlock goal pause** → 下一轮 `done` 后 `_should_continue_goal` 读到非 active 而 break；**不 abort 当前轮**（当前轮正常跑完）。
- `approval_required` / `question_required`（HITL）→ **跳出循环但保留 interrupt checkpoint**（`main.py:2007` 现有逻辑），goal 保持 `active`；用户解决审批后由前端 resume 流恢复续跑（见 3.3.2）。
- 每轮结束做记账（3.5）。

> 复用现有 `skip_user_append` 路径（`main.py:1744`）：首轮由用户消息驱动；续跑轮 `skip_user_append=True`，由注入的 goal 上下文驱动。

**每轮必须是"新的一轮"，不能复用首轮的单轮态（三个强制点）：**

- **每轮新 assistant 消息 id**：`_persist_assistant`（`main.py:1780`）默认用 `request.assistant_message_id`（整条连接只有一个 id），续跑轮若沿用会把第 2..N 轮写成**重复 id** 的 assistant 消息，污染会话且前端对账错乱。**续跑轮必须传 `message_id=None`** 让后端生成新 id（或前端按轮传新 id）。
- **每轮 snapshot 按轮包裹**：`snapshot_manager.begin_turn / end_turn`（`main.py:1958/2014`）目前只在连接级各调一次。goal 多轮下必须**每轮 begin/end 一次**（绑定该轮的 `snapshot_user_message_id`），否则"编辑消息回滚该轮文件改动"会跨轮误伤。
- **每轮都是完整上下文**：续跑轮 `messages = [continuation_system] + history`，history 取最新会话历史（上一轮 assistant 已持久化），第 N 轮自然看到前 N-1 轮成果（对齐 codex rollout 语义）。

### 3.3.1 会话重开 / 前端刷新时恢复续跑（对标 codex `restore_inherited_goal_runtime`）

- 目标持久化在 `Session.goal`，但续跑循环只存在于活跃 `/chat/stream` 内。用户在目标执行中途刷新页面或重开会话会丢失续跑链。
- 解决：前端加载会话时拉 `GET /goal`；若返回 `active` 则在 TodoBlock 渲染 goal section，并**自动发一次 `skip_user_append=True` 的 `/chat/stream`** 重启续跑（复用 3.3 同一循环，不新增主链）。若状态为 `paused/complete/blocked/budget_limited/usage_limited` 则只渲染、不重启。以前端「当前无进行中 stream」为防重入闸门。

### 3.3.2 空闲会话启动与 HITL 恢复（两个必须显式触发的续跑入口）

- **`/goal set` / `/goal resume` 必须立即触发续跑流**：对标 codex `apply_external_goal_set` → `continue_if_idle()` 直接起一轮。前端 `/goal set`（或 `resume`）接口返回 `active` 后，**立即自动发一次 `skip_user_append=True` 的 `/chat/stream`**（复用 3.3 同一循环），否则空闲会话下设定目标后什么都不发生。3.3.1 的"加载会话恢复"只管刷新场景，两者是同一代码路径、同一防重入闸门。
- **HITL 后恢复续跑**：goal 运行中模型触发 `approval_required` / `question_required` → 后端跳出续跑循环但保留 interrupt checkpoint（goal 仍 `active`）；前端收到该事件后处于 `waiting` 态。用户解决审批后，前端对当前 `waiting` 消息发起 resume 流（复用现有 HITL 恢复逻辑）——**该 resume 流启动时重查 `GET /goal`，若仍 `active` 则回到 3.3 循环继续续跑**，不新增第二主链。
- **`/goal clear` / `pause` / `budget_limited` / `complete` / `blocked`**：状态离开 `active`，续跑流在下一个轮边界 break，且**该次 break 不重建续跑**（`pause` 不 abort 当前轮，仅影响下一轮是否续跑）。

## 3.4 模型工具 `update_goal` — `backend/coworker/agent/core.py` + `graph.py`

对齐 `spec.rs`：注册工具：

```
update_goal(status: "complete" | "blocked")
```

- 仅当 session 有 active goal 时可见（`build_workspace_tools` 按 session goal 开关）。
- 模型调用 → 持久化状态（`update_goal_status`）。
- **严禁**模型置 `paused` / `resume` / `budget`（由用户 / 系统控制）。
- **只读 `get_goal` 工具（必做，已拍板）**：模型可主动读取 objective / 预算 / 已用，不暴露变更（对齐 codex `get_goal`）。
- 提示词约束照搬 `update_goal` 的严格审计描述（完成须逐条需求审计；blocked 须连续 ≥3 轮同一阻塞）。

## 3.5 记账与兜底 — `runtime.py` + `sessions.py`

- 每轮结束用 `context_usage` 帧累加 `tokens_used`；用 wall-clock 差累加 `time_used_seconds` 到 `GoalState`（`account_goal_usage`）。
- `token_budget` 超 → 置 `budget_limited`，并**在下一轮构造 messages 时把 `budget_limit.md` 渲染结果作为 system 首位消息注入**（见 3.6 同一注入通道；codex 是经 `tool_finish` 钩子注入当前运行中 turn，cw 在轮边界注入，时机不同但等价），循环停。
- turn 抛错（非可重试）→ `blocked`；用量超限 → `usage_limited`。

### 3.5.1 状态范围界定（避免遗留半成品）

- `usage_limited`：cw 无 org / 账户级用量上限检测，本专项**不自动触发**，仅在 `GoalState` 枚举保留位以对齐 codex schema；未来接入用量上限再补触发器。
- `stalled`：cw 不单独建模「active 但无进展」；续跑中若模型连续多轮无工具调用 / 无文件改动，仍显示为 `active`。stall 检测列为后续增强，不在本专项范围。

## 3.6 续跑上下文注入 — 直接构造 messages（不扩展 steer inbox）

**实现方式（比对审计 §3.6 的简化）**：cw 每轮 `messages` 在 `event_stream` 内现造（`main.py:1744-1752`，`history` 只含 user/assistant）。因此**续跑轮直接把 continuation 拼进 messages 首位即可，无需走 `steer_inbox`**：

```python
messages = [{"role": "system", "content": render_goal_continuation(goal)}] + history
```

- `prepare_agent_messages`（`agent/core.py:956-965`）原生支持 `system` 角色；`NormalizeMessagesMiddleware`（`middleware.py:466`）只约束"非首位 system 降级为 human"，放首位恰好安全。
- 该 system 消息是**内部指令**：不在 session 落库、前端不渲染为用户泡泡（对齐 codex `InternalModelContextFragment`，渲染为 `<codex_internal_context source="goal">` 的语义）。
- **`steer_inbox` 保持只服务 interject**（运行中注入，`SteerInjectionMiddleware` 不改），goal 续跑（轮间注入）完全不碰 inbox——避免 HumanMessage 被前端误渲染为插话卡，也免去 `SteerEntry.kind` 新字段。

**每轮注入内容（按状态分支，渲染函数见 3.7；三份全部必做，已拍板）：**

- 续跑轮（goal active）→ `render_goal_continuation(goal)`（continuation.md）。
- 预算超限轮（goal budget_limited，仅注入一次）→ `render_budget_limit(goal)`（budget_limit.md）。
- `edit` 场景（objective 被改且当前轮未启动）→ `render_objective_updated(goal)`（objective_updated.md）；`/goal/edit` 已纳入本专项（§3.2），与 set/pause/resume/clear 一起交付。

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

- 新增 `goal` store（订阅后端 `goal_updated` SSE 事件），**严格会话隔离**：goal 状态按 session 存（`sessionGoals` 平行于 `sessionTodos`），TodoBlock 只渲染**当前活动会话**的 goal；其他会话的 goal 不显示、不影响本会话。
- **Goal 显示复用 TodoBlock**（`components/TodoBlock.tsx`，渲染位 `workspace-composer-slot`，App.tsx:3624）：goal section 展示 objective、状态标签（对齐 codex `goal_status_label`：active / paused / stalled / usage limited / limited by budget / complete）、已用时间、token 预算进度条，以及 **pause / resume / clear** 三个按钮（= `/goal pause|resume|clear`）。**不另设独立 GoalBar**。
  - TodoBlock 渲染条件改为 `(showTodoCard || queuedMessagesFor(sessionId).length > 0 || goal != null)`。
  - props 扩展：`goal: GoalState | null`、`onGoalPause / onGoalResume / onGoalClear`。
- 聊天输入框 `/` 命令卡加 `/goal` 子命令（set / pause / resume / clear），复用现有 skill 命令卡机制（实际入口在 `ChatInput.tsx` 的 `/` 卡片，`App.tsx` 持有权威状态）；**命令只作用于当前会话**。
- 自动续跑由后端驱动；前端在 goal 存在时显示「目标执行中」。

**两个独立控制（用户拍板，见 3.0）：**
- **输入框 Stop**（现有 `stopMessage`，App.tsx:1798）= 停当前任务：abort 本地流 + 调 `/sessions/{session_id}/stop` 强停；goal 保持 active（TodoBlock 仍显示 active），续跑需 kick。复用现有逻辑，零新前端代码。
- **TodoBlock goal pause/resume/clear** = 控制目标作用：不碰进行中的流。

**单流多轮的前端适配（不能"沿用现有 done 逻辑"，三个强制点）：**

- **每轮一个 assistant 气泡**：goal 续跑流会在同一条 SSE 连接上连续发出 N 个 `done` 事件（每轮一个）。现有 `sendMessage`（`App.tsx:1606`）把第一个 `done` 当终态（commit done / 播声音 / 清 todos / 对账），第 2..N 轮在 UI 上不可见。适配：**每个 `done` 都是一次独立 commit** —— 首轮复用现有 `assistant_message_id`，后续轮**新建 assistant 气泡**（新 id 来自后端 `done.session_id` + 按轮生成的前端气泡 id，或后端在 `done` 帧回带该轮 message id），stream 未终态前不触发 stream-settle 收尾。
- **前端不把 goal 流"一次收尾"**：`settleAssistantMessage` / 队列自动发送 / `isStreamStale` 都要感知"goal 流 = 多段 done，未收到终态事件前仍在推进"；提前收尾 = 输入框 Stop（停任务，goal 保持 active）。对账（settle）只在流的真正终态（最终 `done` 后或 `error` / `worker_stream_end`）执行。
- **`/goal set|resume` 触发续跑流**：接口返回 `active` 后前端立即自动发一次 `skip_user_append=True` 的 `/chat/stream`（3.3.2），与"会话加载恢复"同一路径。
- **HITL `waiting` 保持现状**：`approval_required` / `question_required` 时消息进 `waiting`；用户解决后 resume 流重查 goal 续跑（3.3.2）。

---

# 四、最佳落手点与实施顺序

| 阶段 | 文件 | 内容 |
|---|---|---|
| M1 数据+API | `sessions.py`, `main.py`, `electron/{main,preload}.js`, `frontend/src/electron.d.ts`, `services/chatService.ts` | `GoalState` + `/goal/*` 端点 + `goal_updated` 广播 + **Electron IPC 桥接层（3.2.1）** |
| M2 续跑循环 | `main.py` `event_stream` | **单一生成器内层多轮循环**（3.3 落地方式）+ `skip_user_append` + 退出条件 + **每轮新 assistant id + 每轮 snapshot 包裹 + 每轮终态旗标重置 + HITL/空闲启动/恢复入口（3.3/3.3.1/3.3.2）** |
| M3 上下文注入 | `main.py`（续跑轮 messages 构造）+ `goal_prompts.py` | **直接拼 `system` 首位消息**（continuation / budget_limit / objective_updated），不改 `steer.py` / `middleware.py` |
| M4 模型工具+记账 | `core.py`, `graph.py`, `runtime.py` | `update_goal`(complete/blocked) + **只读 `get_goal`** + token/time 记账 + 预算/错误兜底 |
| M5 提示词+UI | `goal_prompts.py`, 前端 TodoBlock goal section + `/goal` 命令 | 移植三份模板 + **TodoBlock goal section + 单流多轮气泡/收尾适配（3.8）** |

**最小可跑闭环**：M1（含 IPC 桥接层）+ M2 + M3 + `goal_prompts.py` 即可让「`/goal X` → 逐轮自动推进 → TodoBlock goal section 显示进度 → Stop 停任务 / pause 停目标」跑通（含空闲启动与 HITL 恢复）；M4 / M5 补完成审计、get_goal 与 UI 精致度。M2 必须同时带上「单一生成器内层循环 / 每轮新 id / snapshot 包裹 / 终态旗标重置 / HITL 恢复」，否则多轮会写脏会话。

---

# 五、行为对齐校验清单（对照 codex）

> 勾选依据：后端脚本化 fake runtime 端到端验证 + `/goal/*` 端点 TestClient + 前端 tsc/build + **vLLM 真机对 ailicode 项目真实任务验收（2026-08-25）**。

- [x] `/goal <obj>` 后目标钉窗显示 active，无需用户再输入即自动开下一轮（**空闲会话下 `/goal set` 即自动起续跑流，无需刷新**）。
- [x] 每轮结束若仍 active 则续跑；模型不得自行缩小目标（continuation.md 约束）。（真机：复杂目标跨 8 轮持续推进）
- [x] 模型只能 `update_goal(complete|blocked)`；暂停 / 恢复 / 清理由用户命令控制。
- [x] `complete` 须经逐条需求审计；`blocked` 须连续 ≥3 轮同一阻塞。（真机：逐条验证交付物后 `update_goal(complete)` 成功）
- [x] 超 token 预算 → `budget_limited` 停 + 注入预算提示；turn 报错 → `blocked` 停。
- [x] `pause` / `resume` 正确挂起 / 恢复续跑；`clear` 立即终止循环并清状态条。
- [x] **双控制语义**：输入框 Stop 停当前任务、goal 保持 active、续跑需 kick（新消息 / resume / 重开会话）；TodoBlock pause 停目标作用（当前轮跑完、下一轮边界停）、不 abort 进行中的任务。
- [x] **goal 严格会话隔离**：TodoBlock 只显示当前会话的 goal；跨会话不显示、不影响。
- [x] **多轮会话无脏数据**：每轮 assistant 消息 id 唯一、每轮 snapshot 独立回滚、"编辑回滚该轮改动"不跨轮误伤。
- [x] **HITL 恢复**：goal 轮触发 approval/question → 消息 `waiting` → 用户解决后 resume 流重查 goal 仍 active 则继续续跑。
- [x] **会话锁语义明确**：goal 运行期普通消息只能排队、编辑/重生成被 409 拒、interject / 输入框 Stop / TodoBlock goal pause 可介入——与文档声明一致。
- [x] **续跑注入为内部上下文**：continuation/budget 提示是 system 内部指令，不落库、前端不渲染为用户泡泡。

---

# 附：codex 关键代码索引

| 关注点 | 文件 |
|---|---|
| 状态枚举 / 数据模型 | `codex-rs/state/src/model/thread_goal.rs` |
| TUI 命令定义 | `codex-rs/tui/src/goal_display.rs:5` |
| TUI set/get/clear | `codex-rs/tui/src/app_server_session.rs:1192-1244` |
| 请求处理器 / dispatch | `codex-rs/app-server/src/request_processors/thread_goal_processor.rs` / `message_processor.rs:1187` |
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
| 对话主流程 | `backend/main.py` `chat_stream` / `event_stream`（`main.py:1679` / `:1832`）；续跑轮 messages 构造（`:1744`） |
| 插话机制（仅 interject 用） | `backend/coworker/steer.py` + `agent/middleware.py:498,548,582` |
| 续跑注入（直接构造，非 steer） | `backend/main.py` 续跑轮 + `backend/coworker/goal_prompts.py` |
| 工具注册 | `backend/coworker/agent/core.py` + `graph.py` |
| 运行时 | `backend/coworker/agent/runtime.py` |
| Electron IPC 桥接 | `electron/main.js` + `electron/preload.js` + `frontend/src/electron.d.ts` + `services/chatService.ts` |
| 前端状态/命令 | `frontend/src/App.tsx`（`:858` interject 续跑，`:1606` 主流 done 处理，`:1798` stopMessage，`:3624` TodoBlock 渲染位）、`frontend/src/components/TodoBlock.tsx`（goal section）、`frontend/src/components/ChatInput.tsx`（`/` 命令卡） |

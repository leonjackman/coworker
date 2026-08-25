# cw `/goal` 目标能力开发 Task List

更新时间：2026-08-25
状态：执行中
说明：本清单对应 [cw `/goal` 目标能力开发方案](./goal-capability-plan.md)，用于把该设计文档拆成按顺序可执行的专项清单，使 cw 拥有与 codex `thread goal`（见 `codex-rs/ext/goal`）一致的实现方式与行为模式：`/goal` 设定持久目标、目标钉窗、agent 在目标未达成前无用户输入地自动连续推进多轮、模型通过工具声明完成/受阻、状态离开 active 即停止续跑。

## 目标

- 建立 cw 首个**持久、绑定会话（session）的目标（goal）** 正式结构：数据模型、状态机、API。
- 建立**后端内部多轮续跑循环**，使目标 active 时一轮结束自动开启下一轮，无用户输入。
- 建立 **goal 续跑上下文注入**机制（复用并扩展 `steer.py` 插话 inbox），每轮注入 objective + 预算 + 完成/受阻审计提示。
- 提供**模型侧 `manage_goal` 工具**，模型只能声明 `complete` / `blocked`，暂停/恢复/清理由用户命令控制。
- 提供 **token/time 记账与兜底**：超预算 → `budget_limited`、turn 报错 → `blocked`、用量超限 → `usage_limited`，均停止续跑。
- 提供**钉窗 UI（GoalBar）** 与 `/goal` 聊天命令，实时反映目标状态、进度、耗时。
- 移植 codex `continuation.md` 严格审计提示词，保证模型不缩小目标、不伪完成。

## 非目标

- 不新增第二对话主链 / 第二 submit path（续跑必须复用现有 `/chat/stream` + `skip_user_append`）。
- 不重写已冻结的 LangGraph 主链架构，只在 `event_stream` 外套多轮循环、在中间件层扩展注入。
- 不为目标模式新增独立的前端连接 / 轮询通道（续跑在单次 SSE 连接内完成）。
- 不做把"环境事件/系统事件"伪装成用户 turn 的设计（goal 上下文是内部指令，不是用户消息泡泡）。
- 不实现目标的跨会话持久化之外的协作/团队编排（与现有 delegation/team 工具解耦，仅 goal 维度）。
- 本专项不要求失败测试（按用户明确指示豁免，见执行规则）。

## 完成标准

- `owner 边界`：`/goal/*` 端点与 `event_stream` 续跑逻辑集中在 `main.py` + `sessions.py`，goal 状态真源只在 `Session.goal`，无第二真源。
- `主链边界`：单轮 Q&A 行为完全不变；仅当 session 存在 `active` goal 时触发续跑，Stop 一次中断整条续跑链。
- `观测面`：GoalBar 实时显示 objective / 状态 / 已用时间 / token 预算进度；`goal_updated` 事件驱动刷新。
- `行为对齐`：通过第五章节「行为对齐校验清单」全部勾选项；模型无法置 paused/resume/budget，只能 complete/blocked。
- `回归不退化`：现有 `/chat/stream`、`/chat/interject`、普通一问一答、steer 插话路径无回归。
- `文档收口`：本 task list、设计文档、前端命令卡、相关 docs-facts 已同步。

## 输入真源

后端（必读 / 必改）：
- `backend/coworker/sessions.py` — `Session` 模型与 `SessionStore`（新增 `GoalState` 与 CRUD）。
- `backend/main.py` — `chat_stream`（`:1679`）、`event_stream` 内 `_handle_event` 的 `done` 处理（`:1832`）、`_on_end`/`_on_error`、现有 `skip_user_append` 路径（`:1744`）、`/chat/interject`（`:2040`）。
- `backend/coworker/steer.py` — `SteerEntry`（`:27`）、`SteerInbox`（`:40`）、`steer_inbox` 单例（`:76`）。
- `backend/coworker/agent/middleware.py` — `SteerInjectionMiddleware`（`:498`）、`abefore_model`（`:582`）、`take_all` 消费（`:548`）。
- `backend/coworker/agent/core.py` — 工具 schema 定义（如 `RunCommandArgs` 等）、`build_workspace_tools` / 工具可见性控制。
- `backend/coworker/agent/graph.py` — 中间件注册（`:760`）、`build_workspace_tools`（`:73` 引用）。
- `backend/coworker/agent/runtime.py` — `stream`（`:891`）与 `context_usage` 帧产出位置。
- `backend/coworker/prompts.py` 或新建 `backend/coworker/goal_prompts.py` — 续跑提示词模板。

前端（必读 / 必改）：
- `frontend/src/App.tsx` — 命令卡（`:600`）、`done` 处理（`:712`）、interject 自动续跑（`:858`）、消息可见性过滤（`:644`）。
- `frontend/src/components/ChatInput.tsx` — 斜杠命令卡（如存在；否则在 `App.tsx` 内联）。
- 任一状态管理入口（store / context）— 新增 `goal` 状态与 `goal_updated` 订阅。

参照真源（codex，只读）：
- `codex-rs/ext/goal/src/{extension,runtime,steering,tool,spec,accounting}.rs`
- `codex-rs/state/src/model/thread_goal.rs`
- `codex-rs/ext/goal/templates/goals/{continuation,budget_limit,objective_updated}.md`
- `codex-rs/app-server/src/request_processors/thread_goal_processor.rs`

## 执行规则

- 每轮只允许推进本清单中最靠前的未完成 Step；完成条件全部满足才勾选。
- 先读真实代码、真实入口、真实主链，再动手；不允许凭印象改。
- 续跑循环必须复用 `/chat/stream` + `skip_user_append`，不允许新增第二主链。
- 每完成一个 Step，补该 Step 的直接相关手动验证 / 回归（见完成标准），不允许只写代码不验证就勾选。
- 阶段结论变化必须同步：设计文档、本 task list、前端命令卡、相关 docs-facts。
- **测试豁免**：按用户明确指示，本专项**不要求失败测试（failure-first）**；以「实现后补直接相关手动链路验证 + 回归」替代，但禁止把未验证代码勾为完成。
- 不允许把一次性调试日志、长命令流水堆进本清单。

---

## Phase 1：数据模型与 API contract 建立

- [ ] Step 1.1：在 `sessions.py` 建立 `GoalState` 正式模型与状态机
  - [ ] 预期目标：`Session` 拥有唯一 goal 真源，状态机与 codex `ThreadGoalStatus` 对齐。
  - [ ] 行为：goal 状态只能取 `active | paused | blocked | complete | budget_limited | usage_limited`。
  - [ ] 改动点：`backend/coworker/sessions.py`
    - 新增 `class GoalState(BaseModel)`，字段：`objective:str`、`status:Literal[...]`、`token_budget:int|None=None`、`tokens_used:int=0`、`time_used_seconds:int=0`、`created_at:int=0`、`updated_at:int=0`。
    - 在 `Session` 增加 `goal: GoalState | None = None`（默认 `None` 表示无目标）。
  - [ ] 组织：模型放 `sessions.py` 顶部与其他 `BaseModel` 同区；状态字面量与 codex 一致（小写下划线）。
- [ ] Step 1.2：在 `SessionStore` 增加 goal 读写 API
  - [ ] 预期目标：为后续端点与续跑循环提供稳定 CRUD。
  - [ ] 行为：get/set/clear/update_status/account_usage 全部以 `session_id` 定位；并发写用既有 store 锁。
  - [ ] 改动点：`backend/coworker/sessions.py` 的 `SessionStore`
    - `get_goal(session_id) -> GoalState | None`
    - `set_goal(session_id, objective, token_budget) -> GoalState`（新建或覆盖重建，状态置 `active`，刷新时间戳）
    - `clear_goal(session_id) -> bool`
    - `update_goal_status(session_id, status) -> GoalState | None`（同时刷新 `updated_at`）
    - `account_goal_usage(session_id, token_delta, time_delta_seconds) -> GoalState | None`（累加 `tokens_used` / `time_used_seconds`；若 `token_budget` 非 None 且 `tokens_used >= token_budget` 自动把状态置 `budget_limited`）
  - [ ] 组织：方法与现有 `append_message`/`update_todos` 同风格，异常安全（store 写失败不影响主链）。
- [ ] Step 1.3：在 `main.py` 新增 `/goal/*` 端点与 `goal_updated` 广播
  - [ ] 预期目标：前端与内部可通过 HTTP 设定/查询/控制目标，状态变化实时推给前端。
  - [ ] 行为：`/goal/set` 置 active 并广播；`pause`/`resume` 切 paused/active；`clear` 清并广播清除；`get` 返回当前 goal。
  - [ ] 改动点：`backend/main.py`
    - 新增 Pydantic 请求体：`GoalSetRequest{session_id, objective, token_budget?}`、`GoalControlRequest{session_id}`。
    - `POST /goal/set`：校验 `session_id` 存在 → `session_store.set_goal(...)` → 调用 `_emit_goal_updated(session_id, goal)`。
    - `POST /goal/pause`：`update_goal_status(session_id, "paused")`（仅当当前 active/budget_limited 才允许，避免覆盖 complete/blocked）。
    - `POST /goal/resume`：`update_goal_status(session_id, "active")`（仅当当前 paused 才允许）。
    - `POST /goal/clear`：`clear_goal(...)` → 广播 `goal_cleared`。
    - `POST /goal/edit`：`update_goal_objective(session_id, objective)`（仅当当前 active 时允许，重建 objective 并刷新时间戳，状态保持 active）→ 广播 `goal_updated`。
    - `GET /goal?session_id=`：返回 `GoalState | None`。
  - [ ] **交付口径（goal_updated 的双通道）**：
    - **空闲态更新**（用户在无流式运行时点 pause/resume/clear/edit）：端点**直接在 HTTP 响应体返回更新后的 `GoalState`**，前端以响应为准刷新 GoalBar（不依赖 SSE 推送，因此时无活跃订阅）。
    - **流式态更新**（模型在运行中调 `manage_goal` 改变状态）：通过既有 SSE 事件总线增量推送 `goal_updated{goal}`，前端实时刷新。
    - 两通道都落到同一前端 `goal` 状态，避免真源漂移。
  - [ ] 组织：端点与 `chat_stream` 同文件、同鉴权风格；广播函数集中一处便于 Phase 5 前端订阅。

## Phase 2：后端内部多轮续跑循环

- [ ] Step 2.1：在 `event_stream` 外套多轮 while 循环
  - [ ] 预期目标：目标 active 时，一轮 `done` 后自动开启下一轮，无需前端再请求。
  - [ ] 行为：首轮走正常用户消息路径；续跑轮走 `skip_user_append=True` 且注入 goal 上下文（Phase 3）；`done` 且仍 active 则 `continue`；`error`/中断/Stop/非 active 则跳出。
  - [ ] 改动点：`backend/main.py` 的 `chat_stream` 内层 `event_stream()`
    - 引入外层 `while True`（或 `for _ in itertools.count()`），用 `loop_index` 区分首轮/续跑轮。
    - 首轮：`messages = history + [user_message]`（保持现状）。
    - 续跑轮：`skip_user_append=True`，`messages = history`（history 已由首轮写入，后续轮直接复用最新 session 历史），并在调用 `runtime.stream` 前通过 `steer_inbox.push(...)` 注入 goal 续跑上下文（Phase 3 落点）。
    - `done` 处理（`_handle_event` 内 `etype=="done"`，`:1832`）：执行完现有持久化后，读 `session_store.get_goal(session_id)`；若 `goal and goal.status == "active"` 且未收到 Stop/中断信号 → 置 `continue_loop=True`；否则 `break`。
    - `_on_error` / `approval_required` / `question_required` / 客户端断开 → `break`（对标 codex `Blocked`/`UsageLimited` 停循环）。
  - [ ] 组织：循环变量与退出标志集中声明；每轮结束做 `account_goal_usage`（Phase 4 接入点）；退出前发一次最终 `goal_updated` 让前端收口。
- [ ] Step 2.2：接入 Stop 与并发守卫
  - [ ] 预期目标：Stop 一次中断整条续跑链，不留孤儿轮；与现有 `_guard_session_not_streaming` 不冲突。
  - [ ] 行为：Stop 触发 `_on_error`/取消，循环在下一轮判断点 `break`；不允许续跑轮绕过 Stop。
  - [ ] 改动点：`backend/main.py` 复用现有 `terminal_sent`/`interrupt_emitted` 标志与客户端断开检测；续跑 `continue` 条件增加「未中断」闸门。
  - [ ] 组织：续跑判断条件写成单一只读函数 `_should_continue_goal(session_id, stopped)`，便于回归与阅读。
- [ ] Step 2.3：会话重开 / 前端刷新时恢复 goal 续跑（对标 codex `restore_inherited_goal_runtime`）
  - [ ] 预期目标：用户在目标执行中途刷新页面或重开会话，goal 不丢失且能继续推进。
  - [ ] 行为：
    - 前端加载会话时拉取 `GET /goal`，若返回 `active` 目标则渲染 GoalBar，并**自动发一次 `/chat/stream`（skip_user_append=True，无用户消息）** 以重启续跑循环。
    - 若 goal 状态是 `paused` / `complete` / `blocked` / `budget_limited` / `usage_limited`，只渲染、不自动重启。
  - [ ] 改动点：
    - `backend/main.py`：`GET /goal` 已提供；`chat_stream` 现有续跑循环可承接「无用户消息 + skip_user_append」的重启请求（首轮 `user_message=None` 路径已存在 `:1744`）。
    - 前端会话加载逻辑（`App.tsx` 或会话 store）：载入后若 `goal.status == "active"`，自动触发一次续跑 `chat_stream`（复用现有发送函数，仅置 `skip_user_append`）。
  - [ ] 组织：重启请求与普通续跑轮走同一代码路径，不新增第二主链；避免重复重启（以「当前无进行中 stream」为前置闸门）。

## Phase 3：续跑上下文注入（扩展 steer inbox）

- [ ] Step 3.1：扩展 `SteerEntry` 支持 `kind="goal"`
  - [ ] 预期目标：goal 续跑提示作为**内部指令块**注入，不与用户插话混淆，且前端不渲染为用户泡泡。
  - [ ] 行为：goal 类 entry 在 `abefore_model` 被折入下一轮模型请求；`kind="user"` 保持现有插话行为。
  - [ ] 改动点：
    - `backend/coworker/steer.py`：`SteerEntry` 增加 `kind: str = "user"` 字段；`SteerInbox` 可选按 kind 读取（或 `take_all` 返回后由中间件分类）。
    - `backend/coworker/agent/middleware.py` 的 `SteerInjectionMiddleware.abefore_model`（`:582`）：区分 `kind`；`goal` 类渲染为带内部标记的系统/指令块（如前缀 `【目标续跑上下文，非用户输入】`），`user` 类保持 `HumanMessage` 插话。
  - [ ] 组织：渲染逻辑与现有 steer 渲染同函数分支；内部标记常量集中定义，供前端过滤（Phase 5）。
- [ ] Step 3.2：续跑轮在 `event_stream` 注入 goal 上下文
  - [ ] 预期目标：每轮起调前把 `continuation.md` 渲染结果推入 inbox，下一轮自动生效。
  - [ ] 行为：仅续跑轮注入；goal 状态非 active 不注入。
  - [ ] 改动点：`backend/main.py` 续跑轮起点调用 `steer_inbox.push(session_id, SteerEntry(kind="goal", content=render_goal_continuation(goal), ...))`（渲染函数见 Phase 6）。
  - [ ] 组织：渲染调用放在 Phase 6 的 `goal_prompts.py`，本 Step 只接调用点。

## Phase 4：模型工具与记账兜底

- [ ] Step 4.1：注册 `manage_goal` 模型工具
  - [ ] 预期目标：模型在目标模式下可声明完成/受阻；无权改 paused/resume/budget。
  - [ ] 行为：仅当 session 有 active goal 时工具可见；调用后持久化状态，续跑循环据此停止（complete/blocked）。
  - [ ] 改动点：
    - `backend/coworker/agent/core.py`：新增 `ManageGoalArgs(BaseModel)`（`status: Literal["complete","blocked"]`）与工具 schema；在 `build_workspace_tools` 按 `session_id` 的 goal 状态决定是否加入工具集。
    - 工具执行体：调用 `session_store.update_goal_status(session_id, status)` 并 `_emit_goal_updated(...)`；遇非 active 时报错提示「当前没有 active 目标」。
    - `backend/coworker/agent/graph.py`：确保工具在 graph 工具清单中注册（与现有工具同机制）。
  - [ ] **可选 parity：注册 `get_goal` 只读工具**（对标 codex `get_goal`），让模型在运行中可主动读取当前 objective / 预算 / 已用，减少依赖注入提示。仅暴露查询，不暴露变更。
  - [ ] 组织：工具描述照搬 codex `update_goal` 严格约束（完成须逐条审计；blocked 须连续 ≥3 轮同一阻塞），见 Phase 6 提示词。
- [ ] Step 4.2：续跑轮记账与兜底
  - [ ] 预期目标：goal 维度 token/time 准确累加，预算/错误自动停循环。
  - [ ] 行为：每轮结束用 `context_usage` 帧累加 `tokens_used`；wall-clock 差累加 `time_used_seconds`；超预算自动 `budget_limited` 并注入预算提示；turn 报错（非可重试）置 `blocked`；用量超限置 `usage_limited`。
  - [ ] 改动点：
    - `backend/main.py` `event_stream` 每轮 `done` 后调用 `session_store.account_goal_usage(session_id, token_delta, time_delta)`（token 取自 `context_usage.used_tokens`，time 取本轮起止差）。
    - 续跑轮起点若检测到 `goal.status == "budget_limited"` → 注入 `budget_limit` 提示（Phase 6 模板）并经 SteerInjectionMiddleware 折入。
    - turn 抛错路径（`_on_error`）若当前 goal active → `update_goal_status(session_id, "blocked")`（对标 codex `TurnError`→Blocked）。
  - [ ] **`usage_limited` / `stalled` 状态范围界定（避免遗留半成品）**：
    - `usage_limited`：cw 无 org/账户级用量上限检测机制，本专项**不自动触发**该状态（仅在数据模型保留枚举位以对齐 codex schema）；若未来接入用量上限再补触发器。
    - `stalled`：cw 不单独建模「active 但无进展」；续跑中若模型连续多轮无任何工具调用/文件改动，仍显示为 `active`。如需 stall 检测，列为后续增强，不在本专项范围。
  - [ ] 组织：记账写入集中在 Phase 2 的续跑收尾处，避免分散；预算提示渲染放 `goal_prompts.py`。

## Phase 5：钉窗 UI 与 `/goal` 命令

- [ ] Step 5.1：前端 `goal` 状态与事件订阅
  - [ ] 预期目标：前端持有 goal 真源镜像，实时响应 `goal_updated` / `goal_cleared`。
  - [ ] 行为：收到事件更新 GoalBar；`goal_cleared` 清空。
  - [ ] 改动点：前端状态管理入口新增 `goal: GoalState | null`；在既有 SSE 事件分发处订阅 `goal_updated`/`goal_cleared`（与现有事件总线同机制，不新建通道）。
  - [ ] 组织：状态形态与后端 `GoalState` 字段对齐。
- [ ] Step 5.2：GoalBar 组件（钉窗）
  - [ ] 预期目标：常驻显示目标 objective、状态、已用时间、token 预算进度。
  - [ ] 行为：状态标签对齐 codex `goal_status_label`：`active`→进行中、`paused`→已暂停、`blocked`→受阻、`complete`→已完成、`budget_limited`→预算受限、`usage_limited`→用量受限。
  - [ ] 改动点：新增 `frontend/src/components/GoalBar.tsx`（或在 `App.tsx` 内联），于聊天主区顶部渲染；时间格式化对齐 codex `format_goal_elapsed_seconds`；token 进度条按 `tokens_used/token_budget`。
  - [ ] 组织：组件样式沿用现有 capsule/状态色板（`components/ui/type-capsule.tsx` 风格）。
- [ ] Step 5.3：`/goal` 聊天命令
  - [ ] 预期目标：用户在输入框 `/goal <目标>` 即可设定并激活；`/goal pause|resume|clear` 控制。
  - [ ] 行为：命令解析后调 `Phase 1` 的 `/goal/*` 端点；`/goal clear` 立即终止续跑并清 GoalBar。
  - [ ] 改动点：`frontend/src/App.tsx`（命令卡 `:600`）与 `ChatInput.tsx` 斜杠菜单：新增 `/goal` 子命令集；复用现有 skill 命令卡机制渲染。
  - [ ] 组织：命令与现有 `/` 菜单同入口，不新建交互范式；解析逻辑与既有命令分发一致。

## Phase 6：续跑提示词移植与行为对齐

- [ ] Step 6.1：建立 `goal_prompts.py` 并移植 `continuation.md`
  - [ ] 预期目标：续跑上下文含 objective、token 预算、完成审计、blocked 审计、fidelity 约束，与 codex 行为一致。
  - [ ] 行为：模板渲染 `objective / tokens_used / token_budget / remaining_tokens`；模型被告知「目标跨轮持久、不得缩小目标、须逐条需求审计后才 complete」。
  - [ ] 改动点：新建 `backend/coworker/goal_prompts.py`，提供：
    - `render_goal_continuation(goal) -> str`（移植 `codex-rs/ext/goal/templates/goals/continuation.md` 全文要点）
    - `render_budget_limit(goal) -> str`（移植 `budget_limit.md`）
    - `render_objective_updated(goal) -> str`（移植 `objective_updated.md`，用于 edit 场景）
  - [ ] 组织：模板用 Python 字符串/`str.format` 渲染；常量集中，便于回归。
- [ ] Step 6.2：`manage_goal` 工具描述移植严格约束
  - [ ] 预期目标：模型侧约束与 codex `update_goal` 完全一致。
  - [ ] 行为：complete 须逐条需求审计；blocked 须连续 ≥3 轮同一阻塞；禁止模型置 paused/resume/budget。
  - [ ] 改动点：`backend/coworker/agent/core.py` 工具 description 直接采用 codex `spec.rs` 中 `update_goal` 的英文/中文约束文本。
  - [ ] 组织：描述文本与 Phase 6.1 提示词口径统一，避免矛盾。

## Phase 7：回归、验收与文档收口

- [ ] Step 7.1：手动链路验证（替代失败测试，按用户豁免）
  - [ ] 预期目标：核心行为可端到端复现。
  - [ ] 行为：
    - `/goal 写一个计算器 CLI` → GoalBar 显示 active，自动连跑多轮直至 `manage_goal(complete)`；中途 Stop 立即终止。
    - 超小 `token_budget` → 自动 `budget_limited` 停 + 预算提示。
    - 人为制造持续报错 → `blocked` 停。
    - `/goal pause` 挂起续跑；`/goal resume` 恢复；`/goal clear` 清空。
    - 普通一问一答（无 goal）行为完全不变；`/chat/interject` 插话不变。
  - [ ] 改动点：无代码改动，仅验证；记录验证结果到本 Step。
- [ ] Step 7.2：完成第五章节「行为对齐校验清单」
  - [ ] 预期目标：全部勾选项通过。
  - [ ] 改动点：逐条核对并勾选 `goal-capability-plan.md` 第五章；未过项回到对应 Phase 修复。
- [ ] Step 7.3：文档与真源同步
  - [ ] 预期目标：设计文档、本 task list、前端命令卡、docs-facts 一致。
  - [ ] 改动点：
    - 更新 `goal-capability-plan.md` 与本文档的实施结论。
    - 若新增前端命令卡/GoalBar，在 README 或命令清单补充说明。
    - 顶部「状态」由「执行中」视情况转「已完成冻结，不再作为当前前线执行面」。
  - [ ] 组织：所有同步在同一轮内完成，避免真源漂移。

## 附：行为对齐校验清单（Phase 7.2 勾选源）

- [ ] `/goal <obj>` 后目标钉窗显示 active，无需用户再输入即自动开下一轮。
- [ ] 每轮结束若仍 active 则续跑；模型不得自行缩小目标（continuation 约束）。
- [ ] 模型只能 `manage_goal(complete|blocked)`；暂停 / 恢复 / 清理由用户命令控制。
- [ ] `complete` 须经逐条需求审计；`blocked` 须连续 ≥3 轮同一阻塞。
- [ ] 超 token 预算 → `budget_limited` 停 + 注入预算提示；turn 报错 → `blocked` 停。
- [ ] `pause` / `resume` 正确挂起 / 恢复续跑；`clear` 立即终止循环并清状态条。
- [ ] Stop 中断当前轮且不再自动续跑。

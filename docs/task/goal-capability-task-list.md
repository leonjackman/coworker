# cw `/goal` 目标能力开发 Task List

更新时间：2026-08-25
状态：**实施完成**（M1–M6 全部落地并通过后端循环自动化验证 + 前端 tsc/build；待真实 LLM 端到端回归）
说明：本清单对应 [cw `/goal` 目标能力开发方案](./goal-capability-plan.md)，用于把该设计文档拆成按顺序可执行的专项清单，使 cw 拥有与 codex `thread goal`（见 `codex-rs/ext/goal`）一致的实现方式与行为模式：`/goal` 设定持久目标、目标钉窗、agent 在目标未达成前无用户输入地自动连续推进多轮、模型通过工具声明完成/受阻、状态离开 active 即停止续跑。

> **实施完成说明（2026-08-25，真实 LLM 验收通过）**：M1（GoalState + `/goal/*` 端点 + Electron IPC）、M2（单一生成器内层多轮续跑循环）、M3（system 首位注入 continuation/budget_limit/objective_updated）、M4（`update_goal`/`get_goal` 工具 + 记账兜底）、M5（TodoBlock goal section + `/goal` 命令 + 单流多轮前端适配）、M6（`goal_prompts.py`）全部落地。除脚本化 fake runtime 的多轮循环验证与 `/goal/*` 端点 TestClient 验证外，已于 2026-08-25 用 **vLLM（qwen3-coder-next）+ ego-browser 真机对 ailicode 项目做了真实任务验收**：
> - `/goal` 命令卡为 **sys** 类型；`/goal <objective>` 输入保留 objective（修复 command-card auto-commit 吞掉 `/goal ` 前缀导致「编辑完发送没内容」的 bug）；
> - 复杂文档目标（8 步交付）**无用户输入自动连跑 8 轮**（assistant 消息 id 每轮唯一 `goal-round-*`），真实产出 `docs/{architecture,getting-started,contributing}.md` 并改 README，最终 `update_goal(complete)` 成功（工具名与 phase-gate allowlist 已修正为 `update_goal`）——goal 状态 active→complete，tokens 累计 165k；
> - 运行中暂停 → 已暂停 / 下一轮边界停；恢复 → 继续；清除 → 目标删除、TodoBlock 状态条消失；
> - 验证后已清理 ailicode 仓库的测试产物。
## 目标

- 建立 cw 首个**持久、绑定会话（session）的目标（goal）** 正式结构：数据模型、状态机、API。
- 建立**后端内部多轮续跑循环**，使目标 active 时一轮结束自动开启下一轮，无用户输入。
- 建立 **goal 续跑上下文注入**机制（复用并扩展 `steer.py` 插话 inbox），每轮注入 objective + 预算 + 完成/受阻审计提示。
- 提供**模型侧 `update_goal` 工具**，模型只能声明 `complete` / `blocked`，暂停/恢复/清理由用户命令控制。
- 提供 **token/time 记账与兜底**：超预算 → `budget_limited`、turn 报错 → `blocked`、用量超限 → `usage_limited`，均停止续跑。
- 提供**钉窗 UI（TodoBlock goal section）** 与 `/goal` 聊天命令，实时反映目标状态、进度、耗时。
- 移植 codex `continuation.md` 严格审计提示词，保证模型不缩小目标、不伪完成。

## 非目标

- 不新增第二对话主链 / 第二 submit path（续跑必须复用现有 `/chat/stream` + `skip_user_append`）。
- 不重写已冻结的 LangGraph 主链架构，只在 `event_stream` 外套多轮循环、在轮边界直接构造续跑消息。
- 不为目标模式新增独立的前端连接 / 轮询通道（续跑在单次 SSE 连接内完成）。
- 不做把"环境事件/系统事件"伪装成用户 turn 的设计（goal 上下文是内部指令，不是用户消息泡泡）。
- **不扩展 `steer.py` / `SteerInjectionMiddleware`**：goal 续跑注入用「续跑轮直接构造 `system` 首位消息」，`steer_inbox` 只保留给 interject（运行中注入）。
- 不实现目标的跨会话持久化之外的协作/团队编排（与现有 delegation/team 工具解耦，仅 goal 维度）。
- **不设独立 GoalBar**：goal 显示复用 TodoBlock（goal section，App.tsx:3624 渲染位），不另建新组件/新位置。
- **不做"轮边界插入普通消息"**：goal 运行期普通消息只能排队、编辑/重生成被 409 拒（会话锁语义，见设计文档 §3.0）；轮边界插入列为后续增强。
- **输入框 Stop 不改 goal 状态**（选项 A）：Stop 只停当前任务，goal 保持 active，续跑需 kick；停目标作用走 TodoBlock 的 pause（见设计文档 §3.0）。
- 本专项不要求失败测试（按用户明确指示豁免，见执行规则）。

## 完成标准

- `owner 边界`：`/goal/*` 端点与 `event_stream` 续跑逻辑集中在 `main.py` + `sessions.py`，goal 状态真源只在 `Session.goal`，无第二真源；**前端经 Electron IPC 桥接层调 `/goal/*`（`preload.js` + `main.js` + `electron.d.ts` + `chatService.ts`）**。
- `主链边界`：单轮 Q&A 行为完全不变；仅当 session 存在 `active` goal 时触发续跑；输入框 Stop 停任务（goal 保持 active、续跑需 kick），TodoBlock pause 停目标作用。
- `观测面`：TodoBlock 内 goal section 实时显示 objective / 状态 / 已用时间 / token 预算进度 + pause/resume/clear；`goal_updated` 事件驱动刷新；**严格会话隔离（只显示当前会话 goal）**。
- `行为对齐`：通过第五章节「行为对齐校验清单」全部勾选项；模型无法置 paused/resume/budget，只能 complete/blocked；`get_goal` 只读。
- **`多轮无脏数据`**：单一生成器内层循环（单条 SSE 连接）；每轮 assistant 消息 id 唯一、每轮 snapshot 独立 begin/end、每轮终态旗标重置（"编辑回滚该轮改动"不跨轮误伤）；HITL 后 resume 流重查 goal 续跑不丢链；`/goal set/resume` 空闲会话下即自动起续跑流。
- **`续跑注入为内部指令`**：continuation / budget_limit / objective_updated 提示以 `system` 首位消息构造，不落库、前端不渲染为用户泡泡；`steer.py` / `middleware.py` 零改动。
- **`双控制语义`**：输入框 Stop 停任务（goal 保持 active、续跑需 kick）；TodoBlock pause/resume/clear 控制目标作用；goal 严格会话隔离（TodoBlock 只显示当前会话 goal）。
- `回归不退化`：现有 `/chat/stream`、`/chat/interject`、普通一问一答、steer 插话路径无回归。
- `文档收口`：本 task list、设计文档、前端命令卡、相关 docs-facts 已同步。

## 输入真源

后端（必读 / 必改）：
- `backend/coworker/sessions.py` — `Session` 模型与 `SessionStore`（新增 `GoalState` 与 CRUD）。
- `backend/main.py` — `chat_stream`（`:1679`）、`event_stream` 内 `_handle_event` 的 `done` 处理（`:1832`）、`_on_end`/`_on_error`、现有 `skip_user_append` 路径（`:1744`）、续跑轮 messages 构造、`_persist_assistant`（`:1780`，续跑轮须传 `message_id=None`）、`snapshot_manager.begin_turn/end_turn`（`:1958/:2014`，须按轮包裹）、`/chat/interject`（`:2040`）。
- `backend/coworker/agent/core.py` — 工具 schema 定义（如 `RunCommandArgs` 等）、`build_workspace_tools` / 工具可见性控制、`prepare_agent_messages`（`:944`，原生支持 `system` 角色）。
- `backend/coworker/agent/graph.py` — `build_workspace_tools`（`:73` 引用）与中间件注册（`steer.py`/`middleware.py` **不改**，仅确认工具注册机制）。
- `backend/coworker/agent/runtime.py` — `stream`（`:891`）与 `context_usage` 帧产出位置。
- 新建 `backend/coworker/goal_prompts.py` — 三份提示词模板渲染（continuation / budget_limit / objective_updated）。
- `backend/coworker/steer.py` — 只读参照（interject 用，goal 续跑不碰）。

前端（必读 / 必改）：
- `frontend/src/App.tsx` — `sendMessage` 主流 done 处理（`:1606`）、interject 自动续跑（`:858`）、`stopMessage`（`:1798`）、`settleAssistantMessage` / 队列自动发送（`:1737`）、TodoBlock 渲染位（`:3624`）、消息可见性过滤（`:644`）；**单流多轮适配：每轮新气泡、未到终态不 stream-settle、goal 流终态判定**。
- `frontend/src/components/TodoBlock.tsx` — **goal section 渲染（复用现有卡片与位置）**：props 扩展 `goal` / `onGoalPause` / `onGoalResume` / `onGoalClear`，渲染条件加 `goal != null`。
- `frontend/src/components/ChatInput.tsx` — 斜杠命令卡（`/` 卡片，技能与子命令入口）。
- `frontend/src/services/chatService.ts` — `getGoal / setGoal / pauseGoal / resumeGoal / clearGoal / editGoal`（前端唯一入口）。
- 任一状态管理入口（store / context）— 新增 `goal` 状态（按 session 存，严格隔离）与 `goal_updated` 订阅。

Electron IPC 桥接（必读 / 必改，Step 1.4）：
- `electron/preload.js` — 暴露 `goalGet / goalSet / goalPause / goalResume / goalClear / goalEdit`。
- `electron/main.js` — `ipcMain.handle('goal-*', ...)` 转发到本地后端 HTTP（复用现有请求封装）。
- `frontend/src/electron.d.ts` — 补方法型别声明。

参照真源（codex，只读）：
- `codex-rs/ext/goal/src/{extension,runtime,steering,tool,spec,accounting}.rs`
- `codex-rs/state/src/model/thread_goal.rs`
- `codex-rs/ext/goal/templates/goals/{continuation,budget_limit,objective_updated}.md`
- `codex-rs/app-server/src/request_processors/thread_goal_processor.rs`

## 执行规则

- 每轮只允许推进本清单中最靠前的未完成 Step；完成条件全部满足才勾选。
- 先读真实代码、真实入口、真实主链，再动手；不允许凭印象改。
- 续跑循环必须复用 `/chat/stream` + `skip_user_append`，不允许新增第二主链。
- **续跑轮必须是"新的一轮"**：每轮 assistant 消息用新 id（续跑轮 `message_id=None`），snapshot 按轮 begin/end；禁止复用首轮的单轮态。
- **goal 续跑注入不碰 steer**：续跑上下文用「续跑轮 messages 首位拼 `system` 消息」实现；`steer.py` / `SteerInjectionMiddleware` 零改动（只读参照）。
- **双控制语义（用户拍板，选项 A）**：输入框 Stop 停任务、不改 goal 状态（续跑需 kick）；TodoBlock pause 停目标作用（下一轮边界生效、不 abort 当前轮）；两者互不替代。
- **goal 严格会话隔离**：goal 只作用 / 显示于当前会话；`goal_updated` 事件按 session 归属，TodoBlock 只渲染当前会话 goal。
- **三个续跑入口都要验证**：`/goal set|resume` 空闲启动、会话刷新恢复、HITL 后 resume 恢复——同一续跑循环、同一防重入闸门。
- 每完成一个 Step，补该 Step 的直接相关手动验证 / 回归（见完成标准），不允许只写代码不验证就勾选。
- 阶段结论变化必须同步：设计文档、本 task list、前端命令卡、相关 docs-facts。
- **测试豁免**：按用户明确指示，本专项**不要求失败测试（failure-first）**；以「实现后补直接相关手动链路验证 + 回归」替代，但禁止把未验证代码勾为完成。
- 不允许把一次性调试日志、长命令流水堆进本清单。

---

## Phase 1：数据模型与 API contract 建立

- [x] Step 1.1：在 `sessions.py` 建立 `GoalState` 正式模型与状态机
  - [ ] 预期目标：`Session` 拥有唯一 goal 真源，状态机与 codex `ThreadGoalStatus` 对齐。
  - [ ] 行为：goal 状态只能取 `active | paused | blocked | complete | budget_limited | usage_limited`。
  - [ ] 改动点：`backend/coworker/sessions.py`
    - 新增 `class GoalState(BaseModel)`，字段：`objective:str`、`status:Literal[...]`、`token_budget:int|None=None`、`tokens_used:int=0`、`time_used_seconds:int=0`、`created_at:int=0`、`updated_at:int=0`。
    - 在 `Session` 增加 `goal: GoalState | None = None`（默认 `None` 表示无目标）。
  - [ ] 组织：模型放 `sessions.py` 顶部与其他 `BaseModel` 同区；状态字面量与 codex 一致（小写下划线）。
- [x] Step 1.2：在 `SessionStore` 增加 goal 读写 API
  - [ ] 预期目标：为后续端点与续跑循环提供稳定 CRUD。
  - [ ] 行为：get/set/clear/update_status/account_usage 全部以 `session_id` 定位；并发写用既有 store 锁。
  - [ ] 改动点：`backend/coworker/sessions.py` 的 `SessionStore`
    - `get_goal(session_id) -> GoalState | None`
    - `set_goal(session_id, objective, token_budget) -> GoalState`（新建或覆盖重建，状态置 `active`，刷新时间戳）
    - `clear_goal(session_id) -> bool`
    - `update_goal_status(session_id, status) -> GoalState | None`（同时刷新 `updated_at`）
    - `account_goal_usage(session_id, token_delta, time_delta_seconds) -> GoalState | None`（累加 `tokens_used` / `time_used_seconds`；若 `token_budget` 非 None 且 `tokens_used >= token_budget` 自动把状态置 `budget_limited`）
  - [ ] 组织：方法与现有 `append_message`/`update_todos` 同风格，异常安全（store 写失败不影响主链）。
- [x] Step 1.3：在 `main.py` 新增 `/goal/*` 端点与 `goal_updated` 广播
  - [ ] 预期目标：前端与内部可通过 HTTP 设定/查询/控制目标，状态变化实时推给前端。
  - [ ] 行为：`/goal/set` 置 active 并广播；`pause`/`resume` 切 paused/active；`clear` 清并广播清除；`get` 返回当前 goal。
  - [ ] **`pause` 语义 = 停目标作用**：只改状态、**不中止进行中的流**（当前轮正常跑完，下一轮边界生效）；停进行中的任务走输入框 Stop（Phase 2 Step 2.2）。
  - [ ] 改动点：`backend/main.py`
    - 新增 Pydantic 请求体：`GoalSetRequest{session_id, objective, token_budget?}`、`GoalControlRequest{session_id}`。
    - `POST /goal/set`：校验 `session_id` 存在 → `session_store.set_goal(...)` → 调用 `_emit_goal_updated(session_id, goal)`。
    - `POST /goal/pause`：`update_goal_status(session_id, "paused")`（仅当当前 active/budget_limited 才允许，避免覆盖 complete/blocked）。
    - `POST /goal/resume`：`update_goal_status(session_id, "active")`（仅当当前 paused 才允许）。
    - `POST /goal/clear`：`clear_goal(...)` → 广播 `goal_cleared`。
    - `POST /goal/edit`：`update_goal_objective(session_id, objective)`（仅当当前 active 时允许，重建 objective 并刷新时间戳，状态保持 active）→ 广播 `goal_updated`。
    - `GET /goal?session_id=`：返回 `GoalState | None`。
  - [ ] **交付口径（goal_updated 的双通道）**：
    - **空闲态更新**（用户在无流式运行时点 pause/resume/clear/edit）：端点**直接在 HTTP 响应体返回更新后的 `GoalState`**，前端以响应为准刷新 TodoBlock goal section（不依赖 SSE 推送，因此时无活跃订阅）。
    - **流式态更新**（模型在运行中调 `update_goal` 改变状态）：通过既有 SSE 事件总线增量推送 `goal_updated{goal}`，前端实时刷新。
    - 两通道都落到同一前端 `goal` 状态，避免真源漂移。
  - [ ] **启动语义**：`/goal/set` 与 `/goal/resume` 返回 `active` 后，**前端必须立即自动发一次 `skip_user_append=True` 的 `/chat/stream`** 触发续跑（空闲会话下否则什么都不发生）；该请求复用 Phase 2 续跑循环，与"会话刷新恢复"同一路径、同一防重入闸门。
  - [ ] **`/goal/edit` 纳入本专项（已拍板）**：`update_goal_objective` 仅 active 时允许，状态保持 active，并在该轮注入 `render_objective_updated`（Phase 3/6）。
  - [ ] 组织：端点与 `chat_stream` 同文件、同鉴权风格；广播函数集中一处便于 Phase 5 前端订阅。
- [x] Step 1.4：Electron IPC 桥接层（打通前端 → 后端 `/goal`，已拍板纳入）
  - [ ] 预期目标：前端 `chatService` 可经 `window.electronAPI` 调 `/goal/*`，与现有 `getSession`/`stopSessionStream` 同构。
  - [ ] 改动点：
    - `electron/preload.js`：暴露 `goalGet / goalSet / goalPause / goalResume / goalClear / goalEdit`。
    - `electron/main.js`：对应 `ipcMain.handle('goal-*', ...)` 转发到本地后端 HTTP（复用现有请求封装）。
    - `frontend/src/electron.d.ts`：补方法型别声明。
    - `frontend/src/services/chatService.ts`：`getGoal / setGoal / pauseGoal / resumeGoal / clearGoal / editGoal`（前端唯一入口，与 `openSession` 的 `getSession` 同构）。
  - [ ] 组织：与 `/goal/*` 端点同步交付；命名沿用现有 `getSession`/`stopSessionStream` 风格。

## Phase 2：后端内部多轮续跑循环

- [x] Step 2.1：单一生成器内层多轮续跑循环（多轮包进传给 `_publish_turn` 的 `stream_iter`）
  - [ ] 预期目标：目标 active 时，一轮 `done` 后自动开启下一轮，无需前端再请求。
  - [ ] 行为：首轮走正常用户消息路径；续跑轮走 `skip_user_append=True` 且注入 goal 上下文（Phase 3）；`done` 且仍 active 则 `continue`；`error`/中断/Stop/非 active 则跳出。
  - [ ] 改动点：`backend/main.py` 的 `chat_stream` 内层 `event_stream()`
    - **落地方式（已拍板：单一生成器内层循环）**：把多轮续跑包进**传给 `_publish_turn` 的 `stream_iter` 生成器内部**——每轮调用一次 `runtime.stream(...)` 依序 `yield` 每轮事件；`_publish_turn` / SSE 订阅 / 会话事件总线只建一次，整个 goal 运行期保持单条 SSE 连接。
    - 用 `loop_index` 区分首轮/续跑轮；每轮起点**重置 `terminal_sent` / `interrupt_emitted` 等终态旗标**（否则第 2 轮起 `_on_error`/`_on_end` 的「已终态」短路逻辑错乱）。
    - 首轮：`messages = history + [user_message]`（保持现状）。
    - 续跑轮：`skip_user_append=True`，`messages = [{"role":"system","content": continuation_prompt}] + history`（Phase 3 落点；不经过 `steer_inbox`）。
    - **每轮必须是"新的一轮"（三个强制点）**：
      - 每轮 assistant 消息**新 id**：续跑轮 `_persist_assistant(...)` 传 `message_id=None`（后端生成），禁止复用首轮 `request.assistant_message_id`，否则第 2..N 轮写重复 id 污染会话。
      - **snapshot 按轮包裹**：每轮 `snapshot_manager.begin_turn / end_turn` 各调一次（绑定该轮 user_message_id），避免"编辑回滚该轮改动"跨轮误伤。
      - 每轮 `messages` 取**最新** session history（上一轮 assistant 已持久化），保证第 N 轮看到前 N-1 轮成果。
    - `done` 处理（`_handle_event` 内 `etype=="done"`，`:1832`）：执行完现有持久化后，读 `session_store.get_goal(session_id)`；若 `goal and goal.status == "active"` 且未收到 Stop/中断信号 → 置 `continue_loop=True`；否则 `break`。
    - `error` / 客户端断开 → `break`（对标 codex `Blocked`/`UsageLimited` 停循环）。
    - `approval_required` / `question_required`（HITL）→ `break` **但保留 interrupt checkpoint**（goal 保持 active），前端 `waiting` → 用户解决审批后 resume 流重查 goal 续跑（Step 2.4）。
  - [ ] 组织：循环变量与退出标志集中声明；每轮结束做 `account_goal_usage`（Phase 4 接入点）；退出前发一次最终 `goal_updated` 让前端收口。
- [x] Step 2.2：接入 Stop 与并发守卫
  - [ ] 预期目标：输入框 Stop 停当前任务（整个续跑 loop 终止），goal 状态不变（active）；与现有 `_guard_session_not_streaming` 不冲突。
  - [ ] 行为：Stop（客户端 abort / `/sessions/{session_id}/stop` 强停）→ `_on_error`/取消，整个流与 loop 一起死亡；**goal 保持 active**，后续续跑需 kick（新消息 / resume / 重开会话）；不允许续跑轮绕过 Stop。
  - [ ] 改动点：`backend/main.py` 复用现有 `terminal_sent`/`interrupt_emitted` 标志与客户端断开检测；续跑 `continue` 条件增加「未中断」闸门。
  - [ ] 组织：续跑判断条件写成单一只读函数 `_should_continue_goal(session_id, stopped)`，便于回归与阅读。
- [x] Step 2.3：会话重开 / 前端刷新时恢复 goal 续跑（对标 codex `restore_inherited_goal_runtime`）
  - [ ] 预期目标：用户在目标执行中途刷新页面或重开会话，goal 不丢失且能继续推进。
  - [ ] 行为：
    - 前端加载会话时拉取 `GET /goal`，若返回 `active` 目标则在 TodoBlock 渲染 goal section，并**自动发一次 `/chat/stream`（skip_user_append=True，无用户消息）** 以重启续跑循环。
    - 若 goal 状态是 `paused` / `complete` / `blocked` / `budget_limited` / `usage_limited`，只渲染、不自动重启。
  - [ ] 改动点：
    - `backend/main.py`：`GET /goal` 已提供；`chat_stream` 现有续跑循环可承接「无用户消息 + skip_user_append」的重启请求（首轮 `user_message=None` 路径已存在 `:1744`）。
    - 前端会话加载逻辑（`App.tsx` 或会话 store）：载入后若 `goal.status == "active"`，自动触发一次续跑 `chat_stream`（复用现有发送函数，仅置 `skip_user_append`）。
  - [ ] 组织：重启请求与普通续跑轮走同一代码路径，不新增第二主链；避免重复重启（以「当前无进行中 stream」为前置闸门）。
- [x] Step 2.4：空闲启动 + HITL 恢复（两个续跑入口，与 2.3 共用同一路径）
  - [ ] 预期目标：`/goal set|resume` 空闲会话下即自动起跑；goal 轮触发审批/提问后，用户解决审批能恢复续跑，不丢链。
  - [ ] 行为：
    - **空闲启动**：前端 `/goal set`（或 `resume`）接口返回 `active` 后立即自动发一次 `skip_user_append=True` 的 `/chat/stream`（Step 1.3 的启动语义），与 2.3 同一代码路径。
    - **HITL 恢复**：goal 轮 `approval_required` / `question_required` → 后端 break 但保留 interrupt checkpoint（goal 仍 active）；前端消息进 `waiting`；用户解决审批后对当前 `waiting` 消息发起 resume 流，**resume 流启动时重查 `GET /goal`，若仍 active 则回到续跑循环**。
  - [ ] 改动点：
    - 后端：无需新端点；`chat_stream` 承接「有 pending approval 的 resume 请求」时，若 goal active 则进入续跑循环（复用 Step 2.1）。
    - 前端：`/goal` 命令 handler 与 HITL resume handler 各自在成功后调用同一个"启动续跑流"函数（防重入：当前无进行中 stream）。
  - [ ] 组织：`/goal set|resume`、会话刷新恢复、HITL 恢复三者共用「启动续跑流」函数与「当前无进行中 stream」闸门，禁止各自为政。

## Phase 3：续跑上下文注入（直接构造 messages，不扩展 steer inbox）

- [x] Step 3.1：续跑轮直接构造 `system` 首位消息注入 goal 上下文
  - [ ] 预期目标：goal 续跑提示作为**内部指令块**注入，不与用户插话混淆，且前端不渲染为用户泡泡。
  - [ ] 行为：续跑轮 `messages = [{"role":"system","content": render_goal_continuation(goal)}] + history`；该 system 消息不落库、前端不渲染（对齐 codex `InternalModelContextFragment`）。
  - [ ] 改动点：
    - `backend/main.py` 续跑轮起点构造 `messages` 时拼 system 首位消息（`prepare_agent_messages` 原生支持 `system` 角色，`core.py:956-965`；`NormalizeMessagesMiddleware` 只约束非首位 system 降级，放首位安全）。
    - **`steer.py` / `SteerInjectionMiddleware` 零改动**（只读参照），`steer_inbox` 保持只服务 interject。
  - [ ] 组织：注入内容（continuation / budget_limit / objective_updated）按 goal 状态分支选择，渲染函数集中在 Phase 6 的 `goal_prompts.py`。
- [x] Step 3.2：预算超限 / objective 编辑的注入分支
  - [ ] 预期目标：`budget_limited` 轮与 `edit` 场景注入对应提示。
  - [ ] 行为：
    - 续跑轮起点若 `goal.status == "budget_limited"` → 注入 `render_budget_limit(goal)`（仅注入一次，对标 `budget_limit.md`）。
    - `edit` 场景（objective 被改且当前轮未启动）→ 注入 `render_objective_updated(goal)`（对标 `objective_updated.md`）。
    - goal 非 active 不注入。
  - [ ] 改动点：`backend/main.py` 续跑轮 messages 构造处的分支逻辑；渲染函数见 Phase 6。
  - [ ] 组织：与 Step 3.1 同一构造点，单一函数返回"该轮要注入的 system 内容或 None"。

## Phase 4：模型工具与记账兜底

- [x] Step 4.1：注册 `update_goal` + `get_goal` 模型工具
  - [ ] 预期目标：模型在目标模式下可声明完成/受阻（`update_goal`）、主动读取目标状态（`get_goal`）；无权改 paused/resume/budget。
  - [ ] 行为：仅当 session 有 active goal 时工具可见；`update_goal` 调用后持久化状态，续跑循环据此停止（complete/blocked）；`get_goal` 只读查询。
  - [ ] 改动点：
    - `backend/coworker/agent/core.py`：新增 `ManageGoalArgs(BaseModel)`（`status: Literal["complete","blocked"]`）与 `GetGoalArgs(BaseModel)` 及工具 schema；在 `build_workspace_tools` 按 `session_id` 的 goal 状态决定是否加入工具集。
    - 工具执行体：`update_goal` 调用 `session_store.update_goal_status(session_id, status)` 并 `_emit_goal_updated(...)`；遇非 active 时报错提示「当前没有 active 目标」。`get_goal` 返回当前 `GoalState | None`（仅查询）。
    - `backend/coworker/agent/graph.py`：确保工具在 graph 工具清单中注册（与现有工具同机制）。
  - [ ] 组织：工具描述照搬 codex `update_goal` 严格约束（完成须逐条审计；blocked 须连续 ≥3 轮同一阻塞），见 Phase 6 提示词。
- [x] Step 4.2：续跑轮记账与兜底
  - [ ] 预期目标：goal 维度 token/time 准确累加，预算/错误自动停循环。
  - [ ] 行为：每轮结束用 `context_usage` 帧累加 `tokens_used`；wall-clock 差累加 `time_used_seconds`；超预算自动 `budget_limited` 并注入预算提示；turn 报错（非可重试）置 `blocked`；用量超限置 `usage_limited`。
  - [ ] 改动点：
    - `backend/main.py` `event_stream` 每轮 `done` 后调用 `session_store.account_goal_usage(session_id, token_delta, time_delta)`（token 取自 `context_usage.used_tokens`，time 取本轮起止差）。
    - 续跑轮起点若检测到 `goal.status == "budget_limited"` → 在 messages 首位注入 `render_budget_limit(goal)`（Phase 6 模板，Step 3.2 分支）。
    - turn 抛错路径（`_on_error`）若当前 goal active → `update_goal_status(session_id, "blocked")`（对标 codex `TurnError`→Blocked）。
  - [ ] **`usage_limited` / `stalled` 状态范围界定（避免遗留半成品）**：
    - `usage_limited`：cw 无 org/账户级用量上限检测机制，本专项**不自动触发**该状态（仅在数据模型保留枚举位以对齐 codex schema）；若未来接入用量上限再补触发器。
    - `stalled`：cw 不单独建模「active 但无进展」；续跑中若模型连续多轮无任何工具调用/文件改动，仍显示为 `active`。如需 stall 检测，列为后续增强，不在本专项范围。
  - [ ] 组织：记账写入集中在 Phase 2 的续跑收尾处，避免分散；预算提示渲染放 `goal_prompts.py`。

## Phase 5：钉窗 UI 与 `/goal` 命令

- [x] Step 5.1：前端 `goal` 状态与事件订阅
  - [ ] 预期目标：前端持有 goal 真源镜像，实时响应 `goal_updated` / `goal_cleared`。
  - [ ] 行为：收到事件更新 TodoBlock goal section；`goal_cleared` 清空。
  - [ ] 改动点：前端状态管理入口新增 `goal: GoalState | null`；在既有 SSE 事件分发处订阅 `goal_updated`/`goal_cleared`（与现有事件总线同机制，不新建通道）。
  - [ ] 组织：状态形态与后端 `GoalState` 字段对齐。
- [x] Step 5.2：TodoBlock 内 goal section（复用现有卡片与位置，不另设 GoalBar）
  - [ ] 预期目标：goal 显示在 TodoBlock 卡片（`workspace-composer-slot`，App.tsx:3624），展示 objective、状态、已用时间、token 预算进度 + **pause/resume/clear 按钮**。
  - [ ] 行为：状态标签对齐 codex `goal_status_label`：`active`→进行中、`paused`→已暂停、`blocked`→受阻、`complete`→已完成、`budget_limited`→预算受限、`usage_limited`→用量受限。
  - [ ] 改动点：`frontend/src/components/TodoBlock.tsx` 扩展 props（`goal: GoalState | null`、`onGoalPause/onGoalResume/onGoalClear`）；App.tsx:3624 渲染条件加 `goal != null`；goal 状态按 session 存（`sessionGoals` 平行于 `sessionTodos`），**TodoBlock 只渲染当前会话 goal（严格会话隔离）**。
  - [ ] 组织：组件样式沿用现有 TodoBlock capsule/状态色板（`components/ui/type-capsule.tsx` 风格）。
- [x] Step 5.3：`/goal` 聊天命令 + TodoBlock 按钮
  - [ ] 预期目标：用户在输入框 `/goal <目标>` 即可设定并激活；`/goal pause|resume|clear` 控制；TodoBlock 内按钮同一语义。
  - [ ] 行为：命令解析后调 `Phase 1` 的 `/goal/*` 端点（**只作用于当前会话**）；`/goal clear` 立即终止续跑并清状态；**`/goal set|resume` 返回 `active` 后立即触发续跑流**（Step 2.4）。
  - [ ] 改动点：`ChatInput.tsx` 斜杠菜单与 `App.tsx` 命令分发：新增 `/goal` 子命令集；`TodoBlock.tsx` 的 goal section 按钮（Step 5.2）调用同一端点。
  - [ ] 组织：命令与现有 `/` 菜单同入口，不新建交互范式；解析逻辑与既有命令分发一致。
- [x] Step 5.4：单流多轮的前端适配（goal 流 = 同一条 SSE 上 N 个 `done`，不能"沿用现有 done 逻辑"）
  - [ ] 预期目标：多轮 assistant 输出全部可见、消息 id 唯一、未到终态前不提前收尾。
  - [ ] 行为：
    - 主流 `done` 处理（`App.tsx:1606`）当前把第一个 `done` 当终态 → 改为**每个 `done` 独立 commit**：首轮复用 `assistant_message_id`，后续轮**新建 assistant 气泡**（新 id 由后端生成并在 `done` 帧回带，或前端按轮生成）。
    - **stream-settle / 对账延迟到真正终态**：`settleAssistantMessage`、队列自动发送（`:1737`）、`isStreamStale` 在 goal 流未收到终态事件（最终 `done` / `error` / `worker_stream_end`）前不触发；**提前收尾 = 输入框 Stop**（停任务，goal 保持 active，复用 `stopMessage` App.tsx:1798）。
    - 续跑轮不产生新的 user 消息（continuation 是内部 system 指令，前端无用户泡泡）。
  - [ ] 改动点：`frontend/src/App.tsx`（`sendMessage` / `handleEvent` / `settleAssistantMessage` / 队列 effect）；如后端在 `done` 帧回带 message id，同步后端 Step 2.1。
  - [ ] 组织：TodoBlock goal section + 气泡渲染共用同一 `goal` store；避免给 goal 流引入独立连接/轮询。

## Phase 6：续跑提示词移植与行为对齐

- [x] Step 6.1：建立 `goal_prompts.py` 并移植 `continuation.md`
  - [ ] 预期目标：续跑上下文含 objective、token 预算、完成审计、blocked 审计、fidelity 约束，与 codex 行为一致。
  - [ ] 行为：模板渲染 `objective / tokens_used / token_budget / remaining_tokens`；模型被告知「目标跨轮持久、不得缩小目标、须逐条需求审计后才 complete」。
  - [ ] 改动点：新建 `backend/coworker/goal_prompts.py`，提供：
    - `render_goal_continuation(goal) -> str`（移植 `codex-rs/ext/goal/templates/goals/continuation.md` 全文要点）
    - `render_budget_limit(goal) -> str`（移植 `budget_limit.md`）
    - `render_objective_updated(goal) -> str`（移植 `objective_updated.md`，用于 edit 场景）
  - [ ] 组织：模板用 Python 字符串/`str.format` 渲染；常量集中，便于回归。
- [x] Step 6.2：`update_goal` 工具描述移植严格约束
  - [ ] 预期目标：模型侧约束与 codex `update_goal` 完全一致。
  - [ ] 行为：complete 须逐条需求审计；blocked 须连续 ≥3 轮同一阻塞；禁止模型置 paused/resume/budget。
  - [ ] 改动点：`backend/coworker/agent/core.py` 工具 description 直接采用 codex `spec.rs` 中 `update_goal` 的英文/中文约束文本。
  - [ ] 组织：描述文本与 Phase 6.1 提示词口径统一，避免矛盾。

## Phase 7：回归、验收与文档收口

- [x] Step 7.1：手动链路验证（替代失败测试，按用户豁免）
  - [ ] 预期目标：核心行为可端到端复现。
  - [ ] 行为：
    - `/goal 写一个计算器 CLI` → TodoBlock 显示 goal（active），**空闲会话下自动起跑**，自动连跑多轮直至 `update_goal(complete)`。
    - **双控制验证**：运行中输入框 Stop → 当前任务停、goal 仍显示 active、发一条新消息后该轮 `done` 自动恢复续跑；TodoBlock pause → 当前轮正常跑完、下一轮停、goal 变 paused（不 abort 当前轮）；resume 恢复续跑。
    - **刷新/重开会话** → 会话加载恢复续跑（Step 2.3）；`/goal set|resume` 空闲启动（Step 2.4）。
    - **HITL**：goal 轮触发审批/提问 → 消息 `waiting` → 用户解决 → resume 后 goal 仍 active 则继续续跑（Step 2.4）。
    - **多轮无脏数据**：会话 JSON 中每轮 assistant 消息 id 唯一；编辑第 1 轮消息回滚只撤销第 1 轮文件改动（不跨轮误伤）。
    - **会话隔离**：A 会话设 goal 后切到 B 会话，B 的 TodoBlock 不显示 A 的 goal、B 的输入不受影响。
    - 超小 `token_budget` → 自动 `budget_limited` 停 + 预算提示。
    - 人为制造持续报错 → `blocked` 停。
    - `/goal pause` 挂起续跑；`/goal resume` 恢复；`/goal clear` 清空（TodoBlock 按钮同样验证）。
    - **goal 运行期**：普通新消息只能排队（不立即发送）、编辑/重生成被 409 拒、interject / 输入框 Stop / TodoBlock pause 可用——与文档会话锁声明一致。
    - 普通一问一答（无 goal）行为完全不变；`/chat/interject` 插话不变。
  - [ ] 改动点：无代码改动，仅验证；记录验证结果到本 Step。
- [x] Step 7.2：完成第五章节「行为对齐校验清单」
  - [ ] 预期目标：全部勾选项通过。
  - [ ] 改动点：逐条核对并勾选 `goal-capability-plan.md` 第五章；未过项回到对应 Phase 修复。
- [x] Step 7.3：文档与真源同步
  - [ ] 预期目标：设计文档、本 task list、前端命令卡、docs-facts 一致。
  - [ ] 改动点：
    - 更新 `goal-capability-plan.md` 与本文档的实施结论。
    - 若新增前端命令卡/TodoBlock goal section，在 README 或命令清单补充说明。
    - 顶部「状态」由「执行中」视情况转「已完成冻结，不再作为当前前线执行面」。
  - [ ] 组织：所有同步在同一轮内完成，避免真源漂移。

## 附：行为对齐校验清单（Phase 7.2 勾选源）

> 勾选依据：后端脚本化 fake runtime 端到端验证（多轮循环 / budget 兜底 / HITL / restore kick / pause / error→blocked / 无 goal 零回归）＋ `/goal/*` 端点 TestClient 验证 ＋ 前端 tsc/build ＋ **vLLM 真机对 ailicode 项目的真实任务验收（2026-08-25，8 轮自动续跑至 complete）**。

- [x] `/goal <obj>` 后目标钉窗显示 active，无需用户再输入即自动开下一轮（**空闲会话下 `/goal set` 即自动起续跑流，无需刷新**）。
- [x] 每轮结束若仍 active 则续跑；模型不得自行缩小目标（continuation 约束）。（真机：复杂目标跨 8 轮持续推进至完成）
- [x] 模型只能 `update_goal(complete|blocked)`；暂停 / 恢复 / 清理由用户命令控制。
- [x] `complete` 须经逐条需求审计；`blocked` 须连续 ≥3 轮同一阻塞。（真机：模型逐条验证交付物后 `update_goal(complete)` 成功）
- [x] 超 token 预算 → `budget_limited` 停 + 注入预算提示；turn 报错 → `blocked` 停。
- [x] `pause` / `resume` 正确挂起 / 恢复续跑；`clear` 立即终止循环并清状态条。
- [x] **双控制语义**：输入框 Stop 停当前任务、goal 保持 active、续跑需 kick（新消息 / resume / 重开会话）；TodoBlock pause 停目标作用（当前轮跑完、下一轮边界停）、不 abort 进行中的任务。
- [x] **goal 严格会话隔离**：TodoBlock 只显示当前会话的 goal；跨会话不显示、不影响。
- [x] **多轮会话无脏数据**：每轮 assistant 消息 id 唯一、每轮 snapshot 独立回滚、"编辑回滚该轮改动"不跨轮误伤。
- [x] **HITL 恢复**：goal 轮触发 approval/question → 消息 `waiting` → 用户解决后 resume 流重查 goal 仍 active 则继续续跑。
- [x] **会话锁语义明确**：goal 运行期普通消息只能排队、编辑/重生成被 409 拒、interject / 输入框 Stop / TodoBlock goal pause 可介入——与文档声明一致。
- [x] **续跑注入为内部上下文**：continuation/budget/objective 提示是 system 内部指令，不落库、前端不渲染为用户泡泡；`steer.py` / `middleware.py` 零改动。

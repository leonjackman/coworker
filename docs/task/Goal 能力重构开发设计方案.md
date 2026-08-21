# Coworker Goal 能力重构开发设计方案

更新时间：2026-08-21
状态：已完成（2026-08-21 实现落地，回归通过）
适用范围：`/Users/leon/Documents/CodeProjects/coworker` 下 Goal 能力专项重构
设计规范参照：`/Users/leon/Documents/CodeProjects/ARKS/docs/ruls/ARKS 专项开发设计方案制定规范.md`

说明：

- 本文档定义 Coworker Goal 能力的重构方案：从「裸无限循环 + 工具源头失控」重构为「Plan → Execute → Verify 三阶段 + 工具源头截断 + 渐进式仓库探索」。
- 背景、证据与行业对标见《Goal 能力调研报告.md》；本文档只做正式设计边界，不写过细实施步骤。
- 本方案已确认决策：1A（在现有 `goal_stream` 内改造）+ 2B（goal 模式移除 `git_status`、强制渐进探索）+ 3（`read_file` 50K）+ 4A（提示词引导验证）+ 5（前后端同时）。
- 依赖的仓库级规则以本仓库实际代码现实为准；ARKS 规范用于约束本文档结构、owner、单一真源、删除面、禁止事项与验收口径。
- 所有行号以 2026-08-21 工作区为准；实现前须重新核对。

---

## 0.1 实现落地记录（2026-08-21）

> **v2 治理层重构（2026-08-21 晚）**：行业对标（Codex/SWE-agent/Cline/opencode）后新增——预算会计（100 万 token + 30 分钟）+ 模型可感知注入、进度指纹去重防 runaway、token 软阈值回合交接、控制工具 parts 清理、`goal_status` 状态机收口。详见 task-list「第二轮：Goal 治理层重构」。本设计方案正文仍是 v1 阶段化设计。

- 已按 task-list 全量实现并回归通过（详见 task-list「实现记录」与完成标准核对表）。
- 实现偏差（相对本设计，均为小范围收口）：
  1. `read_preview` 默认 `max_chars` 保持 `100_000`（task-list Step 2.3 显式要求，前端文件预览路径不变），未按 §8「对齐 `READ_FILE_MAX_CHARS`」改默认值。
  2. plan 阶段无 todos 不强制计入 stall（§6.2 的「无 todos → force-loop/stalled」以既有 checkpoint 防空转语义落地，避免破坏 `test_goal_loop.py` 既有用例的「checkpoint 重置 force 计数」行为）。
  3. goal 模式工具列表移除 `git_status` 落在 `GoalModeMiddleware._overrides`（goal 模式模型可见工具集过滤），未改 `build_coworker_agent_graph` 的 tools 入参——`git_status` 定义与 `_READ_ONLY_TOOLS` 原样保留。
- 主要落点（行号以当前工作区为准）：
  - `workspace.py`：`READ_FILE_MAX_CHARS=50_000`（≈37 行）、`GIT_MAX_FILES=50` / `GIT_MAX_DIFF_CHARS=100_000` / `GIT_MAX_PER_FILE_DIFF_CHARS=2_000`（≈1337 行）、`workspace_git_diff` 每文件 diff 截断（≈1445 行）。
  - `agents.py`：`_GOAL_PHASES` / `_first_incomplete_todo`（≈2890 行）、`goal_system_prompt` 三阶段文案（≈2947 行）、`GoalModeMiddleware._overrides` 移除 `git_status` + 注入 phase/todo（≈3059 行）、`read_file` 复用 `read_preview`（≈565 行）、`_stream` 新增 `goal_phase`/`goal_todo` 参数与 inputs（≈3915 / 3990 行）、`goal_stream` 阶段推进（≈4230-4360 行）。
  - `sessions.py`：`goal_phase` 字段 / `from_dict` / `public` / `full` / `update_goal`。
  - `main.py`：`chat_stream` goal 分支与 `/goal/start` 初始化 `goal_phase="plan"`、`/goal/resume` 按 todo 推断写回、`/goal/status` 返回 `goal_phase`。
  - 前端：`types.ts`（`GoalState.phase` / `goal_round.phase` / `SessionSummary.goal_phase`）、`GoalCard.tsx` 阶段徽标、`App.tsx` 事件与恢复透传、11 个 locale 阶段文案。

---

## 0. 输入真源 / 当前证据
- 调研报告：《Goal 能力调研报告.md》（同目录）—— 含完整代码行号、运行证据、行业对标。
- 当前代码（精确定位见调研报告第 0.1 节）：
  - `backend/coworker/agents.py`：`goal_stream`（4031）、`GoalModeMiddleware`（2959）、`goal_system_prompt`（2919）、`_goal_tools`（2898）、工具列表（924）、`git_status`（679）、`read_file`（557）、`_stream`（3862）、`CoworkerSummarizationMiddleware`（2306）、`build_coworker_agent_graph`（3220）、`_handle_message_chunk`（4380-4427）
  - `backend/coworker/workspace.py`：`workspace_git_diff`（1389）、`GIT_MAX_FILES`（1337）、`GIT_MAX_DIFF_CHARS`（1338）、`read_text`（499）、`read_preview`（1160）、`is_text_file`（1146）、`MAX_COMMAND_OUTPUT_CHARS`（37）
  - `backend/coworker/sessions.py`：`Session.goal_*`（49-58）、`update_goal`（203）、`commit_goal_end`（242）
  - `backend/main.py`：goal 端点（`/goal/start` 2491、`/goal/status` 2438、`/goal/pause` 2473、`/goal/resume` 2557、`/goal/delete` 2527、`/goal/edit` 2482、`/goal/stop` 2416）、`chat_stream` goal 分支（1894-1932）
  - `frontend/src/types.ts`：goal 事件类型（667-668）、`GoalState`（720-735）、`GoalStatusResponse`（736）
  - `frontend/src/App.tsx`：goal 事件处理（1264-1275）、resume 处理（3034-3070）
  - `frontend/src/components/GoalCard.tsx`、`frontend/src/locales/*.json`
- 运行证据：会话 `1fd5f8bf-8d9f-4d4e-b6c7-1c7045f879c9`（git_status 1MB、上下文 300k 跳变、vLLM 600s 停滞；详见调研报告第 0.3 / 6 节）。

---

## 1. 背景与问题定义

- **痛点**：Goal 模式下上下文预算从 0 直接跳到 ~300k tokens（`git_status` 一次返回 1MB+ diff），触发压缩后仍伴随 vLLM 极慢生成，最终长时等待触发 round 墙钟超时（会话 `1fd5f8bf` 实测 600s）。
- **影响主链**：`/chat/stream`（goal 分支，main.py:1894）→ `goal_stream`（agents.py:4031）→ `_stream`（agents.py:3862）→ `graph.astream`（model↔tool 循环）。失控点不在压缩中间件（已对齐 opencode），而在**工具源头**与**执行模型**。
- **不做的持续风险**：任意大仓库上 goal 首轮就会因 `git_status` 全量 dump 而预填过载；上下文管理机制正常却因源头注入过度而失效；回合无子任务边界导致压缩只能被动兜底；前端长期面对「计划中却空转」的坏体验。
- **本次解决层级**：工具层源头截断（P0 根因）+ 执行模型重构（P0 行为）+ 仓库探索引导（P0 行为）+ 读面收口（P1）。

---

## 2. 目标

- **主目标 1（工具源头截断）**：`git_status` 单文件 diff 上限 2,000 字符、总文件数上限 50、整库 diff 总量上限 100,000 字符；超出只保留 `path/added/removed` 统计 + `note`。可验收：任意大仓库 `git_status` 返回 ≤ ~110KB。
- **主目标 2（执行模型阶段化）**：Goal 循环改为 Plan → Execute → Verify 三阶段；回合边界 = 一个 todo 子任务；`goal_phase` 随 checkpoint 持久化。可验收：`/goal/status` 返回 `goal_phase`，前端展示「计划中 / 执行中 / 验证中」。
- **主目标 3（仓库探索引导）**：goal 系统提示改为渐进式探索；goal 模式从工具列表移除 `git_status`。可验收：goal 首轮不再调用 `git_status`，改用 `search_files`/`read_file` 按目标所需。
- **主目标 4（read_file 读面收口）**：`read_file` 工具复用 `read_preview` 语义（`is_text_file` 二进制检测 + 50,000 字符上限）。可验收：二进制文件返回受限结果而非抛错；大文本截断 + 标记。
- **次目标**：前端 GoalCard 展示阶段文案；goal 事件 / reconcile / 恢复与新阶段兼容；现有原子终态、pause/resume/delete/edit、压缩中间件、600s 墙钟、force-loop 全部保留且回归通过。

---

## 3. 非目标

- **不改压缩中间件**：`KEEP_RECENT_TOKENS=8000`、`SUMMARY_OUTPUT_TOKENS=4096`、`TOOL_OUTPUT_MAX_CHARS=2000`、`SUMMARY_INPUT_MAX_TOKENS=32000` 等已对齐 opencode，保持冻结。
- **不引入 subagent 隔离**（Claude Code 式）：本轮不做子代理上下文隔离，记入暂缓项。
- **不引入 repo map / 符号索引**（Aider 式）：本轮不做 tree-sitter 仓库符号图，记入暂缓项。
- **不做显式 verify 工具 / interrupt 验证门**（LangGraph evaluator 式）：本轮只靠提示词引导，记入暂缓项。
- **不治理 vLLM 多会话并发**：checkpoint 库多线程并发是独立话题，超出本次边界。
- **不改 600s 墙钟**：按已确认决策保持不变。
- **不重写 goal 为独立 orchestrator 模块**（方案 1B）：按已确认决策在现有 `goal_stream` 内改造（1A）。
- **不新增第二套 goal 状态/入口/owner**：只新增 `goal_phase` 字段与既有 `goal_*` 同源管理，不另起炉灶。
- **不把 `git_status` 从仓库删除**：普通对话 / 只读子代理（`_READ_ONLY_TOOLS`）仍使用；只在 goal 模式工具列表移除。

---

## 4. 设计原则

- **单一真源**：阶段、回合、截断阈值的正式常量/字段只放一处（见第 8 节），其余调用方引用。
- **单一主链**：Goal 仍从 `/chat/stream`（goal 分支）或 `/goal/*` 端点进入 `goal_stream`，不新增第二条 goal 执行链。
- **owner 唯一**：`goal_stream` 是 goal 状态机的唯一 owner（承上轮修复结论）；阶段推进只在 `goal_stream` 内决定，消费者只透传/展示。
- **上游只传正式 intent**：阶段/todo 只作为注入上下文传给模型，不由模型或前端决定阶段流转；`goal_phase` 由 `goal_stream` 根据 checkpoint 推进。
- **不污染已冻结主链**：压缩中间件、原子终态、SSE 事件契约、前端 reconcile 逻辑保持冻结，只做兼容性扩展。
- **不把调试逻辑产品化成散装开关**：截断阈值、阶段枚举收敛为正式常量，不散落各文件。
- **失败测试先行**：每个行为变更先写能复现旧缺陷的失败测试，再实现使其通过。

---

## 5. 核心概念定义

### 5.1 `goal_phase`（阶段）

- 名称：`goal_phase`
- 取值：`plan` / `execute` / `verify`
- 职责：标识 Goal 当前处于三阶段之一；由 `goal_stream` 根据 checkpoint 推进。
- 不负责：不决定具体 todo 内容，不驱动回合计数（`round_no` 仍由 `goal_stream` 管理），不做工具白名单判断。
- 与现有概念关系：作为 `Session.goal_*` 一族新增字段（sessions.py:49-58），与 `goal_done`/`goal_paused`/`goal_todos` 并列，随 checkpoint 持久化。
- 是否替代旧概念：否，纯新增。
- 兼容/迁移：向后兼容；旧会话无此字段时按 `plan`（新 Goal 起始）或由当前 todo 状态推断（见第 11 节）。

### 5.2 `todo` 子任务（回合边界）

- 名称：goal todo（沿用 `goal_todos`）
- 职责：一个可执行的子任务；**每轮（execute 阶段）聚焦一个 todo**，做完调 `finalize_goal(achieved=false)` 推进。
- 不负责：不承载验证证据（verification 走 `finalize_goal`）。
- 与现有概念关系：沿用 `write_todos` 工具与 `todos` 事件；新增「一轮 = 一个 todo」的边界语义。
- 是否替代旧概念：否，赋予旧概念新边界语义。
- 当前 todo 的选取规则（由 `goal_stream` 决定，单一真源）：取 `goal_todos` 中第一个 `status != "completed"` 的项作为注入目标；没有则视阶段为 `verify`。

### 5.3 `GIT_MAX_PER_FILE_DIFF_CHARS`（单文件 diff 上限）

- 名称：`GIT_MAX_PER_FILE_DIFF_CHARS`
- 职责：`git_status` 单文件 unified diff 的最大字符数，超出只保留统计行。
- 默认值：`2_000`（对齐 opencode `TOOL_OUTPUT_MAX_CHARS=2000`）。
- 放置：`backend/coworker/workspace.py`（与 `GIT_MAX_FILES`/`GIT_MAX_DIFF_CHARS` 同处，单一真源）。

### 5.4 `READ_FILE_MAX_CHARS`（read_file 大小上限）

- 名称：`READ_FILE_MAX_CHARS`
- 职责：`read_file` 工具读取文本的最大字符数。
- 默认值：`50_000`（已确认决策 3）。
- 放置：`backend/coworker/workspace.py`（与 `MAX_COMMAND_OUTPUT_CHARS` 同处，收口现有散落）。

### 5.5 `goal 系统提示`（渐进式探索指令）

- 名称：goal system prompt（`goal_system_prompt`，agents.py:2919）
- 职责：注入目标 + 三阶段指令 + 渐进式探索约束；不再引导 `git_status` 全量。
- 不负责：不决定阶段流转（owner 仍是 `goal_stream`）。

---

## 6. 正式主链与控制流设计

### 6.1 入口

- 新 Goal：`/chat/stream`（`request.goal_mode=true`）→ `chat_stream` goal 分支（main.py:1894）→ `runtime.goal_stream`。
- 恢复 Goal：`/goal/resume`（main.py:2557）→ `goal_resume` → `runtime.goal_stream`（`goal_continue_first=true`）。
- 命令端点：`/goal/start`、`/goal/pause`、`/goal/delete`、`/goal/edit`、`/goal/stop`、`/goal/status`。

### 6.2 状态 owner 与阶段流转（`goal_stream` 内）

```
初始：goal_phase = "plan"（由 chat_stream 或 /goal/start 写入）

while True:
    round_no += 1
    # 读 session_state（goal_just_edited / goal_stopped / goal_max_rounds / goal_phase / goal_todos）
    if goal_stopped → _commit_terminal(stopped) → goal_done(stopped)
    if 超 max_rounds → _commit_terminal(stopped) → goal_done(max_rounds)

    yield goal_round (带 phase)

    # 根据 goal_phase 构造注入上下文：
    #   plan    → 注入「目标 + 制定计划指令 + 渐进式探索约束」
    #   execute → 注入「目标 + 当前 todo + 完成该 todo 指令」
    #   verify  → 注入「目标 + 全部 todo 完成，运行验证 → finalize_goal(true, verification)」

    try:
        async with asyncio.timeout(600):
            async for event in _stream(goal_mode=True, goal_text, goal_continue=round>1, goal_phase, current_todo):
                goal_checkpoint → last_checkpoint
                todos → last_todos
                approval_required → round_had_interrupt
                delta / done → accum_content / accum_parts
                yield event
    except TimeoutError / CancelledError → 终态处理（不变）

    轮结束分支（阶段推进只在以下分支决定）：
      achieved=true → _commit_terminal(done) → goal_done
      paused → _commit_terminal(paused) → goal_paused
      round_had_interrupt → _commit_terminal(paused) → goal_paused
      # ---- 阶段推进（新）----
      if goal_phase == "plan":
          if last_todos 非空：写 goal_phase="execute"（持久化），continue
          else：无 todos → 视为未产出计划 → force-loop / stalled（不变）
      elif goal_phase == "execute":
          current_todo = 第一个未完成 todo
          if current_todo is None（全部完成）：写 goal_phase="verify"，continue
          else：本轮完成该 todo（last_todos 已推进）→ continue 下一个
      elif goal_phase == "verify":
          achieved=false 且 verification 空 → 保持 verify（nudge 提示跑验证）
          achieved=true → _commit_terminal(done)
      # ---- 防空转（保留）----
      无 checkpoint → force-loop（GOAL_MAX_FORCE=3）→ stalled
```

### 6.3 下游只透传

- `_handle_event`（main.py:1841）对 goal 事件只透传 + 更新 `goal_todos`，不判断阶段。
- `/goal/status`（main.py:2438）返回 `goal_phase`（只读）。
- 前端 App.tsx / GoalCard 只展示阶段，不写阶段。
- `GoalModeMiddleware._overrides`（agents.py:2991）只把 `goal_phase` 与当前 todo 读入注入提示，不判断流转。

### 6.4 不允许做判断的层

- 前端（不写 `goal_phase`）。
- `_handle_event`（不判断阶段）。
- `_stream` 内部中间件（阶段只作为注入上下文读取）。

---

## 7. Owner 与职责边界

| 模块 | 负责 | 只消费 | 不得知道 |
|---|---|---|---|
| `goal_stream`（agents.py） | 阶段推进、回合计数、todo 注入、终态判定、原子落库 | session_store、_stream 事件 | 具体 UI 展示逻辑 |
| `CoworkerSummarizationMiddleware` | 上下文压缩（冻结不变） | state.messages | goal_phase 语义 |
| `GoalModeMiddleware` | 注入 goal 系统提示 + finalize_goal 工具（提示内容改为三阶段 + 渐进探索） | request.state | 阶段推进判定 |
| `TodoListMiddleware` | 暴露 write_todos（冻结不变） | graph state | goal_phase 语义 |
| `workspace.py` | `git_status`/`read_file` 输出截断（工具源头） | 无 | goal 状态机 |
| `main.py` 端点 | goal 生命周期命令、status 读面（补 `goal_phase`） | session_store | 阶段推进逻辑 |
| `frontend` | 展示阶段文案、reconcile 兼容 | SSE 事件、/goal/status | 阶段写回 |

---

## 8. 单一真源与复用要求

- **阶段枚举**：`goal_phase` 取值 `plan`/`execute`/`verify` 的正式常量（如 `_GOAL_PHASES = ("plan", "execute", "verify")`）放 `agents.py`（与 goal 逻辑同处）；`sessions.py` 只存字符串，不定义枚举。
- **截断阈值**：`GIT_MAX_PER_FILE_DIFF_CHARS`、`GIT_MAX_FILES`（500→50）、`GIT_MAX_DIFF_CHARS`（1M→100K）放 `workspace.py`；`READ_FILE_MAX_CHARS` 放 `workspace.py`（与 `MAX_COMMAND_OUTPUT_CHARS` 同处，收口现有散落）。
- **状态字段**：`goal_phase` 只在 `sessions.py` 定义与序列化（`Session`、`from_dict`、`to_dict`、`public`），`update_goal`/`commit_goal_end` 收口。
- **前端类型**：`GoalState.phase` 与 goal 事件类型只在 `types.ts` 定义。
- **现有重复定义收口**：
  - `read_preview`（workspace.py:1160）与 `read_text`（workspace.py:499）当前并存 —— 收口为 `read_file` 工具统一走 `read_preview` 语义；`read_preview` 的 `max_chars` 参数默认对齐 `READ_FILE_MAX_CHARS`。
  - `read_text` 仍被 write/replace/apply 的回读路径使用（workspace.py:554/593/637/911/999），保持不动。

---

## 9. 配置 / 持久化 / 外部接口设计

### 9.1 后端持久化

- `goal_phase` 字段：字符串，取值 `plan`/`execute`/`verify`；默认 `plan`。
- 持久化位置：`Session`（sessions.py:49-58 新增一行）。
- 序列化：`from_dict`（sessions.py:78 起）/ `to_dict`（sessions.py:105 起）/ `public` 补齐。
- 写入收口：`update_goal(goal_phase=...)`（sessions.py:203 新增参数）、`commit_goal_end`（sessions.py:242，终态提交时写死/保留当前阶段）。

### 9.2 外部接口

- `/goal/status` 响应 `goal` 对象新增 `goal_phase`（只读）。
- SSE 事件：**复用 `goal_round` 事件**新增 `phase` 字段（agents.py:4176），不新增事件类型。
- `goal_start` 事件（agents.py:4139）可带 `phase: "plan"`（可选，前端兼容）。
- `goal_force` 事件不变。

### 9.3 前端读面

- `GoalState` 新增 `phase?: 'plan' | 'execute' | 'verify'`（types.ts:720）。
- goal 事件类型 `goal_round` 新增可选 `phase`（types.ts:668）。
- `GoalCard.tsx` 展示阶段文案。
- 不允许前端配置截断阈值 / 阶段语义。

---

## 10. 状态 / 档位 / 模式定义

| 字段 | 用途 | 谁可写 | 谁可读 | 启用能力 | 挂起能力 | 风险 | 本轮范围 |
|---|---|---|---|---|---|---|---|
| `goal_phase=plan` | 计划阶段：渐进探索 + 制定 todos | `goal_stream` | 前端展示、中间件注入 | 只读探索（search_files/read_file）、write_todos | 写文件/改仓库（提示层约束） | agent 可能跳阶段 | 是 |
| `goal_phase=execute` | 执行阶段：逐 todo 推进 | `goal_stream` | 同上 | 全部工具 | 无 | 单 todo 过大 | 是 |
| `goal_phase=verify` | 验证阶段：跑验证后完成 | `goal_stream` | 同上 | 读、run_command 验证 | 大改（提示层约束） | agent 可能跳过验证 | 是（提示层） |

注：`goal_phase` 的能力挂起仅通过系统提示约束，不新增工具白名单机制（避免新增第二套判断；`PhaseToolGateMiddleware` 维持现有 phase（plan/build）语义，不与 `goal_phase` 混淆）。

---

## 11. 兼容 / 迁移 / 删除策略

- **旧会话无 `goal_phase`**：
  - `from_dict` 默认 `plan`。
  - `/goal/resume` 从 checkpoint 恢复时按当前 todo 状态推断：
    - 有未完成 todo → `execute`；
    - 全部完成且未 done → `verify`；
    - 无 todo → `plan`。
- **`git_status` 输出格式兼容**：保留 `path/added/removed/status/untracked` 等统计字段（消费方依赖不变），只截断 `diff` 字段内容。
- **删除面**：
  - `git_status` 从 goal 工具列表移除（agents.py:924 相关过滤点）。
  - `git_status` 工具定义本身**保留**（普通对话 / 只读子代理使用，`_READ_ONLY_TOOLS` 不变）。
  - 无旧字段删除；`goal_phase` 为纯新增。
- **旧测试**：`test_goal_loop.py`（10 用例，tests/test_goal_loop.py:167-395）保持通过；新增阶段流转用例。
- **旧注释/文档**：`goal_system_prompt` 文案更新后，相关注释同步。

---

## 12. 禁止事项

- 不改 `CoworkerSummarizationMiddleware` 的压缩常量与算法。
- 不新增第二条 goal 执行链 / 第二入口 / 第二 owner。
- 不新增散装阶段开关、不新增临时 compat/fallback 分支。
- 不把 `git_status` 从仓库里删除（普通对话/只读子代理仍可用）；只在 goal 模式工具列表移除。
- 不扩到 vLLM 并发治理、subagent、repo map、显式 verify 工具。
- 不改 600s 墙钟、不重写原子终态 `commit_goal_end`。
- 不为了「显示阶段」新增额外 SSE 事件类型（默认复用 `goal_round`）。
- 不把 `goal_phase` 与 `phase`（plan/build 工作模式）混用 —— 二者语义独立。
- 不改 `_READ_ONLY_TOOLS` / `PhaseToolGateMiddleware` 的既有工具过滤逻辑。

---

## 13. 影响面分析

### 13.1 必然影响

| 类型 | 文件 / 位置 | 说明 |
|---|---|---|
| 定义点 | `workspace.py:1337-1338` | 新增 `GIT_MAX_PER_FILE_DIFF_CHARS`；改 `GIT_MAX_FILES`、`GIT_MAX_DIFF_CHARS` |
| 定义点 | `workspace.py:37` 附近 | 新增 `READ_FILE_MAX_CHARS` |
| 定义点 | `sessions.py:49-58` | 新增 `goal_phase` 字段 |
| 赋值点 | `workspace.py:1436-1441` | `workspace_git_diff` 单文件 diff 截断 |
| 赋值点 | `agents.py:557-563` | `read_file` 工具改走 `read_preview` |
| 赋值点 | `main.py:1894-1932` | `chat_stream` goal 分支初始化 `goal_phase=plan` |
| 赋值点 | `main.py:2557` | `/goal/resume` 推断恢复 `goal_phase` |
| 赋值点 | `main.py:2491` | `/goal/start` 重置 `goal_phase=plan` |
| 赋值点 | `agents.py:4140-4349` | `goal_stream` 阶段推进逻辑 |
| 赋值点 | `agents.py:924` / goal 工具过滤 | 移除 `git_status` |
| 消费点 | `agents.py:2919` | `goal_system_prompt` 三阶段文案 |
| 消费点 | `agents.py:2991-3004` | `GoalModeMiddleware._overrides` 注入 `goal_phase` + 当前 todo |
| 消费点 | `agents.py:3926-3939` | `_stream` inputs 传入 `goal_phase` |
| 消费点 | `main.py:2438` | `/goal/status` 返回 `goal_phase` |
| 展示点 | `types.ts:668/720/736` | `phase` 字段 |
| 展示点 | `GoalCard.tsx` | 阶段文案 |
| 展示点 | `App.tsx:1264-1275` | `goal_round` 透传 `phase` |
| 测试点 | `tests/test_goal_loop.py` | 新增阶段流转 / 截断用例 |
| 文档点 | `docs/task/` | 本方案 + task list + 调研报告 |

### 13.2 可能影响

- `App.tsx` reconcile / 恢复逻辑（读 `goal_phase`，需容错缺失值）。
- `App.tsx:3034-3070` resume 处理（透传 `phase`）。
- `locales/*.json`（阶段文案，11 个文件）。
- `types.ts` goal 事件联合（`goal_round` 新增可选 `phase`）。

### 13.3 不应影响

- 普通对话（非 goal）的 `_stream` 行为、SSE 契约、前端消息流。
- 压缩中间件、`commit_goal_end`、`goal_done`/`goal_paused`/`goal_stopped` 终态语义。
- `git_status` 在普通对话 / 只读子代理中的行为（保留原工具，仅源头截断）。
- `read_text` 被 write/replace/apply 回读路径使用（workspace.py:554/593/637/911/999）。
- `_READ_ONLY_TOOLS` / `PhaseToolGateMiddleware` 的既有过滤逻辑。

### 13.4 伴随修正判断

- 本轮收口（同链路不规范）：
  - `read_text` 无二进制检测/无上限（收口为 `read_file` 走 `read_preview`）。
  - `read_preview` 与 `read_file` 双路径并存（收口统一）。
- **已删除（实现后验收补充）**：
  - `Workspace.read_text` 方法——read_file 改走 `read_preview` 后全仓无调用（§8 中「write/replace/apply 回读路径」实为 `Path.read_text()`，非该方法），已删除。
  - `GOAL_MARKER`（agents.py）、`GoalModeMiddleware._finalize_called`（agents.py，含 class docstring 不实描述修正）、`App.tsx` `handleGoalResumeEvent`（无 `WithChat` 变体）——均为无调用方死代码，已删除。
- 暂缓（记入删除面或暂缓项）：
  - goal 系统提示中 force-loop（`MAX_FORCE`）语义随阶段化的进一步优化。
  - vLLM 并发治理。

---

## 14. 分阶段落地策略

### Phase 1：工具源头截断（治本）

- 目标：`git_status` 单文件 diff ≤2K、文件数 ≤50、总量 ≤100K；`read_file` 复用 `read_preview` 语义 + 50K。
- 边界：只改 `workspace.py` 与 `read_file` 工具；不改 goal 循环。
- 完成标志：`git_status` 在 aicode 类大仓库返回 ≤ ~110KB；`read_file` 二进制/大文件返回受限结果；现有测试通过。
- 不能做：不新增第二套截断逻辑、不散落阈值。

### Phase 2：goal 系统提示改造（渐进式探索 + 三阶段指令）

- 目标：重写 `goal_system_prompt`；goal 工具列表移除 `git_status`。
- 边界：只改提示文本与工具列表过滤。
- 完成标志：goal 首轮不再调 `git_status`；提示含三阶段指令。
- 不能做：不新增工具白名单机制。

### Phase 3：goal_stream 阶段化执行（Plan → Execute → Verify）

- 目标：`goal_phase` 状态、阶段推进、每轮注入当前 todo、`goal_round` 事件带 `phase`。
- 边界：只在 `goal_stream` + `sessions.py` + `main.py` 初始化/恢复处改动。
- 完成标志：阶段正确流转；`/goal/status` 返回 `goal_phase`。
- 不能做：不新增第二 owner / 第二链。

### Phase 4：前端阶段展示 + 兼容

- 目标：`types.ts` 加 `phase`；GoalCard 阶段文案；App.tsx reconcile 容错。
- 边界：前端只展示，不写阶段。
- 完成标志：前端展示「计划中/执行中/验证中」；缺失 `goal_phase` 不报错。
- 不能做：不新增阶段写回。

### Phase 5：回归验收与文档收口

- 目标：跑通全部测试、手动验证大仓库 goal、同步文档。
- 边界：不改已冻结结构。
- 完成标志：验收标准全过；task list / current 同步。
- 不能做：不扩范围。

---

## 15. 验收标准

### 15.1 功能验收

- 大仓库（≥1000 改动文件）上 `git_status` 返回 ≤ ~110KB；单文件 diff 截断到 2K，超出只保留统计行。
- goal 首轮进入 `plan`，不调 `git_status`，产出 todos 后进入 `execute`；逐 todo 推进；最后一个 todo 后进入 `verify`；`finalize_goal(achieved=true)` 后原子落库 `done`。
- `read_file` 读二进制返回受限结果（不抛错、不塞入）；读大文本截断到 50K 并标记。

### 15.2 边界验收

- `goal_phase` 缺失时（旧会话/恢复）不报错且合理推断。
- `paused`/`stopped`/`timeout`/`stalled` 终态语义不变。
- `goal_phase` 与 `phase`（plan/build）不混用。

### 15.3 回归验收

- `backend/tests/test_goal_loop.py` 10 用例全过；新增阶段流转用例全过。
- 前端 `tsc --noEmit` 与 `vite build` 通过。
- 普通对话手动回归：SSE 事件、消息流、工具调用无变化。

### 15.4 日志 / trace / diagnostics 证据

- `goal_round` 事件带 `phase`。
- `/goal/status` 返回 `goal_phase`。
- 大仓库 goal 运行日志中不再出现 git_status 1MB 注入。

### 15.5 性能 / 时延指标

- goal 首轮上下文不再跳 300k（titlebar 预算平稳增长）。
- 不再因 git_status 大 diff 触发 prefill 过载。

### 15.6 不应发生的回归

- 普通对话不受影响。
- 压缩中间件、原子终态、600s 墙钟、pause/resume/delete/edit 行为不变。
- 刷新后 goal 不复活（上轮修复保持）。

---

## 16. Bugfix 方案审查

- 本方案含对「`git_status` 1MB 注入导致上下文爆炸」的修复：
  1. 正式 owner：`workspace.py`（工具源头输出）。
  2. 原始错误链路点：`workspace_git_diff`（workspace.py:1436-1441）返回完整 per-file diff，无单文件上限。
  3. 能否直接修原点：**能** —— 在 `workspace_git_diff` 内对每文件 `diff` 截断即可，无需外围补丁。
  4. 当前修法是否为外围包裹：否，直接改原点输出。
  5. 是否新增状态/字段/helper：新增 `GIT_MAX_PER_FILE_DIFF_CHARS` 正式常量（长期结构，非补丁）；新增 `goal_phase`（执行模型正式结构）。
  6. 是否长期结构：是，与 `MAX_COMMAND_OUTPUT_CHARS` 同风格的正式截断常量。
- 结论：满足原点修复要求，可进入实现。

---

## 17. Codex 执行约束

- 开始前：复述本方案结论与调研报告证据。
- 必须先全局搜索：正式入口（`goal_stream`、`/goal/*` 端点）、owner、上下游调用链、`git_status`/`read_file`/`read_preview` 全部引用点、`goal_phase` 潜在冲突点（现有 `phase`）。
- 必须先列影响面与删除面（见第 13 节）。
- 不允许：凭感觉补丁式修改、新增第二正式链/第二 owner/compat/fallback、只改代码不改测试/文档。
- 完成后必须交代：失败测试或失败证据、直接相关回归、真实链路验证、删除内容、文档同步、当前风险。

---

## 18. 后续暂缓项

- subagent 上下文隔离（Claude Code 式）。
- repo map / 符号索引（Aider 式）。
- 显式 verify 工具 / LangGraph evaluator-optimizer 验证门。
- vLLM 多会话并发治理与队列/限流。
- force-loop（`MAX_FORCE`）语义随阶段化的进一步优化。
- 以上暂缓项不得混入本轮 task list。

# Coworker Goal 能力调研报告

更新时间：2026-08-21
状态：当前生效（作为 Goal 能力重构的输入证据与行业对标记录）
适用范围：`/Users/leon/Documents/CodeProjects/coworker/docs/task/` 下的 Goal 能力专项调研

说明：

- 本文档记录对 Coworker 当前 Goal 执行能力的完整审计结论，以及对照行业主流（Codex / Claude Code / opencode / LangGraph / Cline / Aider）的设计差距分析。
- 本文档是输入证据与对标记录，不替代专项开发设计方案；正式设计边界与落点见《Goal 能力重构开发设计方案.md》。
- 调研基于真实代码（行号以 2026-08-21 工作区为准）与真实运行证据（`runtime_checkpoints.sqlite`、`app.log`、会话 `1fd5f8bf-8d9f-4d4e-b6c7-1c7045f879c9`）。

---

## 0. 输入真源 / 当前证据

### 0.1 当前代码（精确定位）

| 文件 | 行号 | 内容 |
|---|---|---|
| `backend/coworker/agents.py` | 4031 | `goal_stream`（goal 主循环） |
| `backend/coworker/agents.py` | 2959 | `GoalModeMiddleware` |
| `backend/coworker/agents.py` | 2919 | `goal_system_prompt`（goal 系统提示） |
| `backend/coworker/agents.py` | 2898 | `_goal_tools` / `finalize_goal` |
| `backend/coworker/agents.py` | 924 | 工具列表（含 `git_status`） |
| `backend/coworker/agents.py` | 679 | `git_status` 工具 |
| `backend/coworker/agents.py` | 557 | `read_file` 工具 |
| `backend/coworker/agents.py` | 3862 | `_stream`（单轮 graph 执行） |
| `backend/coworker/agents.py` | 2306 | `CoworkerSummarizationMiddleware` |
| `backend/coworker/agents.py` | 3220-3291 | `build_coworker_agent_graph` / 中间件链 / `TodoListMiddleware` |
| `backend/coworker/agents.py` | 4380-4427 | `_handle_message_chunk` 中 finalize_goal / write_todos 事件 |
| `backend/coworker/workspace.py` | 1337 | `GIT_MAX_FILES = 500` |
| `backend/coworker/workspace.py` | 1338 | `GIT_MAX_DIFF_CHARS = 1_000_000` |
| `backend/coworker/workspace.py` | 1389 | `workspace_git_diff`（git diff 全量组装） |
| `backend/coworker/workspace.py` | 499 | `read_text`（无检测无上限） |
| `backend/coworker/workspace.py` | 1160 | `read_preview`（binary 检测 + max_chars=100_000） |
| `backend/coworker/workspace.py` | 1146 | `is_text_file`（mime/suffix 白名单） |
| `backend/coworker/sessions.py` | 49-58 | `Session.goal_*` 字段 |
| `backend/coworker/sessions.py` | 203 | `update_goal` |
| `backend/coworker/sessions.py` | 242 | `commit_goal_end`（原子终态） |
| `backend/main.py` | 1894-1932 | `chat_stream` goal 分支 |
| `backend/main.py` | 2438 | `/goal/status` |
| `backend/main.py` | 2491 | `/goal/start` |
| `backend/main.py` | 2557 | `/goal/resume` |
| `backend/main.py` | 2473 | `/goal/pause` |
| `backend/main.py` | 2527 | `/goal/delete` |
| `backend/main.py` | 2482 | `/goal/edit` |
| `backend/main.py` | 2416 | `/goal/stop` |
| `frontend/src/types.ts` | 667-668 | goal 事件类型（`goal_round` 等） |
| `frontend/src/types.ts` | 720-735 | `GoalState` |
| `frontend/src/types.ts` | 736 | `GoalStatusResponse` |
| `frontend/src/App.tsx` | 1264-1275 | `goal_round` / `goal_checkpoint` 事件处理 |
| `frontend/src/App.tsx` | 3034-3070 | resume 事件处理 |
| `frontend/src/components/GoalCard.tsx` | — | goal 卡片渲染 |
| `frontend/src/locales/*.json` | — | 11 个语言文件 |

### 0.2 关键代码事实（逐条核对）

**`workspace_git_diff`（workspace.py:1389）当前逻辑：**
1. `git diff HEAD --numstat` 拿文件统计 → 循环塞入 `files` 列表，`len(files) >= GIT_MAX_FILES(500)` 后跳过（`skipped += 1`）。
2. `git diff HEAD` 拿全量 diff 文本 → `if len(diff_text) > GIT_MAX_DIFF_CHARS(1_000_000)` 截断到 1MB。
3. `_parse_git_diff_sections` 把整段 diff 按文件切分，`file_entry["diff"] = body` —— **每个文件的 diff 是完整 unified diff，无单文件上限**。
4. 结论：单个大文件 diff 可达到 1MB 总量内的任意大小（实测 525,527 字符）。

**`git_status` 工具（agents.py:679）返回结构：**
```json
{ "git": true, "workspace": "...", "branch": "dev",
  "status": [80 行 git status --short],
  "status_truncated": bool,
  "diff_files": [ {path, added, removed, binary, diff:<完整diff>} × 500 ],
  "untracked": [...], "truncated_diff": bool }
```
`diff_files` 是体重灾区：500 个文件 × 完整 diff。

**`read_file` 工具（agents.py:557-563）：**
```python
@tool(args_schema=ReadFileArgs)
def read_file(file_path: str) -> str:
    try:
        return workspace.read_text(file_path)   # 无二进制检测、无大小上限
    except Exception as exc:
        return _error_result(exc, "read_file")
```
- `read_text`（workspace.py:499）：`target.read_text(encoding="utf-8")`，无 `errors="replace"`。
- 二进制（ppt/图片/视频）→ `UnicodeDecodeError` → `_error_result`（返回错误，不会把二进制塞进上下文；但体验差、无法引导模型用文件面板）。
- 大文本 → 整个塞入，无上限（与 git_status 同类风险）。

**`read_preview`（workspace.py:1160）：**
```python
def read_preview(self, file_path: str, max_chars: int = 100_000) -> dict[str, Any]:
    target = self.resolve_read_path(file_path)
    if not target.is_file(): raise ValueError(...)
    if not self.is_text_file(target):
        return {"content": None, "binary": True, "size": target.stat().st_size}
    content = target.read_text(encoding="utf-8", errors="replace")
    self._record_fingerprint(target)
    truncated = len(content) > max_chars
    if truncated: content = content[:max_chars]
    return {"content": content, "binary": False, "size": target.stat().st_size, "truncated": truncated}
```
- 已有 `is_text_file`（mime/suffix 白名单）+ `max_chars=100_000`。
- 当前**只被前端文件预览使用**（main.py:3530），未接到 agent 的 `read_file`。

**`goal_system_prompt`（agents.py:2919-2956）当前文案要点：**
- `mode_line`（autonomous/supervised/guarded 三选一）。
- `GOAL: {goal_text}`。
- 5 条指令：write_todos → research/edit/run commands → finalize_goal(false) → finalize_goal(true) → plain text 不算完成。
- **「Research, edit files and run workspace commands」是引导 agent 主动探索的源头**，叠加 `git_status` 在工具列表，agent 第一轮即全量 dump。

**`goal_stream`（agents.py:4031）当前主循环：**
```
yield goal_start
while True:
    round_no += 1
    读 session_state（goal_just_edited / goal_stopped / goal_max_rounds）
    if goal_max_rounds>0 and round_no>goal_max_rounds: → stopped(max_rounds)
    yield goal_round
    try:
        async with asyncio.timeout(600):   # 5 分钟墙钟
            async for event in self._stream(..., goal_mode=True, goal_text=goal_text, goal_continue=round>1):
                goal_checkpoint → last_checkpoint
                todos → last_todos
                approval_required → round_had_interrupt
                delta / done → accum_content/accum_parts
                yield event
    except TimeoutError → stopped(timeout)
    except CancelledError → stopped(stopped)
    轮结束：
        achieved=true → _commit_terminal(done) → goal_done
        paused → _commit_terminal(paused) → goal_paused
        无 checkpoint → force-loop（最多 GOAL_MAX_FORCE=3）→ stalled
    continue
```

**goal 事件全集（agents.py 内 yield）：**
`goal_start` / `goal_round` / `goal_edited` / `goal_checkpoint` / `goal_done` / `goal_paused` / `goal_force` / `todos` / `goal_stream_id` / `goal_system` / `goal_attached`（后三者部分由其他路径发出）。

**压缩中间件关键常量（agents.py:1895-1912）：**
```
CONTEXT_SAFETY_FACTOR = 0.75
CHARS_PER_TOKEN = 3.5
KEEP_RECENT_TOKENS = 8_000        # opencode DEFAULT_KEEP_TOKENS=8000
SUMMARY_OUTPUT_TOKENS = 4_096     # opencode SUMMARY_OUTPUT_TOKENS=4096
TOOL_OUTPUT_MAX_CHARS = 2_000     # opencode TOOL_OUTPUT_MAX_CHARS=2000
SUMMARY_INPUT_MAX_TOKENS = 32_000
TRUNCATE_CHARS_PER_TOKEN = 1.5
```
**结论：压缩/摘要机制已对齐 opencode，不是本次问题。**

**`run_command` 输出上限：** `MAX_COMMAND_OUTPUT_CHARS = 12_000`（workspace.py:37），已做源头截断（workspace.py:798-801）——是正确做法的参照物。

### 0.3 运行证据（会话 `1fd5f8bf-8d9f-4d4e-b6c7-1c7045f879c9`）

| 时间 | 事件 |
|---|---|
| 08:52:04.670 | `git_status` ToolMessage **1,052,155 字节**进入 checkpoint（500 个 diff_files，单文件 diff 最高 525,527 字符） |
| 08:52:04.765 | 压缩触发（`context_compact_count=1`），state 缩至 1,745 字节 |
| 08:52:12.363 | 模型调用 #3 开始（上下文仅 ~15KB，**1MB 已被压缩拦截，未直接喂给模型**） |
| 08:52:14.101 | httpx 收到 200 响应头；SSE body 停滞 |
| 08:52:14 → 09:01:59 | vLLM 响应流 ~600s 无 chunk，socket 进入 CLOSE_WAIT |
| 09:01:59 | round 墙钟超时 → `goal_stopped=True` 原子落库（上轮修复生效，刷新不复活） |

**checkpoint 库**同时存在 4 个 goal 线程：`1fd5f8bf`（18 checkpoints）、`aa0ca1b0`（168）、`b1a8cc3b`（32）、`c076b0c3`（66），全部打到同一 vLLM（47.76.63.128:1111）。

**关键结论**：
- 「上下文 0→300k 跳变」= `git_status` 一次返回 1MB（真实存在，不合理）。
- 「600s 卡死」= 模型调用 #3 的 vLLM 响应流停滞（大概率多会话并发繁忙）；模型 #3 实际只带 ~15KB 上下文。
- **两者是不同问题**：本次只修前者（git_status 源头截断）及由此暴露的执行模型缺陷。

---

## 1. 当前 Goal 执行模型全景

### 1.1 结构图

```
用户输入 goal_text
  → chat_stream（main.py:1894 goal 分支）
  │    重置 goal_* 状态（goal_text, goal_todos=[], goal_stopped=False,
  │    goal_interrupted=False, goal_force_count=0, goal_stream_id=新uuid, goal_max_rounds）
  │    注册 _goal_cancel_events[session_id]
  → runtime.goal_stream(messages, session_id, language, work_mode, autonomy,
                        goal_text, goal_continue_first=False, _cancel_event,
                        goal_stream_id, assistant_message_id)
      ├─ yield goal_start
      ├─ while True:
      │    round_no += 1
      │    读 session_state（goal_just_edited / goal_stopped / goal_max_rounds）
      │    if 超 max_rounds → _commit_terminal(stopped) → goal_done(max_rounds)
      │    yield goal_round
      │    async with asyncio.timeout(600):
      │        async for event in _stream(goal_mode=True, goal_text, goal_continue=round>1):
      │            goal_checkpoint → last_checkpoint
      │            todos → last_todos
      │            approval_required → round_had_interrupt
      │            delta / done → accum_content / accum_parts
      │            yield event
      │    轮结束分支：
      │      - achieved=true → _commit_terminal(done) → goal_done
      │      - paused → _commit_terminal(paused) → goal_paused
      │      - goal_stopped → _commit_terminal(stopped) → goal_done(stopped)
      │      - TimeoutError → _commit_terminal(stopped) → goal_done(timeout)
      │      - CancelledError → _commit_terminal(stopped) → goal_done(stopped)
      │      - 无 checkpoint → force-loop（GOAL_MAX_FORCE=3 次 nudge）→ stalled
      └─ 终止后 /goal/status 映射 done/stopped/paused/active
```

### 1.2 机制对照表

| 机制 | 位置 | 说明 | 评价 |
|---|---|---|---|
| 回合边界 | `goal_stream` 每轮 `_stream` | 轮 = 一次 astream，无子任务语义 | 待改 |
| 完成信号 | `finalize_goal`（agents.py:2898） | agent 自觉调用 | 保留 |
| 任务清单 | `write_todos` + `todos` 事件 | agent 自维护，写入 `goal_todos` | 保留 |
| 阶段 | 无 | 只有一个裸循环 | 待加 |
| 上下文压缩 | `CoworkerSummarizationMiddleware` | 对齐 opencode 三常量 | 冻结 |
| 工具源头截断 | `run_command` 12K；`read_preview` 100K；`git_status` 无单文件上限 | 不一致 | 待修 |
| 仓库理解 | `goal_system_prompt` 引导 | 引导 git_status 全量 | 待改 |
| 原子终态 | `commit_goal_end`（上轮修复） | 已验证有效 | 冻结 |
| 墙钟 | `asyncio.timeout(600)` | 5 分钟/轮 | 冻结 |
| force-loop | `GOAL_MAX_FORCE=3` | 无 checkpoint 时 nudge | 冻结 |

### 1.3 当前状态字段（sessions.py:49-58）

`goal_text` / `goal_done` / `goal_paused` / `goal_todos` / `goal_max_rounds` / `goal_force_count` / `goal_stopped` / `goal_just_edited` / `goal_stream_id` / `goal_interrupted`。

**无 `goal_phase` / `goal_round` / `goal_plan` 字段。**

---

## 2. 已确认问题

### 问题 A（P0，根因）：`git_status` 工具源头无单文件截断 → 一次塞入 1MB+ 上下文

- 代码证据：`workspace_git_diff`（workspace.py:1389）第 1436-1441 行 `file_entry["diff"] = body`，`body` 为 `_parse_git_diff_sections` 切出的完整 unified diff。
- 阈值：`GIT_MAX_FILES=500`、`GIT_MAX_DIFF_CHARS=1_000_000`（**整库总量**上限，无单文件上限）。
- 实测：aicode（1109 改动文件，2.7MB diff）→ `git_status` 返回 1,052,155 字节；单文件 diff 最高 525,527 字符。
- 影响：上下文预算 0→~300k tokens → 压缩 → vLLM prefill/generation 极慢 → 长时等待。
- 主流参照：opencode `TOOL_OUTPUT_MAX_CHARS=2000`、Claude Code 工具输出源头截断；CW 仅 `run_command`（12K）达标。

### 问题 B（P0，行为）：goal 模式引导 agent 主动全量探索仓库

- 代码证据：`goal_system_prompt`（agents.py:2919-2956）「Research, edit files and run workspace commands」+ `git_status` 在工具列表（agents.py:924）。
- 实测：agent 第一轮即调 `git_status`。
- 主流参照：Claude Code / Aider / opencode 均**按需渐进探索**（search/glob 定位、subagent 隔离、repo map），无一初始全量注入。

### 问题 C（P1，行为）：回合无子任务语义，上下文在子任务边界不被治理

- 代码证据：`goal_stream` 每轮 = 一次 `_stream`（一次 graph.astream），agent 可能在一个超长轮内无限跑工具。
- 主流参照：opencode 按 turn 裁剪；LangGraph checkpointer + interrupt 断点；Cline Plan/Act 显式双阶段。

### 问题 D（P1，读面）：`read_file` 工具无二进制检测、无大小上限

- 代码证据：`read_file`（agents.py:557-563）→ `read_text`（workspace.py:499）。
- 二进制（ppt/图片/视频）→ `UnicodeDecodeError` → `_error_result`（安全但不友好，无法引导模型用文件面板）。
- 大文本 → 整个塞入（与 git_status 同类风险）。
- `read_preview`（workspace.py:1160）已有正确语义但只被前端使用。

### 问题 E（P2，观察）：vLLM 多会话并发繁忙

- checkpoint 库同时 4 个 goal 线程打到同一 vLLM；模型调用 #3 SSE 停滞 ~600s。
- 说明：`git_status` 截断能消除单次巨大 prefill；vLLM 并发是独立话题（记入非目标）。

---

## 3. 行业主流做法对标

### 3.1 OpenAI Codex (cli)

- 执行模型：turn→step 两级循环（`codex-rs/core/src/session/turn.rs`）；每个用户消息 = 一个 turn，turn 内循环「sampling → 执行 tool → 再 sampling」。
- 长期目标：`/goal` Goal 模式，目标即首个 prompt 兼完成标准，支持 pause/resume/edit/clear（learn.chatgpt.com/codex/long-running-work）。
- 计划：模型可在输出中提议 plan（`PlanItem`/`ProposedPlanSegment`），支持 `/plan` 计划模式，replan 内建于主循环。
- 上下文：`model_auto_compact_token_limit` 触发自动压缩；压缩分本地摘要 / 远程 / token-budget 换新窗口三类，带 PreCompact/PostCompact hooks。
- 关键参考：**goal 模式下 plan 是一等状态，模型可重写计划；自动压缩是预算驱动而非被动兜底。**

### 3.2 Claude Code (Anthropic)

- 子代理隔离：Explore/Plan/General-purpose 子代理各自独立 context window，只回传摘要（官方量化：子代理读 6.1k tokens 文件，主会话只收 420 tokens）。
- 长任务：plan mode（只读研究 → 出计划）+ `/compact` 结构化摘要（保留请求意图 / 关键代码 / 错误 / 待办，清掉工具输出与推理）。
- 启动注入：只注入 system prompt + CLAUDE.md（建议 <200 行）+ skill 描述，MCP 工具 schema 延迟按需加载。
- 任务清单：agent 用 TodoWrite 工具自维护。
- 关键参考：**不预注入仓库概览；子代理隔离大上下文；todo 由 agent 自维护。**

### 3.3 opencode

- 上下文预算：`KEEP_TOKENS=8000`、`TOOL_OUTPUT_MAX_CHARS=2000`、`SUMMARY_OUTPUT_TOKENS=4096`、`DEFAULT_BUFFER=20000`（`packages/opencode/src/session/compaction.ts`）。
- 回合：按 user turn 划分；溢出时保留最近 N 个 turn，预算不够按 turn 内 `splitTurn` 截断。
- 摘要模板：强制输出 Objective / Completed / Active / Blocked / Next Move / Relevant Files —— 即把「目标→子任务→进度」固化为上下文结构。
- `prune`：清旧工具输出（PRUNE_MINIMUM=20k / PRUNE_PROTECT=40k）。
- 关键参考：**CW 压缩三常量已对齐 opencode；缺的是工具源头截断与结构化摘要模板。**

### 3.4 LangGraph / LangChain

- 官方推荐 `create_agent` 状态机：`llm→tool→llm` 循环 + `checkpointer` 按 `thread_id` 持久化 graph state（checkpoint/恢复游标）。
- HITL：`interrupt()` + `Command(resume=...)`；`Command(goto/update)` 动态改路由与状态。
- 防爆炸：`SummarizationMiddleware(model, trigger=("tokens",4000), keep=("messages",20))`，或 trim/RemoveMessage。
- 规划与执行分离：Orchestrator-worker（`Send` API 动态派发）、evaluator-optimizer（生成→评估→再生成，即验证步骤）。
- 关键参考：**checkpointer+interrupt 做可恢复断点；验证是显式环节（evaluator-optimizer）。**

### 3.5 Cline / Aider

- **Cline**：显式 Plan/Act 双模式——先探索 + 提问 + 出策略，批准后执行；编辑被 checkpoint 跟踪可 undo；Kanban 每张卡片独立 worktree + auto-commit + 依赖链（README）。
- **Aider**：交互回合制；理解大代码库靠 **repo map**——tree-sitter 建全仓库符号图，按图排序选出最相关片段注入（`--map-tokens` 默认 1k tokens），文件再按需读取。
- 关键参考：**Plan/Act 阶段分离；repo map 的「按需渐进探索」。**

---

## 4. 主流共识总结

| 维度 | 主流共识 | 参考系统 | CW 现状 | 差距 |
|---|---|---|---|---|
| 目标组织 | Plan → Execute → Verify 分阶段；plan 是一等状态 | Cline Plan/Act、Claude plan mode、Codex `/plan`、opencode Next Move | 无阶段裸循环 | 大 |
| 回合边界 | turn = 用户消息 / 子任务；checkpoint + interrupt 断点 | opencode、LangGraph | 轮 = 一次 astream，无子任务语义 | 中 |
| 上下文 | 工具源头截断（≤2000 字符）+ turn 级压缩 + 结构化摘要模板 | opencode、Claude | 压缩已对齐；工具源头未对齐 | 小但致命 |
| 仓库理解 | 按需渐进探索，无一全量注入 | Aider repo map、Claude subagent | 提示词引导 git_status 全量 | 大 |
| 验证 | evaluator-optimizer / 验证步骤 | LangGraph、Cline | 依赖 agent 自觉 | 中 |
| 任务清单 | agent 自维护（TodoWrite / 摘要模板） | Claude、opencode | `write_todos` 已有 | 小 |

---

## 5. 建议方向（供设计方案引用）

1. **工具源头截断（治本）**：`git_status` 单文件 diff 上限（2K）+ 文件数收紧（50）+ 总量收紧（100K）；`read_file` 走 `read_preview` 语义（binary 检测 + 50K 上限）。与 opencode `TOOL_OUTPUT_MAX_CHARS=2000`、`run_command` 12K 同风格。
2. **阶段化执行模型**：Plan → Execute → Verify 三阶段；回合边界 = 一个 todo 子任务；`goal_phase` 随 checkpoint 持久化。
3. **仓库探索引导**：goal 系统提示改为渐进式（search_files/read_file 按目标所需）；goal 模式从工具列表移除 `git_status`。
4. **验证环节**：靠提示词引导 agent 自觉跑验证（轻量，本次采用），后续可演进为显式 verify 工具（记入暂缓项）。
5. **保留**：600s 墙钟、`commit_goal_end` 原子终态、压缩中间件（已对齐 opencode）、pause/resume/delete/edit、force-loop 兜底。
6. **非目标**：vLLM 多会话并发治理（独立话题）、subagent 隔离、repo map 符号索引。

---

## 6. 附：本次审计运行证据摘录

- 会话 `1fd5f8bf-8d9f-4d4e-b6c7-1c7045f879c9`：
  - 08:52:04.670 checkpoint：`git_status` ToolMessage 1,052,155 字节进入 state。
  - 08:52:04.765 压缩触发（`context_compact_count=1`），state 缩至 1,745 字节。
  - 08:52:12.363 模型调用 #3 开始（上下文仅 ~15KB）；08:52:14.101 httpx 200；SSE body 停滞。
  - 09:01:59 墙钟超时 → `goal_stopped=True` 原子落库（上轮修复生效）。
- 结论：**300k 跳变 = git_status 一次塞入 1MB（真实存在）；600s 卡死 = vLLM 响应流停滞（大概率多会话并发）**。两者是不同问题，本次只修前者及相关执行模型。

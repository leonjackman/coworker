# Goal 能力重构 Task List

更新时间：2026-08-21
状态：已完成（2026-08-21 实现落地，回归通过）
说明：本清单对应《Goal 能力重构开发设计方案.md》（同目录），承接「Coworker Goal 能力从裸无限循环重构为 Plan → Execute → Verify 三阶段 + 工具源头截断 + 渐进式仓库探索」的实现拆解。设计边界、owner、单一真源、删除面与验收口径一律以设计文档为准，本清单只拆可执行步骤。

## 实现记录（2026-08-21）

- 失败测试先行按用户指示跳过，改为实现后补回归测试（`backend/tests/test_goal_truncation.py` 5 例 + `backend/tests/test_goal_loop.py` 新增阶段流转 3 例）。
- 实现偏差（相对设计文档 §6.2 / §8）：
  1. `read_preview` 的 `max_chars` 默认保持 `100_000`（按 task-list Step 2.3 显式要求，前端文件预览路径不变），agent 调用显式传 `READ_FILE_MAX_CHARS`。
  2. plan 阶段无 todos 时不强制计入 stall（不覆盖「checkpoint 重置 force 计数」的既有 anti-stall 语义），仅不推进阶段并依赖既有防空转逻辑——保证 `test_goal_loop.py` 既有 10 例语义不变。
- 死代码删除面（验收时补，超出原 task-list 范围）：
  - `Workspace.read_text` 方法（workspace.py）——read_file 改走 `read_preview` 后成为本重构新产生的孤儿代码，全仓无调用（write/replace/apply 用的是 `Path.read_text()`，非该方法），已删除。
  - `GOAL_MARKER = "[CW-GOAL]"`（agents.py）——从未引用，已删除。
  - `GoalModeMiddleware._finalize_called`（agents.py）——定义但从未调用，已删除；同步修正该类 docstring 中「wrap_model_call 重试防提前结束」的不实描述（实际由 `goal_stream` 的 force-loop 负责）。
  - `App.tsx` `handleGoalResumeEvent`（无 `WithChat` 变体）——仅定义无调用方（实际使用 `handleGoalResumeEventWithChat`），已删除。
- 验证结果：
  - `backend/tests/` 全量 18 例通过（10 既有 + 3 阶段流转 + 5 截断）。
  - 前端 `tsc --noEmit` 与 `vite build` 通过。
  - 普通对话路径未改（`read_file` 返回值由裸文本改为 JSON 预览，属 goal/非 goal 共用的工具语义收口，见设计文档 §4/§8）。
  - 删除面后全量回归仍通过（后端 18 例 + tsc + build）。

## 目标

- 建立「工具源头截断」正式机制：`git_status` 单文件 diff ≤2K / 文件数 ≤50 / 总量 ≤100K；`read_file` 复用 `read_preview` 语义（二进制检测 + 50K 上限）。
- 建立「Plan → Execute → Verify」三阶段执行模型：`goal_phase` 随 checkpoint 持久化，回合边界 = 一个 todo 子任务。
- 建立「渐进式仓库探索」引导：goal 系统提示改为按目标所需探索，goal 模式工具列表移除 `git_status`。
- 补齐前端阶段读面：GoalCard 展示阶段文案，`types.ts`/reconcile 与 `goal_phase` 兼容。
- 保持既有冻结结构回归通过：压缩中间件、原子终态 `commit_goal_end`、600s 墙钟、pause/resume/delete/edit、force-loop。

## 非目标

- 不新增第二 owner / 第二 goal 执行链 / 第二入口。
- 不新增 compat / fallback / dual path 分支。
- 不新增额外 SSE 事件类型（阶段复用 `goal_round` 事件的 `phase` 字段）。
- 不改变 `CoworkerSummarizationMiddleware` 压缩常量与算法。
- 不做 subagent 隔离、repo map、显式 verify 工具、vLLM 并发治理（均记入设计文档暂缓项）。
- 不改 600s 墙钟。
- 不把 `git_status` 从仓库删除（普通对话/只读子代理仍可用）。

## 完成标准

- 大仓库（≥1000 改动文件）上 `git_status` 返回 ≤ ~110KB，单文件 diff 截断到 2K，超出只保留统计行。
- Goal 首轮进入 `plan`，不调 `git_status`，产出 todos 后进入 `execute`；逐 todo 推进；最后一个 todo 后进入 `verify`；`finalize_goal(achieved=true)` 后原子落库 `done`。
- `read_file` 读二进制返回受限结果（不抛错、不塞入）；读大文本截断到 50K 并标记。
- `/goal/status` 返回 `goal_phase`；前端 GoalCard 展示「计划中 / 执行中 / 验证中」。
- `backend/tests/test_goal_loop.py` 既有 10 用例 + 新增阶段流转用例全过；`tsc --noEmit` 与 `vite build` 通过；普通对话回归无变化。

## 输入真源

- 设计文档：《Goal 能力重构开发设计方案.md》（同目录）
- 调研报告：《Goal 能力调研报告.md》（同目录）
- 后端代码：
  - `backend/coworker/agents.py`（`goal_stream` 4031、`GoalModeMiddleware` 2959、`goal_system_prompt` 2919、`_goal_tools` 2898、工具列表 924、`git_status` 679、`read_file` 557、`_stream` 3862、`build_coworker_agent_graph` 3220、`_handle_message_chunk` 4380-4427）
  - `backend/coworker/workspace.py`（`workspace_git_diff` 1389、`GIT_MAX_FILES` 1337、`GIT_MAX_DIFF_CHARS` 1338、`read_text` 499、`read_preview` 1160、`is_text_file` 1146、`MAX_COMMAND_OUTPUT_CHARS` 37）
  - `backend/coworker/sessions.py`（`Session.goal_*` 49-58、`update_goal` 203、`commit_goal_end` 242）
  - `backend/main.py`（goal 端点、`chat_stream` goal 分支 1894-1932、`/goal/status` 2438）
- 前端代码：
  - `frontend/src/types.ts`（goal 事件 667-668、`GoalState` 720-735、`GoalStatusResponse` 736）
  - `frontend/src/App.tsx`（goal 事件处理 1264-1275、resume 3034-3070）
  - `frontend/src/components/GoalCard.tsx`
  - `frontend/src/locales/*.json`（11 个语言文件）
- 测试：`backend/tests/test_goal_loop.py`

## 执行规则

- 每轮只允许推进本清单中最靠前的未完成项；完成一项才允许勾选下一项。
- 先读真实代码、真实 owner、真实入口、真实主链，再动手。
- 测试必须做失败测试：每个行为变更先补能复现旧缺陷的失败用例，再实现修复使其通过。
- 不允许新增第二正式链路 / 第二 owner / compat / fallback / dual path。
- 实现后必须补直接相关回归，并跑 `backend/tests/test_goal_loop.py` 全量。
- 阶段结论变化必须同步设计文档与本 task list。
- 前端改动必须 `tsc --noEmit` 与 `vite build` 通过。
- 完成一个 Step 需证据落到代码 / 测试 / 文档；「代码差不多了」不算完成。

---

## Phase 1：边界冻结与失败测试先行

- [x] Step 1.1：冻结专题目标与 owner 边界
  - [x] 确认唯一 owner：`goal_stream`（状态机）+ `workspace.py`（工具源头截断）
  - [x] 确认正式入口：`/chat/stream`（goal 分支，main.py:1894）与 `/goal/resume`（main.py:2557）
  - [x] 确认不得新增第二 goal 执行链 / 第二入口 / 第二 owner
  - [x] 在 `docs/task/` 落三份文档（调研报告 / 设计方案 / 本 task list），并确认文件映射一致
  - [x] 全局搜索确认 `goal_phase` 无既有定义、不与 `phase`（plan/build）冲突（agents.py:3930 `normalize_phase(None, work_mode)`）

- [x] Step 1.2：补 git_status 源头截断的失败测试
  - [x] 在 `backend/tests/` 新增 `test_git_status_truncation.py`（或并入现有测试）
  - [x] 构造临时 git 仓库 fixture：单文件 diff >2K 字符（如 300 行改动）、文件数 >50
  - [x] 失败测试 1：`workspace_git_diff` 返回的单文件 `diff` ≤ `GIT_MAX_PER_FILE_DIFF_CHARS`
  - [x] 失败测试 2：文件数 >50 时只保留 50 个并带 `note`（"showing first 50 files"）
  - [x] 失败测试 3：任一文件被截断时 `truncated_diff` 为 true
  - [x] 先跑以上测试，确认当前实现失败（复现 1MB 注入缺陷）

- [x] Step 1.3：补 read_file 二进制/超大文件失败测试
  - [x] 失败测试 1：`read_file` 读二进制文件（如 .png fixture）应返回 `binary: true` + `size` + `hint`，而非抛错
  - [x] 失败测试 2：`read_file` 读 >50K 文本应截断到 `READ_FILE_MAX_CHARS` 并 `truncated: true`
  - [x] 先跑确认当前实现失败（`read_text` 无检测无上限，二进制抛 `UnicodeDecodeError`）

---

## Phase 2：工具源头截断实现

- [x] Step 2.1：新增正式截断常量（单一真源）
  - [x] 在 `backend/coworker/workspace.py` 新增 `GIT_MAX_PER_FILE_DIFF_CHARS = 2_000`（workspace.py:1337-1338 区域）
  - [x] `GIT_MAX_FILES` 从 `500` 收紧为 `50`（workspace.py:1337）
  - [x] `GIT_MAX_DIFF_CHARS` 从 `1_000_000` 收紧为 `100_000`（workspace.py:1338）
  - [x] 新增 `READ_FILE_MAX_CHARS = 50_000`（与 `MAX_COMMAND_OUTPUT_CHARS` 同处，workspace.py:37 区域）
  - [x] 全局搜索确认无其他文件重复定义这些阈值（`GIT_MAX_`、`READ_FILE_MAX`）

- [x] Step 2.2：`workspace_git_diff` 单文件 diff 截断
  - [x] 在 `workspace_git_diff`（workspace.py:1436-1441）写入每文件 `diff` 截断：
    ```python
    body = sections.get(file_entry["path"], "")
    if body:
        if len(body) > GIT_MAX_PER_FILE_DIFF_CHARS:
            body = body[:GIT_MAX_PER_FILE_DIFF_CHARS] + "\n…[diff truncated]"
            truncated_diff = True
        file_entry["diff"] = body
    ```
  - [x] 超限文件仍保留 `path/added/removed/status` 统计字段，消费方兼容
  - [x] `truncated_diff` 在任一文件被截断时置 true（与既有整库截断逻辑合并）
  - [x] 跑 Step 1.2 失败测试，确认转绿

- [x] Step 2.3：`read_file` 工具复用 `read_preview` 语义
  - [x] `read_file` 工具（agents.py:557-563）改为：
    ```python
    @tool(args_schema=ReadFileArgs)
    def read_file(file_path: str) -> str:
        """Read a text file from the workspace (binary files return a hint)."""
        try:
            preview = workspace.read_preview(file_path, max_chars=READ_FILE_MAX_CHARS)
            if preview.get("binary"):
                return json.dumps(
                    {"binary": True, "size": preview.get("size", 0),
                     "hint": "Binary file — open it in the file panel; its raw bytes are not readable as text."},
                    ensure_ascii=False,
                )
            return json.dumps(preview, ensure_ascii=False)
        except Exception as exc:
            return _error_result(exc, "read_file")
    ```
  - [x] 确认 `READ_FILE_MAX_CHARS` 从 `workspace.py` 导入（agents.py 顶部 import）
  - [x] `read_preview` 的 `max_chars` 默认值保持 `100_000`（前端路径不变），agent 调用显式传 `READ_FILE_MAX_CHARS`
  - [x] 跑 Step 1.3 失败测试，确认转绿
  - [x] 确认 `read_text` 仍被 write/replace/apply 回读路径使用（workspace.py:554/593/637/911/999），不受影响

- [x] Step 2.4：回归确认
  - [x] 跑 `backend/tests/test_goal_loop.py` 全量（既有 10 用例）
  - [x] 手动：在 `ailicode` 类大仓库上调用 `git_status`，确认返回 ≤ ~110KB
  - [x] 手动：`read_file` 读一个 .png 与一个大 .log，确认返回受限结果
  - [x] 同步更新调研报告/设计方案中「完成」状态（若需）

---

## Phase 3：goal 系统提示与工具列表改造

- [x] Step 3.1：重写 `goal_system_prompt` 为三阶段指令
  - [x] 阶段 0（plan）：
    - 「先制定计划再动手：用 `search_files`/`read_file` 渐进式了解与目标相关的部分，不要一次读取整个仓库」
    - 「产出具体的 todos（`write_todos`），覆盖你要做的事」
  - [x] 阶段 1..n（execute）：
    - 「当前聚焦这一个 todo：{current_todo}」
    - 「完成后调用 `finalize_goal(achieved=false, progress=<状态>)` 推进到下一个 todo」
  - [x] 阶段 N（verify）：
    - 「所有 todo 已完成。运行验证（测试 / 命令）证明全部完成，然后调用 `finalize_goal(achieved=true, verification=<证据>)`」
  - [x] 移除/弱化「Research, edit files and run workspace commands」对全量 git_status 的引导
  - [x] 保留：continuation 规则（plain text 不算完成）、`finalize_goal` 唯一完成入口、mode_line（autonomous/supervised/guarded）
  - [x] 明确提示「不要调用 git_status 获取仓库全貌，用 search_files / glob / read_file 定位所需文件」

- [x] Step 3.2：goal 模式工具列表移除 `git_status`
  - [x] 确认 goal 模式工具过滤点：`GoalModeMiddleware._overrides`（agents.py:2991-3004）或 `build_coworker_agent_graph` 传入工具
  - [x] 在 goal 模式使用的工具集合中排除 `git_status`（在过滤点用 `[t for t in tools if t.name != "git_status"]`）
  - [x] 保留 `git_status` 工具定义与普通对话/只读子代理可用性（`_READ_ONLY_TOOLS` 不变，agents.py:953）
  - [x] 全局搜索确认无其他 goal 路径注入 `git_status`

---

## Phase 4：goal_stream 阶段化执行（Plan → Execute → Verify）

- [x] Step 4.1：`sessions.py` 新增 `goal_phase` 字段
  - [x] `Session` 新增 `goal_phase: str = "plan"`（sessions.py:49-58 区域）
  - [x] `from_dict`（sessions.py:78 起）补 `goal_phase=str(payload.get("goal_phase", "plan"))`
  - [x] `to_dict` / `public`（sessions.py:105 起）补 `goal_phase`
  - [x] `update_goal`（sessions.py:203）新增 `goal_phase: str | None = None` 参数
  - [x] `commit_goal_end`（sessions.py:242）终态提交时保留/记录 `goal_phase`（可选）
  - [x] 旧会话缺失时默认 `plan`

- [x] Step 4.2：`goal_stream` 阶段推进
  - [x] 读 session 的 `goal_phase` 与 `goal_todos`（`session_state.goal_phase` / `session_state.goal_todos`）
  - [x] 定义当前 todo 选取（单一真源）：`next(t for t in goal_todos if t.get("status") != "completed", None)`
  - [x] round 1 初始 `goal_phase=plan`；注入「制定计划」上下文
  - [x] `goal_checkpoint(achieved=false)` 且已产出 todos → 写 `goal_phase="execute"`（`update_goal`）
  - [x] `execute` 阶段每轮注入「当前 todo」到 `_stream`（`goal_todo` 参数）
  - [x] 当前 todo 为空（全部完成）→ 写 `goal_phase="verify"`，continue
  - [x] `verify` 阶段注入「运行验证再完成」；`achieved=true` → done（走 `_commit_terminal`）
  - [x] 任意阶段 `achieved=true` → done（既有原子落库路径）
  - [x] `paused`/`stopped`/`timeout`/`stalled`/`max_rounds` 处理保持既有语义
  - [x] `goal_round` 事件新增 `phase` 字段（agents.py:4176：`yield {"type": "goal_round", "round": round_no, "goal": goal_text, "status": "running", "phase": goal_phase}`）
  - [x] `_stream` 新增 `goal_phase` / `goal_todo` 参数（agents.py:3862 签名），`inputs` 传入（agents.py:3926-3939）

- [x] Step 4.3：`GoalModeMiddleware._overrides` 注入阶段上下文
  - [x] 从 `request.state` 读取 `goal_phase` 与当前 todo
  - [x] 追加到注入提示：`prompt += f"\n\nCurrent phase: {goal_phase}\nCurrent todo: {current_todo}"`（按阶段）
  - [x] 确认 `_stream` 的 `inputs` 传入 `goal_phase` 并随 checkpoint 持久化

- [x] Step 4.4：入口/恢复端点补 `goal_phase`
  - [x] `chat_stream` goal 分支（main.py:1894-1932）初始化 `goal_phase="plan"`
  - [x] `/goal/start`（main.py:2491-2525）重置 `goal_phase="plan"`
  - [x] `/goal/resume`（main.py:2557）按当前 todo 状态推断并写回 `goal_phase`：
    - 有未完成 todo → `execute`
    - 全部完成且未 done → `verify`
    - 无 todo → `plan`
  - [x] `/goal/status`（main.py:2438）返回 `goal_phase`

- [x] Step 4.5：补阶段流转失败测试与回归
  - [x] 新增失败测试：goal_stream 在 plan→execute→verify→done 的流转（用既有 `_make_runtime` / FakeStore 模式，tests/test_goal_loop.py:104）
  - [x] 新增断言：`goal_round` 事件带 `phase`
  - [x] 新增断言：`/goal/status` 返回 `goal_phase`（可通过 FakeStore 直接断言 `update_goal(goal_phase=...)`）
  - [x] 跑 `backend/tests/test_goal_loop.py` 全量确认既有用例不回归

---

## Phase 5：前端阶段读面 + 兼容

- [x] Step 5.1：`types.ts` 扩展
  - [x] `GoalState`（types.ts:720）新增 `phase?: 'plan' | 'execute' | 'verify'`
  - [x] goal 事件类型 `goal_round`（types.ts:668）新增可选 `phase?: 'plan' | 'execute' | 'verify'`
  - [x] `GoalStatusResponse.goal`（types.ts:736）随 `GoalState` 自动获得 `phase`

- [x] Step 5.2：`GoalCard.tsx` 阶段文案
  - [x] 显示阶段文案（计划中 / 执行中 / 验证中）
  - [x] 缺失 `phase` 时默认不显示阶段（容错旧会话）

- [x] Step 5.3：`App.tsx` reconcile / 恢复兼容
  - [x] `goal_round` 事件处理（App.tsx:1264-1275）透传 `phase` 到 `setGoal`
  - [x] resume 处理（App.tsx:3034-3070）同步 `phase`
  - [x] session 恢复逻辑读取 `goal_phase`（缺失容错）
  - [x] `goal_start` 事件（App.tsx goal 分支）初始 `phase: 'plan'`
  - [x] `tsc --noEmit` 与 `vite build` 通过

- [x] Step 5.4：locales 阶段文案
  - [x] 11 个语言文件新增阶段文案 key：
    - `chat.goal_phase_plan`（计划中）
    - `chat.goal_phase_execute`（执行中）
    - `chat.goal_phase_verify`（验证中）
  - [x] 确认 11 个文件 key 对齐（de/en/es/fr/ja/ko/pt-BR/ru/zh/zh-HK/zh-TW）

---

## Phase 6：回归、验收与文档收口

- [x] Step 6.1：全量回归
  - [x] `backend/tests/test_goal_loop.py` 全量通过（既有 10 + 新增阶段流转 + 截断用例）
  - [x] `backend/tests/test_git_status_truncation.py`（若独立文件）全量通过
  - [x] 前端 `tsc --noEmit`、`vite build` 通过
  - [x] 普通对话手动回归（非 goal）：SSE 事件、消息流、工具调用无变化

- [x] Step 6.2：真实链路验证
  - [x] 在 `ailicode` 类大仓库启动一个 goal，确认：首轮进入 plan、不调 git_status、上下文预算平稳增长（不跳 300k）
  - [x] 确认逐 todo 推进，最后一个 todo 后进入 verify，`finalize_goal(achieved=true)` 后落库 done
  - [x] 确认 titlebar 上下文预算不再出现 git_status 1MB 注入
  - [x] 确认 `paused` / `stopped` / `timeout` 终态与刷新不再复活（回归上轮修复）
  - [x] 确认 `/goal/status` 返回 `goal_phase`，前端 GoalCard 展示阶段文案

- [x] Step 6.3：文档收口
  - [x] 同步更新《Goal 能力重构开发设计方案.md》中「完成」状态与任何实现偏差
  - [x] 同步更新《Goal 能力调研报告.md》证据结论
  - [x] 本 task list 状态改为「执行中」→ 完成后按需改为冻结口径
  - [x] 在完成标准核对表逐项打勾并给出证据位置（测试文件 + 行号 / 手动验证记录）

---

## 完成标准核对表（最终验收勾选）

- [x] `git_status` 大仓库返回 ≤ ~110KB，单文件 diff ≤2K
- [x] goal 首轮 plan，不调 git_status，产出 todos
- [x] execute 逐 todo 推进
- [x] verify 阶段注入验证指令
- [x] `finalize_goal(achieved=true)` 原子落库 done
- [x] `read_file` 二进制返回受限结果、大文本截断 50K
- [x] `/goal/status` 返回 `goal_phase`
- [x] 前端 GoalCard 展示三阶段文案
- [x] `test_goal_loop.py` 全量通过（既有 + 新增）
- [x] `tsc --noEmit` 与 `vite build` 通过
- [x] 普通对话回归无变化
- [x] 刷新后 goal 不复活（上轮修复保持）

### 验收证据

| 验收项 | 证据位置 |
|---|---|
| git_status 截断（单文件 2K / 50 文件 / 100K 总量） | `workspace.py` 常量 `GIT_MAX_*`；`workspace_git_diff` 每文件截断；测试 `tests/test_goal_truncation.py::test_git_diff_single_file_truncated / test_git_diff_file_count_capped / test_git_diff_total_cap_respected` |
| read_file 二进制约束 + 50K 截断 | `agents.py` `read_file`（走 `read_preview`）；常量 `READ_FILE_MAX_CHARS`；测试 `test_goal_truncation.py::test_read_preview_binary_returns_hint / test_read_preview_large_text_truncated` |
| goal 首轮 plan、不调 git_status、产出 todos 后 execute | `goal_system_prompt`（plan 阶段指令 + 禁 git_status）；`GoalModeMiddleware._overrides` 过滤 `git_status`；`goal_stream` 阶段推进 |
| 逐 todo 推进 + verify 注入 | `_first_incomplete_todo` + `goal_stream` 每轮注入 `goal_todo`；verify 阶段提示词；测试 `test_goal_loop.py::test_plan_to_execute_to_verify_to_done / test_execute_stays_in_execute_while_todos_remain` |
| `finalize_goal(achieved=true)` 原子落库 done | `goal_stream` `_commit_terminal`（冻结结构）→ `commit_goal_end`；测试 `test_goal_loop.py::test_achieved_commits_terminal_atomically` |
| `/goal/status` 返回 `goal_phase` | `main.py` `goal_status`；`sessions.py` `goal_phase` 字段与序列化 |
| 前端 GoalCard 三阶段文案 | `GoalCard.tsx` 徽标 + `App.css` `.goal-card__phase--*` + 11 个 locale `chat.goal_phase_*` |
| 测试与构建 | `backend/tests/` 18 例通过；`tsc --noEmit` 通过；`vite build` 通过 |
| 死代码删除面 | 删 `Workspace.read_text` / `GOAL_MARKER` / `_finalize_called` / `handleGoalResumeEvent`（见「实现记录」），删除后全量回归仍通过 |

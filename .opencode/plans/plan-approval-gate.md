---
name: plan-approval-gate
description: Plan 审批门重构（对标 Claude Code / LangGraph 官方），移除手动 toggle 与 [CW-PLAN] 注入
status: completed
---

## 目标

将 CW 的 plan/build 从「手动 toggle + `[CW-PLAN]` marker 注入」重构为「**自动先研究 → 出计划 → interrupt 审批门 → 批准后无缝执行**」，对齐 LangGraph 官方 plan-execute 模式 + Claude Code 审批流程。**删除手动 Plan/Build toggle 与 `/plan` `/build` 命令**。

**v1 范围**：PlanBlock 审批卡上「批准 / 拒绝 / 重新规划」三按钮；「编辑计划」留 v2。

## 架构变更

### 现状（删除）
```
work_mode: "plan"|"build" 驱动
PlanGateMiddleware.before_model → 盲 planner LLM → 注入 [CW-PLAN] assistant 消息
  → _scrub_plan / _strip_plan_leak / _already_planned 三个补丁
前端 Plan/Build toggle + /plan /build
```

### 目标
```
agent 先研究 (search/read) → 调用 submit_plan 工具 → after_model 拦截 → interrupt()
  → 前端 PlanBlock + 审批卡（批准/拒绝/重新规划）
  → 批准后重跑图（注入已批准计划）→ 执行写文件
```

## 后端改动

1. `PlanApprovalMiddleware`（替换 PlanGateMiddleware）：
   - `after_model` 拦截 `submit_plan` 工具调用，构造 HITLRequest 并 `interrupt()` 暂停
   - `wrap_tool_call` 在 `plan_approved` 为 False 时阻断文件写入工具（`_CHANGE_TOOL_NAMES`），**不阻断 run_command**（保留其自有 HITL 审批）
   - approve 决策 → 返回 ToolMessage + `plan_approved: True`
2. 新增 `submit_plan` 工具（present plan）
3. `interrupt_action_kind` / `stream_event_from_interrupt` 支持 `plan` kind → `plan_required` 事件
4. `build_coworker_agent_graph`：始终加入 PlanApprovalMiddleware；system prompt 引导 plan-first
5. `main.py resolve_command_approval` 支持 `regenerate` 决策
6. `CoworkerAgentState` 增加 `plan_approved` 字段；stream/run 输入初始化 `plan_approved: False`

### Resume 修复（重要）

发现 LangGraph 1.2.10 的 `Command(resume=...)` 在**多 middleware + interrupt 位于 after_model 节点**时无法可靠恢复中断。**根因是双重的**：

1. **`plan_approved` 状态未持久化**：approve 决策后 `plan_approved=True` 没有写回 checkpoint，resume 恢复的 agent 看到 `plan_approved=False`，`wrap_tool_call` 拦截写文件工具 → agent 重新 submit_plan → 死循环。
2. **SQLite `database is locked`**：`_open_checkpointer` 每次新建 `AsyncSqliteSaver` 连接，多个连接在同一 checkpoint 文件上交替读写时锁竞争（即使 busy timeout 也只是等待 30 秒后超时）。该 bug 在原始代码的 run_command 审批中同样存在（预先存在，非本次引入）。

**解决方案**：
- **resume 前用 `graph.aupdate_state(config, {"plan_approved": True})`** 持久化批准状态，在**完全独立的 checkpoint 块**中完成（先关闭，再开 resume 块），避免与 resume 连接争锁。
- **进程级单例 `AsyncSqliteSaver`**（`_open_checkpointer`）：整个进程复用一个 checkpoint 连接，串行化所有 checkpoint 读写，彻底消除 `database is locked`。
- reject / regenerate：不重跑 agent，直接返回简洁 done 消息。

**结果**：plan approve 走**官方 interrupt/resume 无缝续跑**（同一 graph、同一 thread 恢复执行），不是重跑。run_command/ask_user 的 resume bug 也因单例连接顺带修复。

## 前端改动

1. `ChatInput.tsx`：删除 Plan/Build toggle、access-mode 禁用逻辑、`/plan` `/build` 命令
2. `SettingsView.tsx` / `WorkspaceInspector.tsx` / `preference-options.tsx`：移除 workMode 选项
3. `PlanBlock.tsx` 保持；`PendingDocks.tsx` 新增 PlanDock（计划文本 + 批准/拒绝/重新规划）
4. `App.tsx`：处理 `plan_required` 事件 → PendingRequest kind=plan；`pendingFromEvent` 支持 plan；删除 `/plan` `/build`
5. `types.ts`：`PendingRequest.kind` 增加 `'plan'` + `plan` 字段；`ApprovalDecisionType` 增加 `'regenerate'`；`StreamEvent` 增加 `plan_required`
6. locales：新增 plan 审批文案

## 验证

后端 + ego-browser 真机（DeepSeek provider）均验证通过：
- ✅ 写文件任务 → plan 审批卡 → 批准 → **同一执行流无缝续跑**写文件成功（非重跑）
- ✅ 拒绝 → "No changes were made"，无文件创建
- ✅ 重新规划 → "No changes were made"
- ✅ 纯问答不触发计划审批
- ✅ 用户消息时间戳 + Agent meta 行（模型 + 时长）
- ✅ run_command 审批触发 + approve 后继续执行均正常（单例连接顺带修复 resume）
- ✅ 并发（2 个并行 session）稳定：连续 4 次 + 并发 2 次全部成功，无 `database is locked`

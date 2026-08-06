# Coworker 交互模型调研报告

**主题**：plan/build 模式 × 三类审批卡片 × 授权档位，三者的业务逻辑关系；如何实现"讨论期问清楚、执行期不打扰"；业界主流做法与 LangGraph 官方指引对照。

**调研日期**：2026-08-06
**代码基线**：`backend/coworker/agents.py`（约 1800 行）、`backend/main.py`、`frontend/src/App.tsx`、`frontend/src/components/PendingDocks.tsx`
**依赖版本**：`langchain==1.3.14`、`langgraph==1.2.10`、`langgraph-checkpoint-sqlite==3.1.1`

---

## 0. 一句话结论

> **现状**：cw 把两个正交的东西（"要不要问人" 和 "能不能做危险动作"）压在了 `access_mode` 一个开关上，又用一个**无条件挂载**的计划中间件绕过它；`work_mode` 则被前端彻底架空（前端根本不发这个字段，后端恒为 `build`）。所以"讨论期问清楚 / 执行期不打扰"这个行为在当前架构下**无法表达**。
>
> **业界共识**：Claude Code / Codex / opencode 全部走**多轴正交 + 命名预设**：一个轴管"阶段/交互契约"，一个轴管"审批策略"，一个轴管"硬能力边界"，用户看到的是几个预设名（Plan / Auto / Full Access），底层是矩阵。
>
> **方案**：cw 应拆成 **Phase（阶段）× Autonomy（自主度）× Capability（硬边界）** 三轴，其中 `ask_user` 的门挂在 **Phase** 上（而不是权限上），命令/写文件的门挂在 **Autonomy** 上，计划门是 **Phase 的状态转移触发器**（对标 Claude Code 的 `ExitPlanMode`）。LangGraph 官方的 `wrap_model_call` + `request.override(tools=...)` 是实现"执行期物理上无法打扰你"的标准写法。

---

## 1. 先把需求变成可判定的规则

原始需求："在讨论和非执行的任务中，会询问清楚用户的意图。在执行中的任务中不打扰用户全自动推进任务达成任务目标。"

这句话隐含了 4 条必须落到代码里的判定：

| # | 规则 | 判定主体 | 现状能否表达 |
|---|---|---|---|
| R1 | 讨论阶段，agent **应当且能够**向用户提问 | 阶段 | ❌ `ask_user` 在 full 档位下被静默禁用 |
| R2 | 执行阶段，agent **不能**向用户提问（物理上没有这个工具） | 阶段 | ❌ `ask_user` 恒注册，任何时候都能弹 |
| R3 | 从讨论进入执行，必须有一次**明确的意图确认**（计划批准） | 阶段转移 | ⚠️ 有计划卡，但它无条件弹，且批准后永久失效 |
| R4 | 执行阶段的"打扰"只应来自**硬边界越界**，而不是常规操作 | 自主度 + 硬边界 | ❌ 默认档位下每条命令都弹卡 |

**关键洞察**：R1/R2 是"阶段"问题，R4 是"权限"问题。cw 现在用同一个变量 `access_mode` 同时控制 R1 和 R4，所以它们必然打架——想让执行期不弹命令卡就得开 full，一开 full 讨论期就不能提问了。

---

## 2. cw 现状：三个机制的真实实现

### 2.1 系统里有三个叫 "mode" 的东西

`backend/coworker/agents.py:23-26`：

```python
AgentMode = Literal["single"]          # agent 拓扑，占位
WorkMode  = Literal["plan", "build"]   # 工作模式
AccessMode = Literal["default", "full"] # 授权档位
```

| 概念 | 实际状态 |
|---|---|
| `AgentMode` | 只有 `"single"` 一个值，为多 agent 预留 |
| `WorkMode` | **前端不可达**。`App.tsx:440-456` 拼请求体时不带 `work_mode`；`normalize_work_mode(None) → "build"`（`agents.py:172-173`）。git 记录 `7e3101e5 「移除手动 Plan/Build toggle」` 是拆除点 |
| `AccessMode` | 唯一真正生效的开关，前端 `App.tsx:99` 一个 React state，**不持久化** |

**结果**：后端为 plan 模式写的所有分支（`agents.py:185-186` 的提示词、`agents.py:1291/1391/1564` 的 `effective_access` 降级）全是死代码。

### 2.2 三类中断来自两个互不知情的中间件

装配顺序 `agents.py:1197-1203`：

```python
middleware.append(PlanApprovalMiddleware(language))                          # 无条件挂载
middleware.extend(command_approval_middleware(access_mode, approval_store))  # access_mode == "full" 时为空
```

LangChain 的 `after_model` **逆序执行**（`langchain/agents/factory.py:1738`），实际顺序是：

```
model → HumanInTheLoopMiddleware → PlanApprovalMiddleware → ToolCallCleaner
```

| 中断类型 | 抛出点 | 触发条件（完整布尔表达式） |
|---|---|---|
| `approval_required`（命令） | langchain 内置 HITL，`human_in_the_loop.py:435` | `access_mode != "full"` ∧ `argv[0] ∈ ALLOWED_COMMANDS` ∧ `¬always_allowed(sha256(argv+cwd))` |
| `question_required`（提问） | 同上 | `access_mode != "full"`（**无 `when` 谓词，无条件**） |
| `plan_required`（计划） | 项目自有，`agents.py:1115` | 模型调用了 `submit_plan` ∧ `¬_is_plan_approved(state)`（**与 mode、access 都无关**） |

### 2.3 授权档位的真实作用面

**唯一影响点**是 `agents.py:466-467`：

```python
if access_mode == "full":
    return []   # 一次性干掉 run_command 和 ask_user 两个 interrupt_on
```

**判定矩阵（现状）**，前提 `effective_access = (work_mode=="build") ? access_mode : "default"`：

| work_mode | access | `run_command` | `ask_user` | `submit_plan` | `write_file` 等 |
|---|---|---|---|---|---|
| plan | default | 弹审批卡 | 弹提问卡 | 弹计划卡 | 计划未批→软阻断 |
| plan | full | **同上（full 被降级）** | 同上 | 同上 | 同上 |
| build | default | 弹审批卡（白名单内、非 always） | 弹提问卡 | 弹计划卡 | 计划未批→软阻断 |
| build | **full** | **直接执行** | ❌**静默失效** | ⚠️**仍弹计划卡** | 计划未批→**仍软阻断** |

三个红字就是问题所在：
- 开 full 想"不打扰"，结果**提问能力被误杀**（`ask_user` 返回一段 `"status":"awaiting_user"` 的死 JSON，`agents.py:343`，永远没人回答，模型只能自己瞎猜）；
- 同时计划卡**照弹不误**，所谓"完整访问"根本没做到不打扰。

**「始终允许」的粒度**（`workspace.py:580-583`）：

```python
payload = json.dumps({"command": command, "cwd": cwd}, sort_keys=True)
return hashlib.sha256(payload.encode()).hexdigest()
```

按**完整 argv + cwd** 精确哈希，全局持久化到 `~/Library/Application Support/Coworker/command_approvals.json`，**无删除接口**。但 UI 只显示 `command[0]`（`PendingDocks.tsx:154`，"始终允许 npm"），语义严重错配——用户以为授权了 npm，实际只授权了 `npm run build` 这一串在这一个目录。

### 2.4 不自洽点清单（按严重度）

| ID | 问题 | 位置 |
|---|---|---|
| **D1** | plan 模式前端完全不可达，后端整套逻辑是死分支 | `App.tsx:99` / `agents.py:172` |
| **D2** | `PlanApprovalMiddleware` 无条件挂载，执行档位下依然被计划卡打断；且 docstring 提到的 `PlanGateMiddleware` 类根本不存在 | `agents.py:1202` / `agents.py:1191-1194` |
| **D3** | 「完整访问」意外禁用提问能力（权限开关越权管交互） | `agents.py:466-467` + `agents.py:343` |
| **D4** | reject / regenerate **不 resume 图**，直接 `yield done` + `return`，LangGraph 线程停在 interrupt 状态，checkpoint 悬挂；下一轮在悬挂 checkpoint 上启动，行为未定义 | `agents.py:1574-1593` |
| **D5** | `regenerate`（UI 叫"重新规划"）被归到终止分支，**不会重新出计划**；中间件里写好的 regenerate 分支是死代码 | `main.py:906` / `agents.py:1130-1136` |
| **D6** | 计划门一次通过**永久失效**：`_is_plan_approved` 扫全会话历史找成功的 `submit_plan` ToolMessage | `agents.py:1070-1086` |
| **D7** | `workspace.py:483-530` 有第二套审批机制，`approval_store` 参数 4 个调用点全传 `None`，48 行死代码；且与 HITL 的 digest 格式不同却混存同一个 JSON | `agents.py:1295/1396/1608/1649` |
| **D8** | `effective_access` 降级表达式复制粘贴 3 份，且 trace metadata 记录的是原始值，运行时行为与观测不一致 | `agents.py:1291/1391/1564` |
| **D9** | `runtime_instruction` 双重注入（system_prompt + 追加到最后一条 user message）；`state["work_mode"]` 只写不读 | `agents.py:1207` + `agents.py:866-869` |
| **D10** | `run_command` **逃逸计划门闸**：`wrap_tool_call` 只查 `_CHANGE_TOOL_NAMES`（不含 run_command），与 prompt 里 "Do NOT use write/execute tools until approved" 直接矛盾 | `agents.py:405-406`, `1156` |
| **D11** | 前端把 `AgentMode`（`'single'`）强转成 `WorkMode`，导致历史消息全部挂假的 "Build" 徽章 | `App.tsx:1234` + `MessageList.tsx:180` |
| **D12** | `restorePendingForSession` 不认识 `'plan'`，切走再切回会话，待批准的计划会退化成一张空白命令卡 | `App.tsx:1176` |
| **D13** | ✕ 关闭 = 拒绝 = **硬停止整轮任务**，无"稍后再说"路径；pending 期间 composer 被整体替换，用户无法插话 | `PendingDocks.tsx:411` / `App.tsx:1526` |
| **D14** | access_mode 有 3 份状态源（React state / 未接线的 `lib/modePrefs.ts` / 后端 session），互不同步，刷新即丢 | `App.tsx:99` / `modePrefs.ts` |
| **D15** | 中断事件→PendingRequest 的映射在前端复制 4 份，已抽出的 `pendingFromEvent()` 只被 1 处复用 | `App.tsx:380/630/807/1087` |
| **D16** | 自定义 plan 流式通道（`plan_start`/`plan_delta`/`plan_end`）整体死亡——无任何 `get_stream_writer` 发射端 | `agents.py:1425-1430` |

### 2.5 文档也已经跟代码脱节

`PROJECT_PLAN.md` 与代码的偏离：

| PROJECT_PLAN.md 声称 | 代码实际 |
|---|---|
| L39 "planner -> executor -> verifier -> summarizer" | 已改为单个 `create_agent` + middleware 链 |
| L42-43 "Default access: `search_files`, `read_file`" | `build_workspace_tools(writable=True)` **4 个调用点全硬编码 True**，任何档位都注册全部工具（`agents.py:1296/1397/1609/1650`） |
| L44 "Plan mode always removes write access" | 仅 prompt 文本约束，无硬移除 |
| L61 "[x] Plan/Build toggle" | 已被移除，前端不可达 |

---

## 3. 业界主流做法

### 3.1 Claude Code —— "模式是基线，规则是叠加层"

**六档权限模式**（官方 `permissions.defaultMode`）：

| Mode | 无需询问即可执行 | 适用 |
|---|---|---|
| `default` | 仅读 | 上手、敏感工作 |
| `acceptEdits` | 读 + 文件编辑 + 常见文件系统命令（工作目录内） | 你会 review diff 的迭代 |
| `plan` | 仅读（**写类工具被移出工具集**） | 改动前探索 |
| `auto` | 全部，但**后台跑一个分类器模型**审每个动作 | 长任务、减少打扰 |
| `dontAsk` | 仅 allow 列表内的（其余自动拒绝） | CI / 脚本 |
| `bypassPermissions` | 全部（跳过规则层），保护路径除外 | 隔离容器/VM |

**四条可直接抄的设计**：

1. **模式与规则是两层**：规则按 `deny → ask → allow` 顺序求值，首个匹配胜出；**除 `bypassPermissions` 外，所有模式下规则层依然生效**。cw 现在只有"模式"没有"规则层"，`ALLOWED_COMMANDS` 是硬编码的 12 项白名单，用户无法配置。

2. **Plan mode 靠删工具，不靠提示词**：
   > "Entering Plan Mode flips the write-class tools (Edit, Write, and any Bash invocation that mutates state) into denied... From the model's perspective, Plan Mode 'prevents' mutations not by persuasion but by **removing those tools from the menu of available actions**. This is why a sufficiently determined prompt cannot bypass it."

   cw 的 plan 约束**完全靠 prompt 文本**（`agents.py:186`），这是根本性的差距。

3. **`ExitPlanMode` 是模式转移的触发器，且出口是多选**：

   ```js
   async function exitPlanMode({ plan }) {
     const approved = await askUser({ type: 'plan_approval', plan, options: [...] })
     if (approved.action === 'approve') { setPermissionMode('normal'); return {...} }
     return { status: 'rejected', feedback: approved.feedback }
   }
   ```

   官方文档列出的批准出口有四个：

   | 出口 | 含义 |
   |---|---|
   | Keep planning | 给反馈，让 Claude 改计划（**继续，不终止**） |
   | Approve with manual review | 进入执行，但编辑/命令仍逐个批 |
   | Approve with accept edits | 进入执行，编辑自动通过 |
   | Approve with auto mode | 进入执行，最大自主度 |

   **这就是用户想要的行为的标准答案**：计划批准这一刻，同时决定了后续执行阶段的自主度。cw 现在计划批准只写了个 `plan_approved=True`，不带任何自主度信息。

4. **`auto` 模式用分类器替代人**：不弹提示，但跑一个独立分类器审每个动作，拦截超出用户原始请求范围的行为（`curl | bash`、生产部署、force-push main）。这是"不打扰但不失控"的工程解法。

### 3.2 OpenAI Codex —— "两轴正交 + 命名预设"

两个**完全独立**的配置项：

```toml
approval_policy = "on-request"     # 交互策略：什么时候停下来问
sandbox_mode    = "workspace-write" # 能力边界：物理上能碰什么
```

| `approval_policy` | 含义 |
|---|---|
| `untrusted` | 仅自动运行已知安全的读操作，可变更状态的命令都要批 |
| `on-request` | 工作区内自动跑；越界写 / 需要网络时才停下来问 |
| `never` | 不弹任何审批，完全靠沙箱边界约束 |
| `granular = {...}` | 按类别细分：哪些类别保持交互、哪些自动拒绝 |
| `approvals_reviewer = "auto_review"` | **符合条件的审批请求先交给一个 reviewer agent 评估**，而不是弹给用户 |

| `sandbox_mode` | 含义 |
|---|---|
| `read-only` | 只读 |
| `workspace-write` | 可写工作区，默认无网络；`.git` / `.codex` 等保护路径递归只读 |
| `danger-full-access` | 无沙箱 |

**官方的组合预设表**（原文）：

| Intent | Flags | Effect |
|---|---|---|
| **Auto (preset)** | `--sandbox workspace-write --ask-for-approval on-request` | 工作区内自由读写执行；越界或联网才问 |
| Safe read-only browsing | `--sandbox read-only --ask-for-approval on-request` | 读+回答；其余要批 |
| Read-only non-interactive (CI) | `--sandbox read-only --ask-for-approval never` | 只读、从不问 |
| Auto-edit but gate untrusted cmds | `--sandbox workspace-write --ask-for-approval untrusted` | 可改文件，跑不可信命令要批 |
| Auto-review mode | `... -c approvals_reviewer=auto_review` | 同 on-request 边界，但审批交给 reviewer agent |
| Dangerous full access | `--yolo` | 无沙箱无审批 |

**三条可直接抄的设计**：

1. **审批与沙箱正交**，`never` + `read-only` 是完全合法且有用的组合（CI）。cw 现在只有一个开关，无法表达"不打扰但严格受限"。
2. **审批的触发点是"越界"，不是"动作类型"**。`on-request` 的语义是：*工作区内的一切都不问，越出边界才问*。这正是"执行期不打扰"的正确定义——不打扰不等于无约束，而是**约束前移到边界，而非逐次询问**。
3. **`auto_review` 是多 agent 演进的官方答案**：把审批从"人"换成"另一个 agent"。reviewer 评估 sandbox escalation、被拦的网络请求、`request_permissions`；低/中风险放行，高风险才升级给人，超时 fail closed。

### 3.3 opencode —— "权限按工具键 × glob，且可 per-agent 覆盖"

三态：`allow` / `ask` / `deny`。按工具键配，支持 glob，**最后匹配的规则胜出**：

```json
{
  "permission": {
    "bash": { "*": "ask", "git *": "allow", "rm *": "deny", "npm *": "allow" },
    "edit": { "*": "deny", "packages/web/src/content/docs/*.mdx": "allow" }
  }
}
```

**两个关键设计**：

1. **`question` 本身就是一个 permission key**。官方权限键列表：
   > `read`, `edit`, `glob`, `grep`, `bash`, `task`, `skill`, `lsp`, **`question` — asking the user questions during execution**, `webfetch`, `websearch`, `external_directory`, `doom_loop`

   也就是说 opencode **明确把"向用户提问"当成一种需要授权的能力来管理**，而不是把它跟命令审批捆在一起。cw 现在恰恰是捆在一起的（D3）。另外 `doom_loop`（同一工具调用连续 3 次相同输入）默认 `ask`——这是全自动模式下防跑飞的实用护栏。

2. **primary agent（Build / Plan）通过 permission 差异化，而非硬编码**：

   ```json
   {
     "agent": {
       "plan": {
         "mode": "primary",
         "permission": { "edit": "deny", "bash": "deny" }
       },
       "build": {
         "permission": { "bash": { "*": "ask", "git status *": "allow" } }
       }
     }
   }
   ```

   Tab 键在 primary agent 之间循环。subagent 通过 `@` 提及或 Task 工具调用，也各自带 permission。**这是从单 agent 平滑演进到多 agent 的现成模型**：agent = 提示词 + 工具集 + 权限契约。

3. `--auto` 全局开关：自动批准一切**未被显式 deny** 的请求。deny 规则永远生效。`ask` 弹窗时三个出口：`once` / `always`（按工具建议的模式白名单，**仅当前会话**）/ `reject`。注意 always 是**会话级**，不是像 cw 那样永久全局持久化。

### 3.4 GitHub Copilot / VS Code —— "allowlist + denylist + 子命令拆解"

```json
{
  "chat.tools.terminal.autoApprove": {
    "/^git\\s+(status|diff|log|show)\\b/": true,
    "rm": false, "sudo": false, "curl": false,
    "/^git\\s+(push|reset|revert|clean)\\b/": false
  },
  "chat.tools.global.autoApprove": false  // YOLO 模式，官方明确"绝不建议"
}
```

值得抄的两点：
- **子命令拆解**：`foo && bar` 会被拆成 `foo` 和 `bar`，两者都必须匹配 allow 且都不匹配 deny 才自动通过；`$(foo)`、`<(foo)` 这类内嵌命令默认被广泛规则拦住。cw 现在只看 `Path(argv[0]).name`（`agents.py:444-457`），`git status && rm -rf /` 这种复合命令若走 shell 会直接漏过。
- **`chat.agent.maxRequests`**：agent 模式连续请求数上限，超过就停下来问"要不要继续"。这是"全自动"的兜底闸。

### 3.5 横向对照与五条行业共识

| 维度 | Claude Code | Codex | opencode | Copilot | **cw 现状** |
|---|---|---|---|---|---|
| 规划阶段独立 | ✅ plan mode（删工具） | ⚠️ 靠 read-only sandbox | ✅ plan agent | ❌ | ❌ 不可达 |
| 计划批准 = 模式转移 | ✅ ExitPlanMode 四选一出口 | — | Tab 手动切 | — | ❌ 只写 bool |
| 审批与能力边界分离 | ✅ mode + rules 两层 | ✅ approval × sandbox 两轴 | ✅ 三态 × 工具键 | ⚠️ 单层 allow/deny | ❌ 一个开关 |
| "提问"独立管理 | ✅ AskUserQuestion 独立工具 | — | ✅ `question` 是独立权限键 | — | ❌ 与命令审批捆绑 |
| 拒绝后可继续 | ✅ 反馈进上下文继续 | ✅ 合成 ToolMessage 继续 | ✅ | ✅ | ❌ 硬停止 |
| always 粒度 | 命令前缀，项目级持久 | — | 工具建议的模式，**会话级** | regex 可配 | ❌ 全 argv 精确哈希，全局永久，不可撤销 |
| 全自动护栏 | auto 模式分类器 | auto_review agent | doom_loop 检测 | maxRequests 上限 | ❌ 无 |
| 多 agent 权限 | subagent 各自 tools | subagent + auto_review | per-agent permission | — | ❌ `Literal["single"]` |

**五条共识**：

1. **正交分轴，预设收敛**。底层是矩阵，用户界面是几个有名字的预设（Plan / Auto / Full Access）。
2. **阶段用工具集实现，不用提示词实现**。想让 agent 在某阶段做不到某事，就把工具从它眼前拿走。
3. **"不打扰"= 把约束前移到边界，不是取消约束**。Codex 的 `on-request` 是最清晰的表述：区内自由，越界才问。
4. **拒绝是对话的一部分，不是任务的终点**。所有主流实现都把拒绝理由喂回模型让它换路走，只有 cw 直接杀掉这一轮。
5. **多 agent 的权限模型 = 单 agent 权限模型 + 每个 agent 一份契约 + agent 审 agent**（Codex `auto_review`、opencode per-agent permission）。

---

## 4. 给 cw 的统一设计：三轴模型

### 4.1 三个正交轴

```
Phase（阶段）      ── 决定：能不能问人 / 能不能写 / 计划门是否生效
Autonomy（自主度） ── 决定：执行期危险动作是弹卡、直接跑，还是拒绝
Capability（硬边界）── 决定：物理上碰不到什么。任何 Phase/Autonomy 都不能突破
```

**Phase** 不是用户拨的开关，是**由事件驱动的状态机**（对标 Claude Code：plan 模式可以由用户 Shift+Tab 进入，也可以由 Claude 在复杂任务时自主进入）：

| Phase | 工具集 | 计划门 | 提问 |
|---|---|---|---|
| `discuss` | 只读 + `ask_user` + `submit_plan` | — | ✅ 鼓励 |
| `plan` | 只读 + `ask_user` + `submit_plan` | 出口 | ✅ 鼓励 |
| `execute` | 全部**减去** `ask_user`、`submit_plan`，**加上** `escalate` | 已通过 | ❌ 物理移除 |
| `review` | 只读 + `ask_user` | — | ✅ |

**Autonomy** 是用户拨的开关，替代现在的「默认权限 / 完整访问」：

| Autonomy | 执行期行为 | 对标 |
|---|---|---|
| `supervised` | 每个写/执行动作弹卡 | Claude `default` |
| `guarded`（默认） | 工作区内的写/白名单命令直接跑；**仅越界才弹卡** | Codex `on-request` + Claude `acceptEdits` |
| `autonomous` | 一切按规则层判定，越界直接拒绝并把理由喂回模型；不打扰人 | Codex `never` + `workspace-write` / Claude `dontAsk` |

**Capability** 是硬边界，不可被前两轴覆盖：workspace 限定、`ALLOWED_COMMANDS`、保护路径（`.git` / 配置目录）。这一层应该开放成**用户可配的规则层**（`deny → ask → allow`），而不是现在硬编码的 12 项白名单。

### 4.2 "问清楚 vs 不打扰" 的判定规则

```
ask_user 是否可用   ← 只看 Phase。execute 阶段从工具集里物理移除。
submit_plan 是否可用 ← 只看 Phase。execute 阶段移除，防止二次弹计划卡。
写/执行是否弹卡      ← Autonomy × 规则层（deny > ask > allow）× 是否越界。
硬拒绝              ← 只看 Capability，与前两轴无关。
```

**执行期唯一的合法打扰通道**：`escalate(reason, blocking_question)` 工具。
- 语义明确："我遇到硬边界/信息缺失，无法继续"，而非"顺便确认一下"。
- `autonomy == autonomous` 时该工具也被移除，模型只能自己想办法或失败退出（对标 Codex `never` + fail closed）。
- 这对标 Codex 的 sandbox escalation：**升级请求是例外路径，不是常规交互**。

### 4.3 状态机

```
                    ┌──────────────────────────────────────────┐
                    │  用户消息进入                              │
                    └───────────────┬──────────────────────────┘
                                    ▼
                          ┌──────────────────┐
                          │  Phase = discuss │  只读 + ask_user + submit_plan
                          └────────┬─────────┘
              信息不足 ┌───────────┴────────────┐ 信息充分
                       ▼                        ▼
              ask_user → question卡      模型调用 submit_plan
                       │                        │
                       └────── respond ─────────┤
                                                ▼
                                     ┌──────────────────┐
                                     │  Phase = plan    │
                                     │  弹 plan 卡      │
                                     └────────┬─────────┘
                     ┌───────────────┬────────┴────────┬─────────────────┐
                     ▼               ▼                 ▼                 ▼
              「继续讨论」      「批准·逐步确认」  「批准·守护执行」  「批准·全自动」
             resume→discuss   execute+supervised execute+guarded  execute+autonomous
                     │               └────────┬────────┴─────────────────┘
                     │                        ▼
                     │            ┌────────────────────────────┐
                     │            │  Phase = execute           │
                     │            │  ask_user / submit_plan 移除│
                     │            │  写/命令按 Autonomy 判定    │
                     │            │  越界 → escalate 或 拒绝    │
                     │            └────────┬───────────────────┘
                     │                     ▼
                     │            ┌──────────────────┐
                     └────────────│  Phase = review  │→ 任务结束，回 idle
                                  └──────────────────┘
```

### 4.4 目标态判定矩阵

| Phase | Autonomy | `ask_user` | `submit_plan` | `write_file` | `run_command`（区内白名单） | `run_command`（越界/非白名单） |
|---|---|---|---|---|---|---|
| discuss | * | ✅ 弹问题卡 | ✅ 可调 | 🚫 无此工具 | 🚫 无此工具 | 🚫 |
| plan | * | ✅ 弹问题卡 | ✅ 弹计划卡 | 🚫 无此工具 | 只读命令可跑 | 🚫 |
| execute | supervised | 🚫 移除 | 🚫 移除 | 弹审批卡 | 弹审批卡 | 弹审批卡 |
| execute | **guarded** | 🚫 移除 | 🚫 移除 | **直接执行** | **直接执行** | escalate 卡 |
| execute | autonomous | 🚫 移除 | 🚫 移除 | **直接执行** | **直接执行** | **直接拒绝**，理由回灌模型 |
| 任意 | 任意 | — | — | 保护路径永远拒绝 | — | Capability 层硬拒绝 |

对比现状矩阵（2.3 节），核心变化是：**"不打扰"从 `access=full` 变成了 `phase=execute + autonomy=guarded`，而这个状态是由计划批准动作进入的，不是用户手动拨的开关。**

### 4.5 面向多 agent 的扩展点

三元组 `(Phase, Autonomy, Capability)` 下沉为**每个 agent 的执行契约**：

```python
@dataclass
class AgentContract:
    phase: Phase
    autonomy: Autonomy
    capability: CapabilityProfile  # 工作区范围、命令规则层、保护路径
    escalation_target: str  # "human" | "supervisor" | None
```

- **Supervisor** 与人对话，phase 走 `discuss → plan`，`escalation_target="human"`。
- **Worker subagent** 被派发时契约固定为 `phase=execute, autonomy=autonomous, escalation_target="supervisor"`——**天然不打扰人**，因为它连 `ask_user` 工具都没有，只有 `escalate` 指向 supervisor。
- Supervisor 收到 escalate 后，按 Codex `auto_review` 的思路自行判定：能决策就决策，不能才升级给人。

这正好落到用户描述的形态："人类发了高层指令，agent 在计划阶段了解清楚需求后全自动执行。这里可以出现人类插入，也可以无人插手。"

---

## 5. LangGraph / LangChain 官方指引与参考代码

cw 用的是 `langchain==1.3.14` + `langgraph==1.2.10`，正好落在官方 middleware API 成熟的版本上。上面每一条设计都有官方对应写法。

### 5.1 官方能力清单

**Middleware hooks**（`docs.langchain.com/oss/python/langchain/middleware/custom`）：

| Hook | 类型 | 时机 |
|---|---|---|
| `before_agent` / `after_agent` | node-style | 每次 invoke 一次 |
| `before_model` / `after_model` | node-style | 每次模型调用前后，返回 dict 更新 state |
| `wrap_model_call` | wrap-style | 包裹模型调用，**可改 tools 和 system prompt** |
| `wrap_tool_call` | wrap-style | 包裹工具调用，可拦截/重试/替换结果 |

**内置中间件**（21 个，与本议题相关的）：

| 中间件 | 用途 | cw 用得上吗 |
|---|---|---|
| `HumanInTheLoopMiddleware` | 工具调用前中断，支持 approve/edit/reject/respond | ✅ 已用，但配置错了 |
| `TodoListMiddleware` | 注入 `write_todos` 工具 + 提示词，任务规划与跟踪 | ✅ **执行期"不打扰但可见"的官方答案** |
| `ToolCallLimitMiddleware` | 全局/按工具限制调用次数（`thread_limit` / `run_limit`） | ✅ 全自动模式护栏（对标 Copilot `maxRequests`） |
| `ModelCallLimitMiddleware` | 限制模型调用次数，`exit_behavior="end"` | ✅ 同上 |
| `SummarizationMiddleware` / `ContextEditingMiddleware` | 长任务上下文管理 | ✅ 全自动长任务必需 |
| `LLMToolSelectorMiddleware` | 用小模型筛工具 | ⚠️ 工具多了再说 |
| Subagent middleware | 生成子 agent 隔离上下文 | ✅ 多 agent 演进路径 |

### 5.2 官方写法逐条对应 cw 的需求

#### (a) 阶段驱动的工具裁剪 —— 官方 dynamic tool selection

**这是"执行期物理上无法打扰你"的标准实现**，官方原文：

```python
@wrap_model_call
def select_tools(request: ModelRequest, handler) -> ModelResponse:
    relevant_tools = select_relevant_tools(request.state, request.runtime)
    return handler(request.override(tools=relevant_tools))

agent = create_agent(
    model="gpt-5.5",
    tools=all_tools,   # 全部工具需预先注册，中间件只做过滤
    middleware=[select_tools],
)
```

cw 落地骨架（替换 `agents.py:397-402` 的 `writable` 硬编码）：

```python
class PhaseToolGateMiddleware(AgentMiddleware[CoworkerAgentState]):
    state_schema = CoworkerAgentState  # 含 phase / autonomy

    def wrap_model_call(self, request, handler):
        phase = request.state.get("phase", "discuss")
        autonomy = request.state.get("autonomy", "guarded")

        allowed = set(READ_ONLY_TOOLS)
        if phase in ("discuss", "plan"):
            allowed |= {"ask_user", "submit_plan"}
        elif phase == "execute":
            allowed |= WRITE_TOOLS | EXEC_TOOLS
            if autonomy != "autonomous":
                allowed |= {"escalate"}
            # ask_user / submit_plan 不在集合里 → 模型看不到 → 无法打扰

        tools = [t for t in request.tools if t.name in allowed]
        return handler(request.override(
            tools=tools,
            system_message=SystemMessage(phase_prompt(phase, autonomy)),
        ))
```

同时用官方 `request.override(system_message=...)` 取代现在的双重注入（D9），system prompt 只有一个来源。

#### (b) 阶段状态放进 state schema —— 官方 state 扩展

```python
from typing_extensions import NotRequired
from langchain.agents.middleware import AgentState

class CoworkerAgentState(AgentState):
    phase: NotRequired[Literal["discuss", "plan", "execute", "review"]]
    autonomy: NotRequired[Literal["supervised", "guarded", "autonomous"]]
    task_id: NotRequired[str]        # 解决 D6：计划门 per-task 而非 per-session
    plan_approved_task: NotRequired[str]
```

`_is_plan_approved` 改为 `state.get("plan_approved_task") == state.get("task_id")`，不再扫全历史（D6 直接消失）。

#### (c) 统一审批判定 —— 用 `when` 谓词读 state，而不是构图时拆中间件

现在 `command_approval_middleware(access_mode)` 在**构图时**根据 access_mode 决定挂不挂中间件（`agents.py:466`），导致 full 档位下 `ask_user` 一起被干掉（D3）。官方支持 `when` 谓词，应该**永远挂载**，在谓词里读 state：

```python
HumanInTheLoopMiddleware(
    interrupt_on={
        "run_command": {
            "allowed_decisions": ["approve", "reject"],
            "when": lambda req: needs_command_approval(req),  # 读 req.state 的 phase/autonomy
        },
        "write_file":   {"allowed_decisions": ["approve", "reject"], "when": needs_write_approval},
        "ask_user":     {"allowed_decisions": ["respond", "reject"]},   # 永远中断，与权限无关
        "submit_plan":  {"allowed_decisions": ["approve", "reject", "regenerate"],
                         "when": lambda req: req.state.get("phase") in ("discuss", "plan")},
        "escalate":     {"allowed_decisions": ["respond", "reject"]},
    }
)
```

**这样自造的 `PlanApprovalMiddleware` 可以整个删掉**（D2 消失），三类中断回到同一个中间件，卡片顺序问题（D16 / 中断逆序）也一并解决。

#### (d) reject / regenerate 必须 resume —— 官方明确语义

官方原文（`docs.langchain.com/oss/python/langchain/human-in-the-loop`）：

> **reject**：`"The message is added to the conversation as feedback to help the agent understand why the action was rejected and what it should do instead."` 中间件会为被拒绝的调用**合成 ToolMessage**，然后继续执行。
>
> **respond**：`"Use respond for 'ask user' style tools where the tool's real implementation is the human's reply. The message content is returned directly as the tool result."` 并且明确警告：**"Do not use respond to deny a proposed action, because it tells the model that the tool completed successfully."**

cw 现在 `agents.py:1574-1593` 对 reject/regenerate **直接 `yield done` + `return`，从不调 `graph.astream(Command(resume=...))`**。这既违反官方语义，也造成 checkpoint 悬挂（D4）。正确写法：

```python
# 所有决策统一走 resume
resume_map = {interrupt_id: {"decisions": decisions}}
async for mode, chunk in graph.astream(Command(resume=resume_map), config=config, ...):
    ...
```

- `reject` → 中间件合成 ToolMessage，模型知道被拒，换路走或收尾。
- `regenerate` → 计划中间件的 regenerate 分支（`agents.py:1130-1136` 现在是死代码）会注入 "revise the plan" 消息，模型重新出计划（D5 消失）。
- 若确实要"硬停止整轮"，应该是一个**独立的 abort 语义**（前端 ✕ 或 Stop 按钮 → `AbortSignal`），而不是把 reject 挪用成 abort（D13）。

#### (e) 官方明确的 interrupt 坑 —— cw 踩了哪几个

官方 Interrupts 文档的四条铁律：

| 官方规则 | 原文 | cw 现状 |
|---|---|---|
| **resume 时节点从头重跑** | "the runtime restarts the entire node from the beginning—it does not resume from the exact line where interrupt was called. **Any code that ran before the interrupt will execute again.**" | ⚠️ 需审计 `PlanApprovalMiddleware.after_model` 在 interrupt 之前有无副作用（目前是纯读，暂安全；但 `record_runtime_interrupts` 写审批 JSON 的时机要确认幂等） |
| **不要用裸 try/except 包 interrupt** | interrupt 靠抛特殊异常暂停，裸 catch 会吞掉它 | ⚠️ 需全量 grep 检查 |
| **同节点多 interrupt 严格按索引匹配，不要条件跳过/动态循环** | "Matching is **strictly index-based**, so the order of interrupt calls within the node is important." | ⚠️ 同一 AIMessage 同时含 `submit_plan` + `run_command` 时，两个中间件各自 interrupt，顺序依赖第三方遍历顺序（D16） |
| **interrupt payload 必须 JSON 可序列化** | 不能传函数、类实例 | ✅ 当前是 dict，OK |

另外官方社区共识的最佳实践：**把 `interrupt()` 放进一个只做这件事的专用节点**，让重跑没有副作用。

#### (f) 全自动模式的护栏（官方内置，直接装）

```python
middleware = [
    PhaseToolGateMiddleware(),
    TodoListMiddleware(),                                      # 执行期进度可见，不打扰
    ToolCallLimitMiddleware(thread_limit=200, run_limit=80),   # 防跑飞
    ModelCallLimitMiddleware(thread_limit=100, run_limit=40, exit_behavior="end"),
    SummarizationMiddleware(model=small_model, trigger=("tokens", 120_000), keep=("messages", 20)),
    HumanInTheLoopMiddleware(interrupt_on={...}),
]
```

`TodoListMiddleware` 尤其值得注意：**"执行期不打扰"不等于"执行期黑箱"**。官方的 todo 工具让 agent 自己维护任务清单，前端可以实时渲染进度，用户随时知道它在干什么、还剩什么——这是所有主流 agent（Claude Code 的 TodoWrite、Codex 的 plan tool）的标配。cw 现在执行期用户只能看到流式文本。

#### (g) 多 agent 的官方路径

官方 middleware overview 明确：

> "Middleware is not a separate runtime: hooks run inside the compiled LangGraph that `create_agent` returns. You can drop the whole agent (middleware and all) into a larger StateGraph **as a node or subgraph**, and every middleware hook continues to run."

```python
worker = create_agent(model=..., tools=..., middleware=[PhaseToolGateMiddleware(), ...])

graph = (
    StateGraph(SupervisorState)
    .add_node("supervisor", supervisor_node)
    .add_node("worker", worker)          # 整个 agent 作为一个节点
    .add_conditional_edges("supervisor", route)
    .compile()
)
```

要点：子图的 checkpointer 作用域（per-invocation vs per-thread）需要显式决定，官方 "Use subgraphs" 文档有完整说明。cw 现在用 SQLite checkpointer，多 agent 时 thread_id 的层级设计要提前想好。

---

## 6. 落地改造清单

按依赖顺序分三批。每条给出文件位置。

### 批次 1：止血（清掉自相矛盾，不改产品行为）

| # | 动作 | 位置 |
|---|---|---|
| 1.1 | reject / regenerate 改走 `Command(resume=...)`，删掉直接 `return` 的终止分支 | `agents.py:1574-1593` |
| 1.2 | 把 ✕ 与「拒绝」拆开：✕ → abort（AbortSignal），拒绝 → resume | `PendingDocks.tsx:411`、`App.tsx:1534` |
| 1.3 | 删掉 `workspace.py:483-530` 的第二套审批死代码，或把它归并到统一 digest 格式 | `agents.py:1295/1396/1608/1649` |
| 1.4 | `restorePendingForSession` 支持 `'plan'` kind | `App.tsx:1176` |
| 1.5 | 修 `record.mode as WorkMode` 类型错用导致的假 Build 徽章 | `App.tsx:1234` |
| 1.6 | always-allow 改为**命令前缀 + 项目级**，并加撤销 API；UI 文案与实际粒度对齐 | `workspace.py:580-583`、`PendingDocks.tsx:154` |
| 1.7 | 清理死代码：`modePrefs.ts`、`SYSTEM_PROMPT`、`_WRITE_TOOL_NAMES`、plan 自定义流式通道、孤儿 i18n 键 | 多处 |

### 批次 2：引入三轴模型（核心）

| # | 动作 | 位置 |
|---|---|---|
| 2.1 | `CoworkerAgentState` 增加 `phase` / `autonomy` / `task_id` / `plan_approved_task` | `agents.py:53-56` |
| 2.2 | 新增 `PhaseToolGateMiddleware`，用 `wrap_model_call` + `request.override(tools=..., system_message=...)` 实现阶段工具裁剪；**删除 `writable` 参数与双重 prompt 注入** | `agents.py:258-402`, `853-871`, `1205-1214` |
| 2.3 | **删除 `PlanApprovalMiddleware`**，把 `submit_plan` 并入 `HumanInTheLoopMiddleware` 的 `interrupt_on`，用 `when` 谓词读 state | `agents.py:1045-1173`, `460-498` |
| 2.4 | `command_approval_middleware` 改为**永远挂载**，全部判定下沉到 `when` 谓词；`ask_user` 中断与权限解耦 | `agents.py:460-498` |
| 2.5 | 计划卡出口从 3 个（批准/重新规划/拒绝）改为 4 个，对标 Claude Code：**继续讨论 / 批准·逐步确认 / 批准·守护执行 / 批准·全自动**。批准动作同时写入 `phase=execute` 和 `autonomy=X` | `PendingDocks.tsx:332-378`、`main.py:884-946` |
| 2.6 | 新增 `escalate` 工具作为执行期唯一打扰通道 | `agents.py:397` |
| 2.7 | 引入可配置规则层（`deny → ask → allow` + glob），替代硬编码 `ALLOWED_COMMANDS`；命令按子命令拆解匹配 | `workspace.py:38-51`, `588-593` |
| 2.8 | 前端：移除 access_mode 二态 toggle，改为 Autonomy 三档 + Phase 只读徽章；access/phase 单一真源改为后端 session，前端读回 | `App.tsx:99`、`ChatInput.tsx:367-372` |

### 批次 3：全自动可用性与多 agent 预留

| # | 动作 |
|---|---|
| 3.1 | 装 `TodoListMiddleware`，前端渲染执行期任务进度（不打扰但可见） |
| 3.2 | 装 `ToolCallLimitMiddleware` + `ModelCallLimitMiddleware`，全自动模式护栏 |
| 3.3 | 装 `SummarizationMiddleware` / `ContextEditingMiddleware`，长任务上下文 |
| 3.4 | 加 doom-loop 检测（同工具同参数连续 N 次 → 强制 escalate），对标 opencode |
| 3.5 | 把 `(phase, autonomy, capability, escalation_target)` 抽成 `AgentContract`，`AgentMode` 从 `Literal["single"]` 扩为可注册；worker agent 作为 subgraph 节点挂进 supervisor StateGraph |
| 3.6 | escalate 的 `escalation_target="supervisor"` 路径 + supervisor 侧的 auto-review 判定（对标 Codex `approvals_reviewer`） |

---

## 7. 需要你拍板的几个点

1. **Phase 由谁决定？** 三个选项：(a) 纯自动（模型调 `submit_plan` 即进 plan，批准即进 execute）；(b) 自动 + 用户可强制（像 Claude Code 的 Shift+Tab 可手动进 plan mode）；(c) 纯手动。建议 **(b)**——默认自动，但保留用户强制进入讨论态的入口。

2. **默认 Autonomy 是哪一档？** 建议 `guarded`（区内自由、越界才问）。Codex 的默认预设 `Auto` 正是这一档，是行业默认。

3. **"讨论 vs 任务"怎么区分？** 现在 system prompt 说 "If the user is simply asking a question... answer directly without calling submit_plan"（`agents.py:1212-1213`），靠模型自觉。可选升级：`before_model` 里跑一个轻量分类器。建议**先靠工具集约束 + prompt，不加分类器**——因为 discuss 阶段本来就没有写工具，分错的代价很低。

4. **`always allow` 的生命周期**：opencode 是**会话级**，Claude Code 是**项目 + 命令前缀级持久**。cw 现在是全局永久且不可撤销，最激进。建议改为**项目级 + 命令前缀 + 可在设置页撤销**。

5. **执行期用户想插话怎么办？** 现在 pending 卡片会把 composer 整个替换掉（`App.tsx:1526-1571`）。全自动执行期必须允许用户随时输入（作为下一轮的补充指令或中断指令）。建议：卡片与输入框**并存**，不再互斥。

---

## 附录：关键证据索引

| 主题 | 文件:行 |
|---|---|
| 三个 mode 的枚举 | `backend/coworker/agents.py:23-26` |
| `normalize_work_mode` 默认落 build | `agents.py:172-181` |
| `runtime_instruction` 唯一的模式差异 | `agents.py:184-189` |
| 工具装配 + `writable` 硬编码 True | `agents.py:397-402`；调用点 `1296/1397/1609/1650` |
| `command_approval_middleware` + full 短路 | `agents.py:460-498` |
| `ask_user` 工具体（awaiting_user 死 JSON） | `agents.py:331-345` |
| `PlanApprovalMiddleware` + `interrupt()` | `agents.py:1045-1173`，interrupt 在 `:1115` |
| `_is_plan_approved` 扫全历史 | `agents.py:1070-1086` |
| `wrap_tool_call` 软阻断（不含 run_command） | `agents.py:1153-1162`、`405-406` |
| 中断 payload 构造 | `agents.py:589-616` |
| reject/regenerate 硬终止 | `agents.py:1574-1593` |
| resume 回灌 | `agents.py:1656-1660` |
| `effective_access` 三处复制 | `agents.py:1291/1391/1564` |
| 命令 digest | `backend/coworker/workspace.py:580-583` |
| `ALLOWED_COMMANDS` 白名单 | `workspace.py:38-51`、硬拒绝 `588-593` |
| 决策映射 | `backend/main.py:884-946` |
| 前端 accessMode 单一 state | `frontend/src/App.tsx:99` |
| 请求体不含 work_mode | `App.tsx:440-456` |
| 三种卡片组件 | `frontend/src/components/PendingDocks.tsx:103/169/332` |
| ✕ = reject | `PendingDocks.tsx:411-421`、`App.tsx:1534-1536` |
| composer 被卡片替换 | `App.tsx:1526-1571` |

**外部参考**

- Claude Code 权限模式：`code.claude.com/docs` — Choose a permission mode / Configure permissions
- Claude Code Plan Mode 与 ExitPlanMode 出口选项
- OpenAI Codex：`learn.chatgpt.com/docs/agent-approvals-security` — approval_policy × sandbox_mode 组合表、`approvals_reviewer = "auto_review"`
- opencode：`opencode.ai/docs/permissions/`（含 `question`、`doom_loop` 权限键）、`opencode.ai/docs/agents/`（per-agent permission）
- VS Code Copilot：`chat.tools.terminal.autoApprove`（子命令拆解）、`chat.agent.maxRequests`
- LangChain middleware：`docs.langchain.com/oss/python/langchain/middleware/{overview,custom,built-in}`
- LangChain HITL：`docs.langchain.com/oss/python/langchain/human-in-the-loop`
- LangGraph Interrupts 铁律：`docs.langchain.com/oss/python/langgraph/human-in-the-loop`

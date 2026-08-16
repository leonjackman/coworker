# 记忆治理（Agent 自主记忆 + 按需注入）— 开发任务清单

目标：落实用户锁定的记忆治理方案，使 CW 的记忆具备「agent 自主治理 + 读端按需注入」能力，取代现有的「读端软警告、写端无上限」状态。

背景与决策（已确认）：
- **理念**：该 agent 自动治理的由 agent 自己管，该用户管的由用户管；用户不需操心 agent 记忆；agent 自动整理/合并/提炼/压缩/优化；不全量存文本；好的治理省 token。
- **参考**：Codex（extract_model + consolidation_model + 后台空闲触发 + 无预算旋钮）、OpenClaw Dreaming（deep 整合 + 安全护栏 + DREAMS.md 审查日记）、Claude Code（索引常驻 + 主题按需）、LangGraph（collection over-insert → 需 reconciliation）。
- **常驻注入边界**（已确认）：
  - 系统记忆（根 MEMORY.md / USER.md / AGENT.md）
  - 项目 `BASE/` + `BASE/PROJECT/` 全部（用户维护 + 系统生成 GOALS/CONTEXT）
  - **整个 agent/BASE/**（SOUL/AGENT/MEMORY + 自建主题文件 RULES.md 等）
  - 部门：名册 + 部门 GOALS/CONTEXT/MEMORY 都常驻
  - **不常驻（on-demand）**：agent `SESSIONS/*.md` 会话记录（memory_read 按需读取）
- **dream 触发**：会话结束空闲后（Codex 式，默认 5 分钟无新消息且无进行中流）。
- **设置页**：精简为 `记忆总开关` + `自动记忆` 两开关，移除 char_limit / nudge_interval / extract_model UI。
- **organize 工具**：agent 会话内可主动调用（hot-path 整合）。

最终形态（注入侧）：
```
常驻（每会话注入）: system + project(BASE+PROJECT) + agent/BASE 全部 + 部门(名册+GOALS/CONTEXT/MEMORY)
按需（memory_read）: agent SESSIONS/*.md
硬截断兜底        : inject_char_limit 超限截断常驻块 + 提示用 memory_read
后台治理          : 会话结束空闲 → 提取 + dream 整合（护栏保护，写 DREAMS.md）
```

---

## 阶段 0 — 设置页去旋钮

方向：设置页记忆分组精简为两开关；后端保留内部默认值；废弃字段从类型/响应中移除。

- [x] **`frontend/src/types.ts`**：
  - [x] `MemorySettings` 删除 `char_limit` / `nudge_interval` / `extract_model` 字段
  - [x] `MemorySettingsPatch` 同步删除三字段
- [x] **`backend/main.py`**：
  - [x] `get_memory_settings` 响应不再返回三废弃字段（或返回但前端不渲染——推荐后端直接不返回）
  - [x] `save_user_memory_settings` 丢弃未知字段，仅接受 `enabled` / `auto_extract`（幂等容错）
- [x] **`backend/coworker/memory/memory_manager.py`** `MemoryConfig`：
  - [x] `char_limit` 改名/复用为内部 `inject_char_limit`（默认 4000，硬上限，不进 UI）
  - [x] 保留内部默认：`nudge_interval=3`、`extract_model=""`（空 → 主模型）
- [x] **`frontend/src/components/settings/SettingsView.tsx`**：
  - [x] 记忆分组改为两个开关：`memory_enabled`（记忆总开关）+ `memory_auto_extract`（自动记忆 · agent 自主治理）
  - [x] 删除三字段对应的设置项渲染
- [x] **`frontend/src/App.tsx`**：`changeMemorySettings` 传参同步（只传 enabled/auto_extract）
- [x] **locales（7 语言）**：更新两开关文案，删除三字段标签

## 阶段 1 — 写端纪律（防全量存文本）

方向：memory tool 拒绝整段粘贴，引导 agent 写精炼事实。

- [x] **`backend/coworker/agents.py`** memory tool（:653）：
  - [x] 新增 `_looks_like_paste(content)` 检查：`len(content) > PASTE_THRESHOLD(400)` 且与当前对话原文高重合 → `add`/`replace` 拒绝，返回「请提炼为精炼要点，勿整段粘贴」
  - [x] `add`/`replace` 分支先做该检查，命中返回错误且不改写
  - [x] 工具 description 强化：MEMORY.md 是精炼索引；详细内容用 `name` 写主题文件；写前先想是否与现有条目重复/可合并，优先 `replace`
- [x] **`backend/coworker/memory/memory_store.py`**：
  - [x] 确认 `add_block` 精确去重（:191-194）、`replace_block` substring 首个匹配（:215）——已满足，仅核对

## 阶段 2 — 后台 dream 整合（核心自主治理）

方向：会话结束空闲后触发「提取 + 整合」，安全护栏保护，写 DREAMS.md 审查日记。与 auto_extract 合并为统一「自动记忆」流水线。

- [x] **`backend/coworker/memory/memory_manager.py`**：
  - [x] `MemoryConfig` 加内部字段：`max_prior_loss=0.25`、`dream_idle_seconds=300`（不进 UI）
  - [x] `after_turn`（:176）：改为「重置 idle 定时器」模型——有进行中流则取消；settle 后重启 idle timer；到点触发 `dream(session_id)`（复用现有 `loop.create_task` 后台执行框架）
  - [x] 新增 `async def _dream_async(session_id)`：读取 agent `BASE/MEMORY.md` + 本期候选 → 整合模型重写 → 护栏校验 → 写回或回退
  - [x] 新增 `write_consolidated(target_rel, new_content, source_refs)`：护栏落盘
  - [x] 现有 `_extract_async`（:203）与 dream 合并为统一后台流水线；由 `config.auto_extract`（「自动记忆」开关）控制，关则提取与整合都不跑
- [x] **`backend/coworker/memory/auto_extract.py`**：
  - [x] 新增整合提示词 `CONSOLIDATE_PROMPT`（仿 OpenClaw deep 阶段）：输入旧 MEMORY.md + 新候选 + 保留 Source 引用（`<!-- source: session-id -->`），输出合并/去重/替换过时后的新 MEMORY.md
  - [x] 新增 `run_consolidation(llm, old_content, candidates, session_id, max_prior_loss)`：
    - 解析结构化输出；失败 → 返回 `None`（调用方回退纯追加）
    - 旧条目保留率 ≥ 75%（超限拒绝）
    - 新版本保留 Source 引用、且 ≤ `inject_char_limit`
  - [x] `build_extract_llm` 复用（整合用同模型/主模型）
- [x] **DREAMS.md 审查日记**：
  - [x] `_dream_async` 成功后写 agent 目录 `DREAMS.md`：「新增/合并/替换/删除 N 条 + 摘要 + 时间」；失败回退追加时记录「整合跳过，纯追加」
  - [x] 记忆面板已按文件扫描，agent/BASE 下的 DREAMS.md 自动可见（核对 scanner 是否纳入——若 agent.base 已扫描所有 *.md 则无需改动）
- [x] **护栏汇总**（实现于 `run_consolidation` + `_dream_async`）：
  - [x] 保留 ≥75% 旧条目，超限拒绝重写
  - [x] 保留 Source 引用
  - [x] 新版本 ≤ inject_char_limit
  - [x] 结构化解析失败 / 模型不可用 → 回退纯追加（不丢数据）

## 阶段 3 — 读端按需注入

方向：SESSIONS 转按需；新增 memory_read 工具；format_memory_prompt 硬截断兜底。

- [x] **`backend/coworker/memory/memory_discovery.py`** `injected()`（:196）：
  - [x] 去掉 `nodes.extend(aview.sessions)`（:216），SESSIONS 转按需
  - [x] 其余保持不变（system / project base+project / agent core+base / team 全常驻）
- [x] **`backend/coworker/agents.py`** 新增 `memory_read` 工具（读端 on-demand）：
  - [x] `memory_read(scope="agent", file="SESSIONS/xxx.md")`：读取并返回指定文件全文
  - [x] 仅读不改写；越权（他人 agent / 不可读文件）拒绝
  - [x] 工具 description：说明 SESSIONS 会话记录不常驻注入，需要回顾历史时用此工具读取
- [x] **`backend/coworker/memory/memory_prompt.py`** `format_memory_prompt`（:13）：
  - [x] `char_limit` 语义改为**硬截断**：累计超 `inject_char_limit` 时截断常驻块到该值 + 追加 `> ... (更多历史会话记录可通过 memory_read 按需读取)`
  - [x] 被截断内容不丢（文件在磁盘，memory_read 可取）
  - [x] 移除原 `<budget_warning>` 纯提示逻辑（或被截断提示取代）
- [x] 记忆面板核对：SESSIONS 仍显示可编辑（用户侧不丢）；仅注入端变化

## 阶段 4 — 测试验证

- [x] **`backend/coworker/memory/selftest.py`** 新增：
  - [x] `injected()` 不再含 agent sessions 节点；system / project base+project / agent core+base / team 仍含
  - [x] `format_memory_prompt` 超限硬截断 + 追加按需提示
  - [x] `memory_read` 读取 SESSIONS 成功；越权读他人 agent 拒绝
  - [x] 写端纪律：>400 字且高重合被拒；精炼短条目正常写入
  - [x] dream 护栏：整合不丢 >25% 时拒绝；解析失败回退纯追加；成功写 DREAMS.md；新版本 ≤ 预算
- [x] **`backend/coworker/memory/stress_test.py`**：
  - [x] HTTP 冒烟：`memory_read` 端点、`memory_write` 大段粘贴拒绝、设置两开关、DREAMS.md 生成
- [x] **全量验证**：
  - [x] `cd backend && ./venv/bin/python -c "import main"` + `py_compile` 相关文件
  - [x] `./venv/bin/python coworker/memory/selftest.py`（全绿）
  - [x] `./venv/bin/python coworker/memory/stress_test.py`（全绿）
  - [x] `export PATH="$HOME/.nvm/versions/node/v22.23.1/bin:$PATH" && cd frontend && npx tsc --noEmit`
  - [x] `npm run build`
  - [x] `node --check ../electron/main.js` / `../electron/preload.js`（如涉 IPC）

## 关键文件清单

| 文件 | 改动 |
|---|---|
| `backend/coworker/memory/memory_discovery.py` | `injected()` 去掉 sessions |
| `backend/coworker/memory/memory_prompt.py` | 硬截断 + 按需提示 |
| `backend/coworker/memory/memory_manager.py` | `dream()` 整合流水线 + idle 触发 + 护栏 + DREAMS.md；`after_turn` 重构 |
| `backend/coworker/memory/auto_extract.py` | `CONSOLIDATE_PROMPT` + `run_consolidation`（护栏） |
| `backend/coworker/agents.py` | memory tool 写端纪律 + `memory_read` 工具 |
| `backend/main.py` | 设置字段调整（移除三字段返回/保存） |
| `backend/coworker/memory/selftest.py` | 新用例 |
| `backend/coworker/memory/stress_test.py` | HTTP 冒烟 |
| `frontend/src/types.ts` | MemorySettings 删三字段 |
| `frontend/src/components/settings/SettingsView.tsx` | 两开关 |
| `frontend/src/App.tsx` | 设置传参同步 |
| `frontend/src/locales/*.json`（7） | 文案 |

## 明确不做（本轮边界）
- 不做记忆面板的 dream 手动触发 UI（DREAMS.md 可看即可）
- 不做语义向量检索（memory_read 按文件名读取）
- 不做 organize 工具的模型重写审批流（自动执行 + DREAMS.md 记录）
- 不动会话 checkpoint / 短程记忆 / 注入优先级排序

## 完成记录

全部阶段已实施并验证通过。

- 阶段 0（去旋钮）：设置页记忆分组精简为「记忆总开关 + 自动记忆」两开关；`MemorySettings`/`MemorySettingsPatch`/`MemoryStatusResponse` 删除 char_limit/nudge_interval/extract_model；后端 `/api/memory/settings`（GET/POST）只返回/接受 enabled+auto_extract；`MemoryConfig.char_limit` 改为内部 `inject_char_limit`（默认 4000，硬上限）+ 保留 `nudge_interval`/`extract_model`/`max_prior_loss`/`dream_idle_seconds` 内部默认；7 语言文案更新。
- 阶段 1（写端纪律）：memory tool 新增 `_looks_like_raw_paste`（>400 字且高引用、或 ≥1200 字 → 拒绝「请提炼为精炼要点」）；工具 description 强化（MEMORY.md 是精炼索引、优先 replace/合并、详细内容写主题文件）。
- 阶段 2（dream 整合）：`after_turn` 改为 Codex 式 idle 触发（`dream_idle_seconds=300`，每次新 turn 取消+重排）；新增 `note_turn_active`（main.py /chat、/chat/stream 开头调用）；`_dream_async` = 提取（`_stage_candidates`）+ `_consolidate_now`（护栏：保留 ≥75% 旧条目、保留 Source、≤inject_char_limit、解析失败/超限回退纯追加）+ `_write_dream_diary`（agent/BASE/DREAMS.md）；`auto_extract.run_consolidation` 新增；与 auto_extract 合并为统一「自动记忆」流水线。
- 阶段 3（按需注入）：`injected()` 不再注入 agent SESSIONS（SESSIONS 转 on-demand）；新增 `memory_read` 工具（readonly 也挂载，读任意内存根内文件）；`format_memory_prompt` 改为硬截断（超 `inject_char_limit` 截断 + `memory_read` 提示，数据不丢）。
- 阶段 4（验证）：selftest 111→123、stress 99→101 全绿；新增用例（sessions 不常驻但可按需读、paste guard、consolidation 护栏、settings 两字段）；`import main`/py_compile、tsc + vite build（node 22）+ electron node --check 通过；HTTP 冒烟（9537）：settings 只返回两字段、save 两开关、discover agent sessions=0。

### 实现说明 / 偏差
- paste guard 在 agent memory tool 层（`/api/memory/write` HTTP 直连端点保持原样，供 UI/测试直接读写）。
- `note_turn_active` 挂在 main.py 的 /chat 与 /chat/stream 开头（流开始即取消待定 dream）。
- 单条超长 turn（>300s）时 dream 可能与该 turn 并发，但受护栏保护且后台执行，不会丢数据。


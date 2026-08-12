# Coworker MVP 修复任务清单

> 来源：全局审计（2026-08-12）。决策已确认：**①②③④⑤ = B B A A A**，⑥ 做（打包分发除外）。
>
> - ① 记忆并发 = **B**（原子写 + 最后一次写胜，不做并发检测/合并）
> - ② 文件陈旧守卫 = **B**（维持单轮，依赖 git + 回滚兜底，不做跨轮持久化）
> - ③ install_skill = **A**（装完可见，不做 HITL 硬审批）
> - ④ 安全加固 = **A**（绑 127.0.0.1 + URL/路径校验，不上 token/Origin 深度防御）
> - ⑤ 后台流 = **A**（真后台更新，重构 App.tsx 流管理）
> - ⑥ 运行记录保留/导出 + 密钥加密 = **做**；打包分发 = **明确不做**
>
> 开发顺序：阶段 1 → 验证 → 阶段 2 → 验证 → 阶段 3 → 验证。一次只做一个阶段，完成勾选并验证后再进入下一阶段。
>
> 行号基于审计时点（2026-08-12），改动后可能漂移，以实际代码为准。

---

## 阶段 1｜安全 + 数据完整性 + 稳定性（P0/P1）

### 1.1 安全（决策④=A）

- [x] **S1 后端默认绑定改 127.0.0.1**
  - 预期：直接 `python backend/main.py` 启动时不再暴露到局域网（launcher 已传 `--host 127.0.0.1`，此项为防御性兜底）
  - 改法：`backend/main.py:2900` `host="0.0.0.0"` → `host="127.0.0.1"`

- [x] **S2 `/ws/terminal` 增加 Origin 校验**
  - 预期：WebSocket 不受 CORS 约束，本机浏览器恶意网页可直连 `ws://127.0.0.1:9527/ws/terminal` 拿到交互 shell；校验后只放行本地可信来源
  - 改法：`backend/main.py:2474` 在 `websocket.accept()` 前读取 `websocket.headers.get("origin")`，仅允许 `http://localhost:3000`、`http://127.0.0.1:3000`、`http://localhost:5173`、`http://127.0.0.1:5173`、`file://` 前缀；不匹配则 `websocket.close(code=1008)` 后 return

- [x] **S3 provider 测试/拉模型接口复用 URL 校验（修 SSRF）**
  - 预期：`test_provider_connection` / `fetch_models` 不再接受任意 `base_url`；同时保留 127.0.0.1/局域网/Ollama 放行，不修过头
  - 改法：`backend/coworker/providers.py:180-214` 在这两个函数入口调用现成的 `validate_base_url(base_url, provider_type)`（已有逻辑在 `providers.py:221-236`），非法直接抛 `ValueError`；`backend/main.py:2274-2285` 的 HTTPException 处理保持

- [x] **S4 技能市场安装 slug 消毒（修路径穿越）**
  - 预期：恶意 slug（如 `../../tmp/evil`）不能用来创建/删除任意目录
  - 改法：`backend/coworker/skills/skill_market.py:301-302` 在 `install_dir / slug` 与 `mkdir` 之前，用白名单正则 `^[a-z0-9._-]+$` 校验 slug，非法抛 `ValueError`；同时校验最终 `install_dir` 必须位于安装根内（`resolve()` 后 containment）

- [x] **S5 技能命令 `file:` 路径 containment 校验（修路径穿越）**
  - 预期：恶意技能 frontmatter 声明 `file: ../../.ssh/id_rsa` 时拒绝，不再经 `/skills/{name}?command=` 泄露任意文件
  - 改法：`backend/coworker/skills/skills.py:169-172` `_parse_commands` 对 `cmd.file` 做 `resolve()` 后 containment 检查（必须位于 `skill.base_dir` 内），非法该命令不入列表；`backend/coworker/skills/skill_manager.py:244-247` `read_command_body` 同样二次校验

- [x] **S6 Electron 基础防御：sandbox + CSP + 窗口导航限制**
  - 预期：渲染进程沙箱化、无 CSP 时注入防御、markdown 链接不再用不受控的新窗口打开
  - 改法：
    - `electron/main.js:255-259` webPreferences 加 `sandbox: true`
    - `frontend/index.html` 加 CSP meta（默认 `default-src 'self'`，放行 `data:` 图片、`https:` 远端模型无关资源按需）
    - `electron/main.js` 注册 `webContents.setWindowOpenHandler`（外部链接用 `shell.openExternal`，不新建 BrowserWindow）与 `will-navigate` 拦截（仅放行 dev server / 本地 dist）

- [x] **S7 `/skills/validate` 路径限制到技能根**
  - 预期：验证接口不再任意读本机文件（存在性/正文泄露 oracle）
  - 改法：`backend/main.py:2874-2896` 对 `target` 先 `resolve()`，校验其位于任一技能扫描根目录内（复用 `skill_discovery` 的 roots），越界返回 403

- [x] **S8 MCP OAuth token / 服务器 secrets 落盘权限 0600**
  - 预期：同机其他账户读不到 OAuth token 与 MCP API key
  - 改法：`backend/coworker/mcp/mcp_oauth.py:60-63` 与 `backend/coworker/mcp/mcp.py:212-219` 写入后 `os.chmod(path, 0o600)`（或写临时文件时直接设权限再 rename）

### 1.2 数据完整性（决策①=B + 并发竞态）

- [x] **D1 记忆并发：原子写 + 最后一次写胜 + 修误报（决策①=B）**
  - 预期：写记忆永远成功不报错；极端并发下后写覆盖先写；记忆正文含单行 `§` 不再清空文件；`replace` 多匹配不再生成重复条目；Windows 首次新建文件不再锁失败
  - 改法（`backend/coworker/memory/memory_store.py`）：
    - `_write_locked`（62-88）：改为 temp 文件写入 + `os.replace` 原子替换；**删除**锁外 round-trip 校验抛 `MemoryError` 的逻辑（保留 fcntl 锁仅串行化写）；`.bak` 兜底逻辑一并移除
    - `add`/`replace`（124-155）：入口校验或转义条目内单行 `§`（拒绝或替换为全角），避免解析分裂；`replace` 改为只替换第一个匹配（或明确多次语义），不再写重复条目
    - `_open_locked`（213-228）：Windows 下先写一行空内容再锁（避免锁 0 字节文件失败），macOS 分支不受影响

- [x] **D2 会话/变更/提供方/项目 JSON 写改原子写**
  - 预期：写进程崩溃时不再出现截断/损坏的 JSON，状态不丢
  - 改法：各写入点改为 temp 文件 + `os.replace`：
    - `backend/coworker/sessions.py:153-158`
    - `backend/coworker/changes.py:131-135`
    - `backend/coworker/providers.py:79-81`
    - `backend/coworker/projects.py:82-87`

- [x] **D3 trace/audit JSONL "追加+trim" 加锁防丢事件**
  - 预期：并发写审计日志时，trim 不再把另一流的追加行一起清掉
  - 改法：`backend/coworker/workspace.py:1001-1008` 与 `backend/coworker/traces.py:50-55` 在"追加一行 + trim 重写"整个临界区加进程内 `threading.Lock`（每文件一把）；如可再叠加 fcntl 文件锁更稳

- [x] **D4 删除/回滚/重新生成前检查活跃流**
  - 预期：流进行中删除/回滚/重生成不再删掉正在写入的 checkpoint，不再出现静默丢失
  - 改法：`backend/main.py:1548-1554`（delete_session）、`1684-1685`（rollback）、`1729-1730`（regenerate）入口用 `agent_registry.checkpoint_manager.active_sessions()` 检查，命中则返回 409 明确错误；SSE 流路径确保 `mark_active`/`mark_idle` 成对（当前 `chat_stream` 若有遗漏一并补上）

- [x] **D5 `walk_files`/`list_dir`/`build_tree` symlink 防环 + 容错**
  - 预期：工作区放自引用 symlink 不再让 `search_text` 无限递归；dangling symlink 不再导致 500
  - 改法：`backend/coworker/workspace.py:947-960` 的 `walk_files` 加 `visited`（resolved inode/真实路径）集合 + 深度上限；`:821-857` 的 `list_dir`/`build_tree` 对 `child.stat()`/`iterdir()` 抛 `OSError` 时跳过该条目而不是上抛

- [x] **D6 同步 `/chat` 与 `generate_title` 移出事件循环**
  - 预期：慢请求（LLM 调用 / 最长 60s 命令）不再卡死整个服务器（心跳、其它会话 SSE）
  - 改法：`backend/main.py:622` `runtime.run(...)` 包 `await asyncio.to_thread(...)`；`main.py:1589-1590` `generate_title(...)` 同理

- [x] **D7 goal_resume 暂停时持久化本轮内容（修丢数据）**
  - 预期：用户暂停 goal → 恢复 → 再暂停后，该轮已完成内容不再丢失（刷新后仍在）
  - 改法：`backend/main.py:1461-1462` `goal_paused` 分支对齐 `chat_stream` 的 `main.py:996-1010`：先 `session_store.append_message(..., content=已产出的 content, parts=_merge_goal_parts(goal_parts_accum))` 再置 `terminal_sent = True`（失败仍 `except KeyError: pass`）

- [x] **D8 `_goal_locks`/`_goal_cancel_events` 正常结束清理**
  - 预期：长期运行不再缓慢泄漏内存
  - 改法：`backend/main.py` 在 `goal_done`、正常流结束、`goal_delete`、`goal/stop` 路径统一 `_goal_locks.pop(session_id, None)`；`_goal_cancel_events` 在正常完成时也 pop（不只 `_on_error`/`finally`）

- [x] **D9 MCP 子进程泄漏修复（经真机验证，部分项误判）**
  - 预期：反复点"测试连接/检查全部"不再累积僵尸 stdio 子进程；挂死的 MCP 服务器不再每次模型调用都新起一个进程并卡 40s
  - 实测结论：
    - `mcp_test.py` 测试后从不 close → **误判**：当前 langchain-mcp-adapters 版本下 `run_blocking` 的 `loop.close()` 会自动清理子进程（用最小 stdio MCP 服务器连跑 3 次、含 5s 超时挂死场景，均无残留进程）
    - `run_blocking` 超时路径 → **误判**：`asyncio.wait_for` 取消 + anyio 任务组清理会终止子进程（实测无残留）
    - `mcp_session.py` hung 握手 `abandon()` 只置 stop → **有界泄漏**：`_connecting` 去重保证每挂死服务器只泄漏 1 个子进程且 shutdown 时清理；尝试 `owner.cancel()` 实测无法清理 enter 中途的子进程反而增加取消风险，故保留原 stop 机制，记为已知限制
  - 状态：保持现状，无需改动

### 1.3 稳定性

- [x] **U1 Electron 单实例锁**
  - 预期：连点两次启动只开一个实例，不再双窗口/双渲染进程打同一后端
  - 改法：`electron/main.js` 顶部 `app.requestSingleInstanceLock()`，未取得锁 `app.quit()`；监听 `second-instance` 聚焦已有窗口（已实现于 main.js:342-355）

- [x] **U2 环境变量解析失败回退默认值**
  - 预期：`COWORKER_CHECKPOINT_CAP` 等写错类型不再导致启动崩溃
  - 改法：`backend/coworker/config.py` 新增 `_env_int()` 安全解析（`try/except` + `logger.warning`），所有 int 型环境变量走该函数；已实测 `COWORKER_CHECKPOINT_CAP=abc` 等不再崩溃、合法值仍生效

### 阶段 1 验证清单

- [x] `backend/venv/bin/python -c "import main"`（backend 目录下）通过
- [x] `node --check electron/main.js && node --check electron/preload.js` 通过
- [x] `cd frontend && npx tsc --noEmit` 通过
- [ ] `COWORKER_SKIP_DESKTOP=1 ./coworker_desktop.command` 冒烟：登录页/设置可用（未执行 launcher 冒烟，改用 uvicorn+静态服务器+ego-browser 真机验证）
- [x] 连续点 MCP"测试连接" 5 次后 `ps aux | grep <mcp-server-cmd>` 无残留子进程（最小 stdio 服务器连测 3 次 + 挂死场景，均无残留）
- [x] 在 workspace 放 `ln -s . loop` 后调用搜索不再卡死（walk_files/build_tree/list_dir 均已实测）

### 阶段 1 额外修复（真机测试中发现）

- [x] **chat_stream 崩溃修复**：`agents.py:_open_sync_checkpointer` 每个连接重复执行 `PRAGMA auto_vacuum=INCREMENTAL`，在 async saver 写 checkpoint 时立即抛 `sqlite3.OperationalError: database is locked`，导致 SSE 流 500 / 前端"网络请求失败"。已移除该冗余 pragma（auto_vacuum 是 DB 持久属性，checkpoints.py 启动时已设），并给 `has_runtime_checkpoint` 增加 `OperationalError` 兜底返回 False。已用真实 vllm provider 通过 curl SSE 与 ego-browser 前端验证恢复。

---

## 阶段 2｜记忆体验 + 语言规则 + 对话正确性

- [x] **L1 语言规则（LLM 默认跟随系统 UI 语言）**
  - 预期：用户设置的系统语言是唯一权威；无论用户发什么语言，LLM 都默认用系统语言回复；用户消息里明确要求"用英文回答"时临时切换；编辑/重新生成/恢复 goal/自动标题全部一致跟随系统语言
  - 改法：
    - `backend/main.py:1596-1600` `EditMessageRequest` 加 `language: Language = "zh"`
    - 编辑端点（~1740）与重新生成端点（~1820）改 `language = request.language`，删除 `request_language_for_session(session)` 调用
    - `backend/main.py:1607-1608` 删除 `request_language_for_session`（连同永不写入的 `_language`）
    - `backend/coworker/agents.py:58` TITLE_SYSTEM_PROMPT 的 "Use the same language as the user's first message" 改为 `Reply in {language_name}`；`generate_title`（agents.py:1271-1294）增加 `language` 参数；`main.py:1589-1590` 调用处传请求语言
    - 前端：编辑（`commitEditMessage`）与重新生成（`handleRegenerateMessage`）请求体加 `language: getLanguage()`；`goalResume` 链（`chatService.ts:594` → `preload.js:38` → `main.js:794-835` → `/goal/resume`）加传 `getLanguage()`

- [x] **L2 记忆 scope 对齐（UI 跟随当前会话项目）**
  - 预期：多项目下记忆面板显示/编辑的是当前会话所属项目的记忆，不再永远指向默认工作区
  - 改法：`backend/main.py:525-530` 的 `/api/memory/*` 请求模型加 `project_id`（可选）；`workspace_controller` 用 `workspace_for_project(project_id)` 解析存储；前端 `MemoryPanel.tsx` 打开时传当前 `session.project_id`

- [x] **L3 `_recent_transcript` 超大消息不再清空**
  - 预期：单条超长消息不再导致自动提取静默跳过
  - 改法：`backend/coworker/memory/auto_extract.py:167-185` 调整顺序：先 `lines.append(line)` 再累加并判断超限 break（保证至少保留尾部最新消息）

- [x] **L4 自动提取调度与计数健壮性**
  - 预期：重复/重跑不再双倍计数、不再并发双调度提取；`_turn_counters` 不再无限增长
  - 改法：`backend/coworker/memory/memory_manager.py:132-147` 把 `record_turn`/`should_extract`/`reset_turns` 合并进同一把锁临界区；`:55` 的 `_turn_counters` 加容量上限（如超过 N 清空最旧）或按会话关闭清理；`after_turn`（149-170）改为在调用方持久化消息之后再调度（或至少对异步竞态加注释容错）

- [x] **L5 记忆注入不再暴露绝对路径**
  - 预期：prompt 中不再给模型绝对文件路径，避免诱导模型用文件工具直接改记忆文件（绕开 store 保护）；与文档"agent 无法直接碰记忆"一致
  - 改法：`backend/coworker/memory/memory_manager.py:89` 的 `source=f"{memory.path}..."` 改为相对/匿名标签（如 `source="project memory"`）；`memory_prompt.py:26` 的 `source="..."` 属性同步处理

- [x] **L6 自动标题判断改为目标会话**
  - 预期：每个新会话首次有回复后都能自动生成标题，不再永远 "New chat"
  - 改法：`frontend/src/App.tsx:1534-1545` 用目标会话的消息条数判断（而不是全局 `messages`）；可把当前会话消息传入判断

- [x] **L7 `goal_edited`/`agent_activity` SSE 事件处理或清理**
  - 预期：后端发出的 `goal_edited` 不再被前端静默丢弃；`agent_activity` 有明确处理或从类型删除
  - 改法：`frontend/src/types.ts:605-663` 补齐 union 分支；`App.tsx:716-954` 加对应 handler（如 goal_edited 更新 goal 状态；agent_activity 若是未实现功能则从类型中删除并同步后端或标注 TODO）

- [x] **L8 错误路径补 `assign_message`**
  - 预期：流错误/断连后该轮改动仍能按消息回滚
  - 改法：`backend/main.py:1087-1103` 与 `:1765-1766` 的 `_on_error` 持久化路径，补上与成功路径一致的 `changes.assign_message(session_id, message_id)` 调用

- [x] **L9 always-allow digest 的 cwd 归一化统一**
  - 预期："对 cwd=`.` 的某个命令允许始终执行"不再因 digest 不一致而失效
  - 改法：`backend/main.py:2023` 存储 digest 前与 `backend/coworker/workspace.py:536,542`、`agents.py:620-626` 统一用同一 cwd 归一化函数（相对根则归为 `""`）

- [x] **L10 SSE 解析兼容 CRLF / 跨 chunk 拆帧**
  - 预期：后端若改发 `\r\n\r\n` 或第三方源，不再整帧丢失
  - 改法：`electron/main.js:443-531,618-633` 与 `frontend/src/services/chatService.ts:739-751` 的按 `\n\n` split 改为按 `\r?\n\r?\n` 匹配并缓存未完成片段（buffer 累积直到遇到空行）

- [x] **L11 SkillHub 上游不可达如实上抛**
  - 预期：SkillHub 源挂时市场页显示错误（与 ClawHub 修复 b1c935d1 对齐），不再显示空网格误导用户
  - 改法：`backend/coworker/skills/skill_market.py:513-515`（`_skillhub_fetch_page`）与 `:538-540`（`_list_skillhub_categories`）返回 `MarketPage(error=...)`/上抛，而非 `([], None)` 静默空

- [x] **L12 聊天安装技能保留全部 frontmatter 键**
  - 预期：从聊天安装的技能不再丢失 `disable-model-invocation`、`allowed-tools`、`paths`、`license` 等键，行为与原始一致
  - 改法：`backend/coworker/skills/skill_market.py:418-431` `install_from_content` 有 `commands` 时不再只重写 `name/description/commands/version`，改为保留原 frontmatter 全部键（仅更新需改动的）

- [x] **L13 市场溯源带 owner**
  - 预期：ClawHub 跨 owner 撞 slug 时，"已安装"徽章只标真正的来源
  - 改法：`backend/coworker/skills/skill_market.py:196-202` `installed_identifiers` 返回 `(source, slug, owner)`；`backend/main.py:2715-2722` `_mark_market_installed` 匹配时用三元组（对旧记录保留 `(source, slug)` 回退）

- [x] **L14 install_skill 装完可见（决策③=A）**
  - 预期：agent 安装/删除技能后，消息流与侧栏即时可见，用户随时能查看/卸载，不打断 agent 流程
  - 改法：前端在安装/删除 tool 完成后触发技能列表刷新（`App.tsx:237` 的 `refreshSkills` 复用），`SkillsPanel.tsx` 自动刷新；agent 消息中工具调用卡片已展示 install 结果则无需额外提示

### 阶段 2 验证清单

- [x] `backend/venv/bin/python -m coworker.memory.selftest` 通过
- [x] 中/英文 UI 下验证：发消息 / 编辑 / 重新生成 / 恢复 goal / 自动标题全部跟随系统语言；curl 实测 regenerate `language=en`→英文、`zh`→中文、generateTitle `en`→"Quick introduction"；浏览器实测英文提问在中文 UI 下回复中文、会话自动标题"操作系统信号量详解"
- [x] 记忆面板在项目会话下打开，确认指向该项目记忆（local 项目显示 `/Users/leon/Documents/CodeProjects/local/.coworker/MEMORY.md`；用前端相同调用 `POST /api/memory/file?project_id` 写入/读回验证项目隔离）
- [x] 故意让 SkillHub 不可达（mock `_get_json=None`），`search('skillhub',...)` 返回 `error: source_unavailable` 而非空网格
- [x] `cd frontend && npx tsc --noEmit` 通过

### 阶段 2 额外说明（经真机验证）

- L1 语言链路实测无回退：主流 / 编辑 / 重新生成 / goal resume / 自动标题全部走 `request.language`（前端 `getLanguage()`），`request_language_for_session` 死函数已删除。
- L8 仅 chat_stream 错误路径原本已含 assign_message；编辑/重新生成错误路径此前完全不持久化部分内容，本次补上"累积 + 错误持久化 + assign_message"，使该轮工具变更可被消息级回滚命中。
- L10 SSE 兼容 CRLF：electron 与 chatService 的 4+3 处 `buffer += chunk` 均加 `\r\n`→`\n` 归一化，跨 chunk 拆帧原有 buffer 累积已覆盖。

---

## 阶段 3｜后台流重构 + 死代码清理 + 保留导出/密钥加密（决策⑤=A、⑥）

- [x] **R1 真后台更新：重构 App.tsx 流管理（决策⑤=A）**
  - 预期：切换会话后，原会话的流继续在后台生成并更新自己的消息，完成时侧栏不再一直转圈；一个会话的 `/clear` 不再抹掉其它会话进行中的消息
  - 改法（`frontend/src/App.tsx` 主体）：
    - 把全局单一消息数组改为"按会话 id 隔离的 streaming 状态"，流事件按 `session_id` 路由到各自会话（替换 `App.tsx:721-723` 的"非活跃会话全部丢弃"逻辑）
    - 收敛 `sendMessage`/`commitEditMessage`/`handleRegenerateMessage`/resume 四个近重复 handler（~716-1081, 1156-1306, 1341-1487, 1594-1704）为统一的流处理函数
    - 顺带消除：P2 每 token 全量 `setMessages` O(n) map（改增量更新/按会话更新）、P7 无 sessionId 的 ambient 消息串入所有会话、B5 `resolvePendingRequest` 空 map、B9 `resolvingRef` 无 idle 超时可 wedge、`goalStreamIdRef` 只写不读（App.tsx:301,944-952,2213-2216）
  - 注意：后端多会话并发流已有 checkpoint 隔离（此前 commit 84cfdadd），本项纯前端状态重构；重构期间保持 SSE 事件协议不变

- [x] **R2 后端死代码清理**
  - 预期：减少误导与维护负担；确认删除后无引用
  - 改法（逐项确认无调用后删除）：
    - `backend/main.py:42` `SKILLS_CONFIG_FILENAME` 导入
    - `backend/main.py:154` 未使用的 `_market_backfill_task`
    - `backend/main.py:266,1384-1386,1396,1405,1418,1490` 无实际作用的 `_goal_active_streams`
    - `backend/main.py:874-876,2199` 冗余重复 import（json/BaseModel/StreamingResponse）
    - `backend/main.py:1868-1870` stray `@app.post(".../rollback")` 装饰器（复制粘贴错误，挂错在 `list_projects` 上）
    - `backend/coworker/agents.py:358` `build_workspace_tools` 未被传入的 `approval_store` 参数及其 sync 分支
    - `backend/coworker/memory/memory_manager.py:95-103` `build_middleware`（agents.py:2000 直接构造）
    - `backend/coworker/memory/memory_store.py:114-120` `scan_all`（无调用，且内部双扫）
    - `backend/coworker/memory/memory_discovery.py:51-56` `roots` 属性（无调用）
    - `backend/coworker/mcp/mcp_session.py:995-1004` `reconnect()`（走 `_reconnect_async`）
    - `backend/coworker/mcp/mcp_loader.py:53-54` `is_remote_transport()`
    - `backend/coworker/skills/skill_market.py:271-275` `list_categories()`（端点用 `list_facets`）
    - `backend/coworker/skills/skill_discovery.py:117-149` `_scan_tree` 未用的 `root` 参数

- [x] **R3 前端死代码清理**
  - 预期：`goalStart`/`goalStop` 等断桥不再留在类型里诱导调用崩溃
  - 改法（确认无调用后删除/实现）：
    - `frontend/src/electron.d.ts:121,125` 与 `chatService.ts:102,209-214,692` 的 `goalStart`/`startGoal`、`goalStop`（goal 实际走 `goal_mode` 启动，App.tsx:977）
    - `frontend/src/services/chatService.ts:101,202-207,684-690` 未使用的 `sendMessage`（非流）
    - `frontend/src/App.tsx:1711` `isResolving`、`:1528-1531` `pendingRequestsRef`、`:178` `_language` hook（仅副作用无引用）
    - 4 个全链路实现但无组件的死 IPC：`runWorkspaceCommand`/`getWorkspaceTree`/`getWorkspaceDir`/`getWorkspaceFile`（`types.ts`/`chatService.ts:452-470`/`preload.js:95-99`/`main.js:715-738`，仅 `getWorkspaceBranch` 在用）
    - `ChatInput.tsx:199` `workspaceLabel` prop、`WorkspaceSidebar.tsx:217` 未用 `config` prop、`MessageList.tsx:354` `isRunningEmpty ? null : null`
    - `App.tsx:1566-1573` `resolvePendingRequest` 无操作 map

- [x] **R4 运行记录保留/导出（决策⑥）**
  - 预期：trace / tool audit / checkpoint 可导出、可清空、可设保留条数；长期使用不再无限膨胀
  - 改法：
    - 后端：`backend/coworker/traces.py`、`backend/coworker/workspace.py`（tool audit）、`backend/coworker/checkpoints.py` 各新增导出（返回文件内容/下载流）与清理（按条数/按时间裁剪）API；设置接口加保留条数配置（复用 `.coworker_settings.json` 模式）
    - 前端：Runtime Observability（Agent trace）面板与 ToolAuditPanel 加"导出 / 清空 / 保留条数"控件，调用新 API
  - 约束：不改变现有 append-only 记录结构，仅在写入/展示侧加保留策略

- [x] **R5 密钥加密（决策⑥，打包除外）**
  - 预期：provider API key 与 MCP secrets 不再明文躺在 JSON；重启后密钥仍可用
  - 改法（待确认实现路径，见下方待定项）：
    - 新增 `backend/coworker/secrets.py`：macOS 用 `security` CLI 存取 Keychain（`security add-generic-password`/`find-generic-password`），不可用时回退 0600 明文文件并 `logger.warning`
    - `backend/coworker/providers.py` 存储/读取 API key 走 secrets 层；`backend/coworker/mcp/mcp.py` 的 env/headers secrets 同理
    - 兼容已有 `providers.json` 中存量明文 key：首次启动迁移进 Keychain 并从 JSON 移除（或保留读取兼容）
  - 待定：若实现复杂度过高，退化为"0600 权限 + 文档说明"为阶段内可接受交付，需在阶段 3 开工时确认

- [x] **R6 README 更新**
  - 预期：已知限制与能力描述与实现一致
  - 改法：`README.md` 补充：文件陈旧守卫为单轮限制（决策②=B）、语言跟随系统语言规则、技能安装可见性、运行记录保留/导出、密钥存储方式；`Current Limitations` 去掉已修复项

### 阶段 3 验证清单

- [x] `cd frontend && npx tsc --noEmit` 通过
- [x] `cd frontend && npm run build` 通过
- [x] 冒烟：同时开两个会话并行生成，来回切换，后台消息照常更新、完成状态正确；一个会话 `/clear` 不影响另一个会话（浏览器实测：A 流式中切到 B，A 完成后侧栏 Running 指示器归零；B 执行 /clear 后 A 消息完好）
- [x] 导出/清空 trace、tool audit、checkpoint 后对应面板更新；保留条数设置生效（浏览器实测清空轨迹后 traces 归零、保留条数 55 保存且重启后持久）
- [x] 配置 provider 后重启 app，密钥仍可用（Keychain 或 0600 回退）（迁移后 SSE 聊天正常、`key_present: True`、providers.json 无明文 key）
- [x] `git diff --check` 无空白错误

### 阶段 3 额外说明（经真机验证）

- R1 关键 bug 复现：A 会话流式中切到已有会话 B，A 完成后侧栏 Running 指示器仍为 1（事件被丢弃）；修复后归零。核心改动=移除主 handler 的"非活跃会话丢弃"守卫，改按消息 id 更新；goal 状态更新加会话守卫；`/clear` 会话隔离；finally 安全网按 id 收尾。
- R2 清理后确认 `_goal_active_streams` 仍被 goal_delete 锁清理守卫使用（阶段 1 引入），非死代码，保留。
- R5 MCP env/headers secrets 采用 0600 回退（任务单允许项）：避免破坏 SECRET_PLACEHOLDER 占位回填与测试流程；provider API key 完整迁移 Keychain。

---

## 明确不做（决策确认）

- [ ] **打包/分发**（electron-builder / .dmg / .exe / 更新器）—— 明确不做，不在任何阶段

## 已补做（原决策 ②=B / ①=B 经确认升级为 A）

- [x] **文件陈旧守卫跨轮持久化**（决策②升级为 A）：指纹持久化到 `data_dir/fingerprints/<root-hash>.json`，每次读取记录、构造 Workspace 时加载，写前校验跨轮/跨会话/重启均生效。实测：轮 1 读→写通过；轮 2 新实例 + 用户改文件后写被拦；重读后放行；重启后指纹仍拦。
- [x] **记忆并发"检测+合并"**（决策①升级为 A）：在锁内读改写（已串行合并）基础上加写后校验——检测到非协作写入（用户直接编辑 MEMORY.md）时合并双方内容而非覆盖。实测：40 路并发 0 丢失；模拟用户编辑插入后三方内容全部保留。

---

## 全面验收（2026-08-12）

对已完成全部修复点做验收，排查出的 bug 隐患已修复：

- **高：记忆读取死循环**：`_read_file_with_retry` 用"重编码字节数 vs st_size"比对，CRLF/非法 UTF-8 文件永不相等→无限循环（记忆 DoS）。改为按原始字节数比对 + 重试上限。实测 CRLF/坏字节不再挂死。
- **高：/ws/terminal Origin 绕过**：`startswith("http://localhost")` 被 `http://localhost.evil.com` 绕过，且放行 `null`（sandboxed iframe）。改为 urlparse 精确主机名（localhost/127.0.0.1/::1）+ 拒绝 null。实测 evil/null 均拒、localhost 放行。
- **高：Electron listActiveSessions 信封未解包**：main.js 返回 `{session_ids}` 而前端按 `string[]` 处理→TypeError+5s 报错循环。改为在 main.js 解包。
- **高：Electron regenerate/edit 丢 language**：Electron 路径请求体不带 language→英文用户重生成/编辑被强制切中文。preload/main.js 全链补齐。
- **高：provider 密钥清不掉**：`api_key=""` 不删 Keychain 旧密钥→删除后复活。`save()` 对 key_in_secrets 且 api_key 空时 `_clear_secret`。
- **中：chat_stream goal 未登记活跃流**：goal_delete 锁释放误判→并发双 goal 循环写同一 checkpoint。chat goal 流登记 `chat-goal:{sid}` 标记 + 退出清理。
- **中：merge 复活删除/清空条目**：remove/clear 的结果被并发写合并回退。updater 返回 removed_set，merge 不复活已删条目（保留真正新增）。实测 remove/clear 语义正确。
- **中：§ 校验缺口**：行尾 `§` 的条目仍被拆分；`write_file_text` 无校验。两者补齐。
- **中：_ensure_fresh 已删文件裸异常**：读取后文件被删→write 永久卡死（无逃生通道）。`_fingerprint` 缺失返回 None，`_ensure_fresh` 删指纹放行重建。
- **中：_on_error 重复 append**：done 后断连再 append 一条相同消息。edit/regenerate 加 terminal_sent 守卫。
- **中：install_from_content symlink 逃逸**：对齐 install() 用 `_safe_install_dir`。
- **中：goalMatchesView hero 污染 + start 劫持**：hero 下后台 goal 事件驱动 goal 卡；后台流 start 把视图拉回旧会话。守卫改为仅当前会话；start 仅当 `=== requestSessionId` 时绑定。
- **中：currentSessionTitle 跨会话串标题**：R1 后 messages 含全会话，fallback 取到别会话消息。按 sessionId 过滤。
- **中：goal resume 期间 Stop 失灵**：resumeGoal 未注册 AbortController。前端注册 + preload/main.js 登记 activeStreams + signal 透传。
- **中：CSP 拦市场远程图标**：img-src 缺 https:。已加。
- **中：/checkpoints/export tmp 泄漏**：BackgroundTask 清理；顺带修 FileResponse 用法（500）。
- **低：** will-navigate 前缀绕过收紧、settings 三写者改原子写、ToolAuditPanel NaN/revoke、streamPost CRLF 归一化、electron.d.ts 死类型清理、_goal_locks 结束清理、skillhub 二次 fetch 降级。

已检查无明显隐患：CORS 精确匹配、slugs 两路径防护一致、MCP 0600 时序、D9 结论、记忆锁释放路径、fingerprint 并发（丢失更新失败开放可接受）。

---

## 变更记录

| 日期 | 阶段 | 变更 |
|---|---|---|
| 2026-08-12 | - | 初始版本：基于全局审计 + 决策 B/B/A/A/A、⑥ 做、打包不做 |
| 2026-08-12 | 阶段 1 | 完成并提交：S1-S8、D1-D8、U1-U2；D9 实测后判定为误判/有界泄漏未改动；真机测试额外修复 chat_stream 的 sqlite database is locked 崩溃 |
| 2026-08-12 | 阶段 2 | 完成：L1-L14（语言跟随系统、记忆 scope 对齐、自动标题、SkillHub 错误上抛、溯源带 owner、install 保留 frontmatter 等）；全部先验证后修复，前端经 ego-browser 真机验证 |
| 2026-08-12 | 阶段 3 | 完成：R1-R6（真后台流、前后端死代码清理、运行记录导出/清空/保留、provider 密钥入 Keychain、README）；R1 先复现再修复，R4 面板经 ego-browser 验证 |
| 2026-08-12 | 补做 | 完成：文件陈旧守卫跨轮持久化（决策②→A）、记忆并发检测+合并（决策①→A）；均先验证后实现 |
| 2026-08-12 | 验收 | 全面验收已完成修复点，修复 20 项 bug 隐患（4 高 / 12 中 / 4 低），详见"全面验收"节 |
| 2026-08-12 | 二轮全局审计 | 全新审计发现的 P0/P1/P2/二梯队/三梯队问题全部处理：provider 密钥迁移丢 key、MCP 死 server 冷却、load_skill 工具、rerun_stream 工作区、install_skill 审批、goal force 去重、chat_stream/edit 流中防护、各类泄漏、SSE 断线标记 interrupted、Electron 空闲超时/IPC 编码、LLM 内置重试、ContextWindow 长上下文滚动、git_status 工具、技能创建 UI、autonomy/workMode 持久化、日志面板真数据、README 修正、launcher 安全杀进程、.coworker_settings.json 移出版本控制 |
| 2026-08-12 | 二轮验收 | 验收二轮修复，修复 16 项隐患：ContextWindow 用 RemoveMessage 真删（原实现被 add_messages reducer 吞掉不生效）、Electron 直连 fetch 被 CORS 拦截（installSkill/导出/清空/retention 全走 IPC）、git_status is_repo 键名、regenerate/edit 工作区异常不再静默回退、edit/regenerate/resume 断线统一标 interrupted、ApprovalEventBus 迟到订阅者不挂起、MCP 超时取消孤儿任务、close 清冷却、goal_nudge 每轮清残留、goal_resume 补流中防护、断线 _goal_locks 清理、_clear_secret 保守、interrupted 消息补重新生成按钮、localStorage try/catch、日志状态 CSS、IPC 编码 |
| 2026-08-12 | 按钮核对 | 全局核对所有 button 功能：修复 9 项断链/死按钮。后端：ApprovalDecisionPayload 缺 autonomy 字段→plan 审批卡"批准·逐步确认/守护执行/全自动"全 500（pydantic 丢弃后 AttributeError），补字段。前端：技能"返回"死按钮（只改 listTab 不改 viewMode）、Provider 模板 pill 重置编辑态为新增、MCP 新增模式无测试入口+stdio 空 command 不 disabled+信任开关新增模式失效、浏览器选目录无反馈、Provider 保存 trim 守卫不一致、流式时"重新生成"静默失败、GoalCard 运行中"编辑"死态。全部修复，ego-browser 真机验证 MCP 测试按钮/技能返回按钮正常 |
| 2026-08-12 | 审批卡核对 | 全面核对 PendingDocks 三卡（审核/提问/计划）+MCP 卡全部按钮：ApprovalDock(拒绝/始终允许/允许一次)、McpApprovalDock(同)、QuestionDock(拒绝/提交+选项/多选/其他自定义)、PlanDock(继续讨论/批准·逐步确认/守护/全自动)、✕ 关闭。静态核对 dispatch→chatService.resolveCommandApproval→POST /command-approvals/resolve 全链路；后端 resolve 端点覆盖 all decision types；plan autonomy 修复（补 payload 字段）经真实 API 验证 200（原 500）；PendingRequest 类型字段齐全；全部 i18n key 配对；Electron/Http 双路径接线正常。未发现新断链 |
| 2026-08-12 | 计划卡重构 | 删除计划审批卡，启用全模式 Todolist。后端：删 PlanApprovalMiddleware/submit_plan/plan_required 全链路；TodoListMiddleware 始终挂载（langchain 自带"完成一步立即标记"引导）；write_todos 全阶段放行（只读计划模式也允许，只写 graph 状态不改文件）；phase prompt 更新（计划模式=只读调研+write_todos 拆清单+不改文件，构建模式=多步骤先列清单逐项更新）。前端：删 PlanDock/PendingDocks plan 分支/plan_required 事件处理；新增全局 todos 状态 + TodoBlock 卡片（composer 上方，复用 goal-card todo 样式，与 Goal 模式同位置同显示）；todos 事件全局路由；GoalCard 去掉内嵌 todo 段统一到 TodoBlock。ego-browser 真机验证：构建/计划模式 TodoBlock 均正常出现、计划审批卡消失、计划模式只读生效。 |

# 记忆子系统重构 — 开发任务清单

目标：将记忆从「project/user 双 scope + § 分隔」重构为「记忆库目录树 + 纯 Markdown」的多 agent 形态组织。

记忆库根目录：`{COWORKER_DATA_DIR}/memory/`（默认 `~/Library/Application Support/Coworker/memory/`）

```
{DATA_DIR}/memory/
├── MEMORY.md / USER.md / AGENT.md        # 系统/用户级记忆（用户维护，全大写=系统默认）
├── <memory_dir>/                          # 项目记忆目录 = 秒级时间戳
│   ├── BASE/                              # 用户维护（系统只放一个模板）
│   │   ├── EXAMPLE.md                     # 模板示例，用户按需自建文件（大小写不限）
│   │   └── PROJECT/                       # 系统生成维护（建议用户勿增）
│   │       ├── GOALS.md / CONTEXT.md
│   ├── <agent>/                           # 创建 agent 时自动生成
│   │   ├── SOUL.md / AGENT.md / MEMORY.md
│   │   └── SESSIONS/*.md
│   └── <folder>/…                         # 非 agent 用户文件夹（只浏览/编辑，不注入）
```

---

## 阶段 1 — 基础层（路径/布局/骨架）

- [x] `config.py`：`BackendSettings` 增加 `memory_dir: Path`（`data_dir / "memory"`）
- [x] 新增 `memory/layout.py`：
  - [x] 常量：`MEMORY_ROOT_NAME="memory"`、`BASE_DIR="BASE"`、`PROJECT_SUBDIR="PROJECT"`、`SESSIONS_DIR="SESSIONS"`、`AGENT_CORE_FILES`
  - [x] `memory_dir_from_created_at(created_at) -> str`（`%Y%m%d%H%M%S`，冲突追加 `_2/_3`）
  - [x] `sanitize_name(name) -> str`（非法字符清洗 + 截断）
  - [x] `resolve_rel_path(root, rel) -> Path`（防目录逃逸校验）
- [x] 新增 `memory/registry.py`：
  - [x] `ensure_root(data_dir)`：建 `memory/` + 空 system 文件（MEMORY/USER/AGENT.md）
  - [x] `ensure_project(memory_dir)`：建 `<dir>/BASE/`（EXAMPLE.md 模板）+ `BASE/PROJECT/` 骨架（GOALS.md/CONTEXT.md 带标题占位）
  - [x] `ensure_agent(project_dir, agent)`：建 `<agent>/` + SOUL/AGENT/MEMORY + `SESSIONS/`

## 阶段 2 — 文件读写层（纯 Markdown）

- [x] `memory/memory_file.py` 简化：`MemoryFile(path, content, mtime)`，移除 § split/render
- [x] `memory/memory_store.py` 重写：
  - [x] 从 scope 操作改为文件路径操作（`read_file/write_file/remove_file`）
  - [x] 条目级 add/replace/remove 改为**按 Markdown 块**（空行分隔块，replace/remove 子串命中块）
  - [x] 保留文件锁 / 原子写 / round-trip 校验
  - [x] 所有对外路径经 `resolve_rel_path` 校验
- [x] `memory/memory_prompt.py` 重写：`format_memory_prompt(nodes, char_limit)`，文件内容块渲染 + 预算警告

## 阶段 3 — 发现与注入层

- [x] `memory/memory_discovery.py` 重写：扫描记忆库树，按 kind 标注（system/base_file/project_file/agent_file/session_file），返回注入顺序
- [x] `memory/memory_manager.py` 重写：
  - [x] `render_for(project_dir, agent)` 替代 `_render(workspace_root)`
  - [x] `resolve_project_context(project_id)` 读 `Project.memory_dir`
  - [x] `for_workspace` → `for_project(project_id, agent)`
  - [x] 保留 `configure_extractor` / `after_turn`（nudge 计数）
- [x] `memory/memory_middleware.py` 适配（`render_prompt()` 内部逻辑）

## 阶段 4 — 项目与 agent 身份

- [x] `projects.py`：`Project` 加 `memory_dir` 字段；`create` 时生成并持久化；`rename` 不重算；旧项目懒初始化
- [x] `main.py` `/projects` 创建时 hook `ensure_project` + 落 `memory_dir`

## 阶段 5 — 工具与提取适配

- [x] `agents.py`：记忆工具改为操作绑定当前 agent `MEMORY.md` 的 store（`MemoryArgs` 不变）；提示说明 BASE/系统记忆只读；运行时 `_memory` 解析改 `for_project`
- [x] `memory/auto_extract.py`：提案记录加 `project_dir`/`agent`；approved 落盘到 `agent/MEMORY.md`（Markdown 块）

## 阶段 6 — API 层

- [x] `main.py` 重写记忆端点：
  - [x] `GET /api/memory/discover`（树形发现，前端主入口）
  - [x] `GET /api/memory/file?rel=…`（读文件）
  - [x] `POST /api/memory/file`（整文件保存）
  - [x] `POST /api/memory/delete`（删文件/目录）
  - [x] `POST /api/memory/write`（写 agent MEMORY.md）
  - [x] `POST /api/memory/register-project` / `register-agent`
  - [x] `POST /api/memory/migrate`
  - [x] 保留 settings / proposals / status（status 改聚合统计）
  - [x] 删除旧端点：`GET /api/memory`、`file?scope=`、`remove`、`clear`

## 阶段 7 — 迁移

- [x] 新增 `memory/migrate_v1.py`：
  - [x] 旧 `~/.coworker/MEMORY.md` → `memory/USER.md`
  - [x] 旧项目 workspace 内 `.coworker/MEMORY.md` → 对应项目 `default_agent/MEMORY.md`
  - [x] 迁移前备份到 `memory/.migrate_backup/`
  - [x] 迁移后删除旧文件
- [x] 接入 `POST /api/memory/migrate` 端点

## 阶段 8 — 前端

- [x] `types.ts`：新增 `MemoryNode`/`MemoryDiscoverResponse`/`MemoryProjectView`/`MemoryAgentView`；移除/兼容旧 scope 类型
- [x] `chatService.ts`：`discoverMemory`/`getMemoryFile(rel)`/`saveMemoryFile(rel)`/`deleteMemory(rel)`/`migrateMemory`
- [x] `MemoryPanel.tsx` 重写为树形结构：
  - [x] 系统记忆节点（MEMORY/USER/AGENT.md）
  - [x] 项目列表（可折叠），项目内 BASE + BASE/PROJECT + 各 agent 目录
  - [x] 点击文件 → 纯 Markdown 编辑器
  - [x] 删除文件（`common.delete` 确认）
  - [x] 显示相对路径（不暴露绝对路径）
- [x] `App.css` 树形样式（`memory-tree__*`），保留编辑器样式
- [x] `locales/*.json`（zh/zh-TW/zh-HK/en/ja/fr/ko）新增 memory 树 key

## 阶段 9 — 测试与验收

- [x] 重写 `memory/selftest.py`：
  - [x] layout：时间戳生成/冲突、sanitize、路径逃逸拒绝
  - [x] registry：ensure_root/ensure_project/ensure_agent 骨架
  - [x] store：Markdown 块 add/replace/remove、整文件写、并发锁、round-trip
  - [x] prompt：注入顺序、预算警告、空库
  - [x] 迁移：旧文件→新库导入 + 旧文件删除
- [x] `backend/venv/bin/python coworker/memory/selftest.py` 全绿（54 项）
- [x] `npm run build` / `tsc` 前端编译通过
- [x] 手动验收：
  - [x] `/memory` 页面显示树结构
  - [x] 打开/编辑/保存各类型文件
  - [x] register-project 后骨架出现
  - [x] approved 提案落盘到 agent MEMORY.md
- [x] 测试环境执行迁移 + 清理旧文件（`~/.coworker/MEMORY.md` 与项目内 `.coworker/MEMORY.md`）

## 阶段 10 — 六项记忆增强

### ⑥ 项目根非 agent 文件夹不再误判为 agent
- [x] `memory_discovery.py`：`_looks_like_agent`（`<dir>/BASE/SOUL|AGENT|MEMORY.md` 或旧版根级三件套）判定；非 agent 目录 → `ProjectView.folders`（`FolderView`，kind=`folder_file`，递归收集 .md）
- [x] `injected()` 排除 folder_file（纯用户归档，不注入）；搜索包含它们
- [x] 前端：`types.ts` 加 `folder_file`/`MemoryFolderView`/`ProjectView.folders`；`MemoryPanel` 渲染普通「文件夹」分支

### ② 全文搜索
- [x] `MemoryScanner.search(query, limit)`：大小写不敏感子串匹配全库（system + 项目 + agent + 文件夹），返回 top50 `{rel,name,kind,location,snippet,match_count}`，按命中数降序
- [x] `GET /api/memory/search?q=&limit=`；空/超长 q → 400
- [x] 前端：搜索框（防抖 300ms）+ 结果列表（点击进编辑器）；`chatService.searchMemory` + preload + main IPC `search-memory`

### ① 删除 → 系统回收站
- [x] 新增 `memory/trash.py`：`send_to_trash(path, dest_dir)` 冲突自动补后缀（`name 2.md`）
- [x] `MemoryStore.remove_file` 改为移动至系统废纸篓（macOS `~/.Trash`，可注入 `trash_dir` 便于测试），失败回退 `memory/.trash/`；删除前清理 `.lock`
- [x] `POST /api/memory/delete` 响应加 `trashed:true`；前端确认文案改「移入系统废纸篓」

### ③ 重命名 / 移动（弹窗式；拖动留待后续）
- [x] `POST /api/memory/move {rel,new_rel}`：双向 `resolve_rel_path` 防逃逸；**禁止**移动系统文件（MEMORY/USER/AGENT.md）与 agent 核心（SOUL/AGENT/MEMORY.md）、禁止移入 agent 核心位置；允许 BASE/PROJECT/SESSIONS/agent-BASE 扩展/用户根文件
- [x] `MemoryStore.move_file` 连带移动 `.lock`
- [x] 前端：文件行悬停「重命名/移动」→ 弹窗（新文件名 + 目标文件夹下拉：系统根/各项目 BASE/PROJECT/各 agent BASE/各文件夹）
- [ ] 拖拽放置（HTML5 drag）——后续迭代

### ④ 导入导出 / 备份
- [x] 新增 `memory/transfer.py`：`export_memory`（zip 至 `data_dir/memory_exports/`，结构=记忆库相对树，排除 `.lock/.trash/.migrate_backup/.DS_Store`）；`preview_import`（两阶段：解压到 staging + 报告 exists 冲突，不落库）；`apply_import`（按 `{rel:skip|overwrite}` 决策合并，逐条防穿越 + 仅 .md）
- [x] `POST /api/memory/export {scope:all|system|projects, project_dirs}` / `POST /api/memory/import/preview {path}` / `POST /api/memory/import/apply {token,decisions}`
- [x] electron：`export-memory` IPC（后端生成 → `dialog.showSaveDialog` → `fs.copyFile` → `shell.showItemInFolder`）；`import-memory` IPC（`dialog.showOpenDialog` zip filter）；`preview/apply-memory-import` IPC
- [x] 前端：工具栏「导出/导入」按钮；导出弹窗（范围单选 + 项目多选）；导入冲突弹窗（逐文件 skip/overwrite + 全部保留/全部覆盖快捷）
- [ ] 局限（已知）：只恢复记忆文件；`projects.json` 的 project↔memory_dir 映射不在本范围（整库恢复到新机器时项目目录可能成为孤儿）

### ⑤ agent 写入权限 + 自动提炼直写
- [x] **权限模型**：agent 可写 ① 自己的 `<agent>/BASE/`（核心+扩展，`scope="agent"`+`name`）；② 系统级默认大写文件 `MEMORY.md/USER.md/AGENT.md`（`scope="system"`+`name` 限定三件套）；其余（项目 BASE 用户文件/PROJECT/其他 agent/用户根文件）只读
- [x] `agents.py`：`MemoryArgs` 加 `scope`/`name`；`_resolve_memory_target` 解析+越界拒绝；工具 docstring 更新权限规则
- [x] `memory_manager.py`：`write_auto_facts(candidates)` 直写（项目→agent `MEMORY.md`，全局→系统 `USER.md`，按块去重）；`configure_extractor` 去掉 proposal_store
- [x] `memory/auto_extract.py`：`run_auto_extract` 改为 `write_facts` 回调直写（免用户确认）
- [x] **移除提案机制**：删 `MemoryProposalStore`、`/api/memory/proposals` 两端点、`proposals_pending`、前端 `MemoryProposalsPanel` 及 settings 入口、相关 types/ipc/locales/CSS（`memory_proposals.jsonl` 数据文件保留未动）

## 阶段 11 — 增强测试与验收

- [x] `memory/selftest.py` 扩展：非 agent 文件夹分类不注入、搜索命中/片段/无匹配、move 约束、trash 注入目录+冲突后缀、export/import 往返（skip/overwrite）（全绿 77 项）
- [x] `memory/stress_test.py` 全绿（99 项）
- [x] `tsc --noEmit` + `vite build` 通过
- [x] HTTP 冒烟（临时实例 9528）：discover 含 folders、search、move（受保护拒绝）、export→import preview/apply 往返、proposals 404、settings/status 无 proposals_pending

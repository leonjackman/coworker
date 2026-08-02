
# 实现程序启动时恢复上一次会话（Resume Last Session）

## 问题

当前 App 启动时 `sessionId` 为 `undefined`，`activeView` 为 `'chat'`。
用户打开一个会话，发送消息，然后关闭窗口（隐藏）。
下次启动时，会话信息（包括 messages、project）全部丢失，用户需要手动从侧边栏重新打开。

## 需求

重启 App 时自动打开上一次关闭（或最后活跃）的会话。

## 审计发现

### 当前架构

1. **Session ID 状态**：`App.tsx:40` — `useState<string | undefined>()` 初始为 undefined
2. **无持久化**：sessionId 不在 localStorage 中，也不在 config 中
3. **Config 接口**：`types.ts:31` — `RuntimeConfig` 只有 workspace、data_dir、provider/model 等信息
4. **Config 更新**：`types.ts:41` — `RuntimeConfigUpdate` 只有 `selected_provider_id` 和 `selected_model`
5. **后端 Config Controller**：仅支持 provider/model 更新，无法扩展 arbitrary fields
6. **App 启动流程**：`App.tsx:108-140` — bootstrap 加载 config、providers、sessions，但从不 auto-select session
7. **session 已按时间排序**：`backend/coworker/sessions.py:103` — `sorted(...st_mtime, reverse=True)`，最新修改的 session 在最前

### 关键代码位置

| 文件 | 行号 | 说明 |
|------|------|------|
| `App.tsx` | 40 | `sessionId` state 声明 |
| `App.tsx` | 393 | `openSession()` 函数 — 加载完整 session 消息 |
| `App.tsx` | 260 | `updateRuntimeConfig` 使用模式 — 后端 PATCH 配置 |
| `electron/main.js` | 323 | IPC handler `update-runtime-config` → backend PATCH /config |
| `backend/config_controller.py` | 24-28 | 当前只处理 provider/model 的 update |
| `electron/main.js` | 264 | `app.on('before-quit')` — 关闭时保存 state 的理想时机 |
| `electron/main.js` | 254 | `app.whenReady()` — 启动流程入口 |

## 方案

### 选择：在 Electron 主进程中用 localStorage/session JSON 保存 last_active_session

**不修改后端 config 接口**（避免破坏现有 API 契约），改用 Electron preload + 主进程存储。

### 步骤

#### Step 1: preload.js 暴露持久化方法

```js
contextBridge.exposeInMainWorld('electronAPI', {
  // ... 现有方法 ...
  // 新增：session 持久化
  setLastSessionId: (sessionId?: string) => {
    localStorage.setItem('coworker-last-session', sessionId || '');
  },
  getLastSessionId: () => {
    return localStorage.getItem('coworker-last-session') || undefined;
  },
});
```

#### Step 2: App.tsx — 启动时恢复上一次会话

在现有的 `bootstrap` useEffect 中，等待 sessions 加载完成后查找最后一次活跃会话：

```ts
// 在 bootstrap 函数的 sessions 和 projects 加载完成后添加：
// (约在 refreshSessions() 和 refreshProjects() 之后)

// 新增：恢复 last session
useEffect(() => {
  if (sessionId || sessions.length === 0) return;
  async function resumeLastSession() {
    const lastId = (window as any).electronAPI?.getLastSessionId?.();
    if (!lastId) return;
    // 检查这个 session 是否仍然存在于列表中（可能已被删除）
    const sessionExists = sessions.some(s => s.id === lastId);
    if (sessionExists) {
      await openSession(lastId);
      return;
    }
    // 如果 session 已删除，尝试打开最新 session
    const latest = sessions[0]; // sessions 已按 st_mtime 降序排列
    if (latest) {
      await openSession(latest.id);
    }
  }
  resumeLastSession();
}, [sessionId, sessions.length, sessions, projects.length, activeProjectId, sessions.length === 0]);
```

**或者更简单**：直接在 bootstrap 的刷新 sessions/projects 后调用（同一 useEffect 内）：

```ts
// bootstrap 函数内，refreshSessions() 和 refreshProjects() 之后：
const lastId = (window as any).electronAPI?.getLastSessionId?.();
if (lastId && sessions.some(s => s.id === lastId) && !sessionId && projects.length > 0) {
  // 延迟一下等 openSession 执行完
  setTimeout(() => openSession(lastId), 50);
}
```

#### Step 3: App.tsx — sessionId 变化时保存

在 `sessionId` state 变化时 persist：

```ts
// 方案 A：useEffect 监听 sessionId 变化
useEffect(() => {
  if (sessionId) {
    (window as any).electronAPI?.setLastSessionId?.(sessionId);
  }
}, [sessionId]);
```

这会覆盖 `openSession()` 的调用（第 413 行 `setSessionId(sessionIdToOpen)`）和 sendMessage 的调用（第 234/254 行）。

#### Step 4: App.tsx — session 删除时清理

当 sessionId 对应的 session 被删除（`deleteSession`），如果这个被删的正是 last session：

```ts
// deleteSession 函数内已有：
if (sessionIdRef.current === sessionIdToDelete) {
  setSessionId(undefined);
  // 新增：清除 last session 记录
  (window as any).electronAPI?.setLastSessionId?.(undefined);
}
```

#### Step 5: 类型声明

在 `frontend/src/electron.d.ts` 补充：

```ts
updateTranslucent: (enabled: boolean) => void;
hasVibrancySupport: () => Promise<boolean>;
setLastSessionId: (sessionId?: string) => void;
getLastSessionId: () => string | undefined;
```

## 涉及文件

| 文件 | 改动类型 | 行号 |
|------|----------|------|
| `electron/preload.js` | 新增 2 个方法 | ~44-47 |
| `frontend/src/electron.d.ts` | 新增 2 个类型 | ~65-66 |
| `frontend/src/App.tsx` | 修改 3 处（bootstrap 恢复、sessionId persist、删除时清理） | ~40, ~130+, ~424 |

**总代码量：约 20 行**

## 非 macOS 支持

localStorage API 是 Web 标准，在所有平台都可以用。preload.js 在 preload context 中可以访问 localStorage。Electron 的 preload 脚本在渲染进程上下文中运行（通过 contextBridge），所以 localStorage 可用。

## 验证

1. 启动 App → 选择/开启一个 session → 发送消息
2. 关闭 App（隐藏窗口）
3. 再次启动 → 自动恢复到上一次的 session，看到完整的 messages 列表
4. 删除 session 后重启 → 不会尝试打开已删除的 session
5. 创建首个 session 后保存记录

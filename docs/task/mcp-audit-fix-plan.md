# MCP 功能完整審計與修正開發計畫

> 本文檔基於對 Coworker MCP 功能的完整代碼審計，以及對 MCP SDK、LangChain、FastAPI、Electron、React、TypeScript、Python asyncio 等官方文檔的深入調研，給出根源性修正方案。
>
> 審計範圍：後端核心模組（8 個 Python 文件）、前端 UI（React + TypeScript）、Electron IPC 層、API 路由、測試覆蓋。
>
> 調研來源：
> - MCP Python SDK v2: https://github.com/modelcontextprotocol/python-sdk
> - MCP Specification (2026-07-28): https://spec.modelcontextprotocol.io/
> - LangChain AgentMiddleware: https://python.langchain.com/docs/
> - FastAPI Security Tutorial: https://fastapi.tiangolo.com/tutorial/security/
> - Electron Security Guide: https://www.electronjs.org/docs/latest/tutorial/security
> - React Testing: https://react.dev/learn/testing
> - TypeScript Handbook: https://www.typescriptlang.org/docs/
> - Python asyncio: https://docs.python.org/3/library/asyncio.html
> - AnyIO: https://anyio.readthedocs.io/

---

## 一、問題總覽

### 嚴重問題（Critical/High）— 階段 1 優先處理

| # | 模組 | 問題 | 嚴重度 | 影響 |
|---|------|------|--------|------|
| 1 | `mcp_oauth.py` | `LoopbackCallbackServer` 未驗證 OAuth `state` 參數 | Critical | CSRF 漏洞，攻擊者可偽造認證回調 |
| 2 | `mcp_oauth.py` | 無法處理並發 OAuth 流程 | High | 第二個認證流程會失敗或被覆蓋 |
| 3 | `mcp_session.py` | probe 階段異常時 `loopback` 資源洩漏 | High | 檔案描述符洩漏，長期運行後資源耗盡 |
| 4 | `mcp_session.py` | `asyncio.shield()` + `wait_for` 超時後 shielded task 仍在後台運行 | High | 子進程/連接洩漏，超時後無法真正中斷 |
| 5 | `mcp_session.py` | stdio 超時時子進程可能未正確清理 | High | 孤兒進程累積 |
| 6 | `backend/main.py` | MCP API 路由無認證保護 | Critical | 任何客戶端都可創建/修改/刪除 MCP 伺服器，執行任意命令 |

### 中等問題（Medium）— 階段 2 處理

| # | 模組 | 問題 |
|---|------|------|
| 7 | `mcp_session.py` | `_rebuild_tools` 每次連接都重建所有工具，O(n*m) 複雜度 |
| 8 | `mcp_loader.py` | `run_blocking` 超時後執行緒仍在運行，可能留下懸空進程 |
| 9 | `mcp_loader.py` | 無效的 timeout 值被靜默忽略 |
| 10 | `mcp_loader.py` | 未知傳輸類型靜默回退到 `stdio` |
| 11 | `mcp_middleware.py` | `_overrides` 每次模型調用都同步阻塞 `ensure_connected` |
| 12 | `mcp_middleware.py` | `_resolve_tool` 線性搜索所有工具，O(n) |
| 13 | `mcp.py` | `update_server` 傳輸類型變更時驗證順序錯誤 |
| 14 | `MCPPanel.tsx` | `handleCheckAll` 的 `done` 計數器有閉包競態條件 |
| 15 | `MCPPanel.tsx` | `handleTest` 使用 `as never` 繞過 TypeScript 類型檢查 |
| 16 | `chatService.ts` | `createMcp` 未驗證 `response.server` 是否存在 |
| 17 | `main.js` | `update-mcp` / `delete-mcp` 空 `serverId` 產生畸形 URL |
| 18 | `mcp_discover.py` | 模板使用 `npx` 但未檢查是否可用 |
| 19 | `mcp_test.py` | `_friendly_error` / `_flatten_exceptions` 與 `mcp_session.py` 重複 |
| 20 | `frontend/src/types.ts` | `isRemoteTransport` 缺少 `streamable_http` |

### 低優先級問題（Low）— 階段 3 處理

| # | 模組 | 問題 |
|---|------|------|
| 21 | `mcp_session.py` | `start()` 忙等待可能無限循環 |
| 22 | `mcp_middleware.py` | `_redact_args` 應為 `staticmethod` 而非 `classmethod` |
| 23 | `mcp_middleware.py` | `_guard_result` 中未使用的 `n` 變數 |
| 24 | `mcp_discover.py` | 模板無版本資訊 |
| 25 | `__init__.py` | 無 `__all__` 聲明 |
| 26 | `MCPPanel.tsx` | 無效的 timeout 值被靜默丟棄 |
| 27 | `MCPPanel.tsx` | 初始 `load()` 失敗時無錯誤提示 |
| 28 | `MCPPanel.tsx` | 刪除確認過於隱晦 |

---

## 二、測試覆蓋缺口

| 缺口 | 嚴重度 | 建議測試 |
|------|--------|----------|
| MCP 中間件執行 | High | 測試 `wrap_tool_call` / `awrap_tool_call` 實際工具分發、審計軌跡、結果保護 |
| MCP 會話生命週期 | High | 測試 connect -> tool call -> disconnect -> reconnect 完整流程 |
| MCP API 路由 | High | 測試 9 個端點的 CRUD + 錯誤路徑 + 認證保護 |
| OAuth 流程 | Medium | 測試 `reauthorize` 完整流程（含 state 驗證、並發場景） |
| 工具衝突解決 | Medium | 測試同名工具 namespacing |
| 委派 MCP 工具 | Medium | 測試 delegated agent 繼承 parent 的 MCP session |
| 審計軌跡 | Medium | 測試 `_audit` 寫入文件且敏感字段被遮蔽 |
| 結果保護 | Medium | 測試 `_guard_result` 截斷和 base64 清除 |
| 測試-生產不一致 | Medium | 修復 `test_workers.py` 中 `mcp_session_manager=None` 與實際行為的不一致 |

---

## 三、修正方案詳細設計

### 階段 1：安全與穩定性（預估 10 小時）

#### 任務 1.1: OAuth state 參數驗證（CSRF 修復）

**影響檔案**: `backend/coworker/mcp/mcp_oauth.py`, `backend/coworker/mcp/mcp_session.py`

**根源**: `LoopbackCallbackServer._handle` 僅回傳 `state`，未驗證其有效性。

**官方文檔指引**:
- MCP SDK 使用 `secrets.token_urlsafe(32)` 生成 state（~214 bits 熵值）
- 使用 `secrets.compare_digest()` 進行定時攻擊防護的比較
- RFC 6749 Section 10.12 要求 state 用於防 CSRF
- RFC 9207 要求驗證 `iss` 參數

**修正方案**:

```python
# backend/coworker/mcp/mcp_oauth.py

import secrets
from urllib.parse import parse_qs, urlparse

class LoopbackCallbackServer:
    def __init__(self):
        self._expected_state: str | None = None
        self._expected_iss: str | None = None
        self._port: int = 0
        self._server = None
        self._thread = None

    def set_expected(self, state: str, iss: str | None = None) -> int:
        """Set the expected state and iss before starting the callback server.
        Returns the port number the server will listen on."""
        self._expected_state = state
        self._expected_iss = iss
        return self._start_server()

    def _handle(self, request):
        """Handle incoming OAuth callback request."""
        # ... existing request parsing ...

        target = request_line.split(" ")[1] if " " in request_line else "/"
        if not target.startswith("/oauth/callback"):
            self._send_response(404, "Not Found")
            return

        query_string = target.split("?", 1)[1] if "?" in target else ""
        params = parse_qs(query_string)

        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        iss = params.get("iss", [None])[0]

        # Validate state (CSRF protection)
        if state is None or self._expected_state is None or \
           not secrets.compare_digest(state, self._expected_state):
            self._send_error_response(400, "Invalid state parameter")
            return

        # Validate iss (RFC 9207)
        if self._expected_iss is not None and iss != self._expected_iss:
            self._send_error_response(400, "Invalid iss parameter")
            return

        # Store result for the waiting coroutine
        from mcp.shared.auth import AuthorizationCodeResult
        result = AuthorizationCodeResult(code=code, state=state, iss=iss)
        if not self._future.done():
            self._future.set_result(result)

        self._send_response(200, "OK")
        # Close server after handling
        if self._server:
            self._server.shutdown()
```

```python
# backend/coworker/mcp/mcp_session.py -- 在 _wire_auth 中

async def _wire_auth(self, server: McpServerEntry) -> tuple:
    """Set up OAuth authentication and return (connection, loopback)."""
    from mcp.client.auth.oauth2 import OAuthClientProvider
    from mcp.shared.auth import AuthorizationCodeResult

    # Generate state and iss
    state = secrets.token_urlsafe(32)
    expected_iss = None  # Will be populated from OAuth metadata

    # Get OAuth metadata from server
    try:
        async with httpx.AsyncClient() as http_client:
            metadata_resp = await http_client.get(f"{server.url}/.well-known/oauth-authorization-server")
            oauth_metadata = metadata_resp.json()
            expected_iss = oauth_metadata.get("issuer")
    except Exception:
        expected_iss = None

    # Create callback server with expected state
    loopback = LoopbackCallbackServer()
    port = loopback.set_expected(state, expected_iss)

    # Create OAuth provider
    provider = build_oauth_provider(server.url, port, state)

    # ... rest of auth flow ...
```

**測試**: 新增 `tests/test_mcp_oauth.py`，涵蓋 state 驗證、iss 驗證、並發場景。

---

#### 任務 1.2: 並發 OAuth 流程支援

**影響檔案**: `backend/coworker/mcp/mcp_oauth.py`

**官方文檔指引**:
- MCP SDK 使用 `anyio.Lock` 序列化同一 provider 的 OAuth 流程
- 並發請求應等待而非丟棄

**修正方案**:

```python
import anyio

class LoopbackCallbackServer:
    def __init__(self):
        self._lock = anyio.Lock()  # 序列化並發流程
        self._futures: list[tuple[str, asyncio.Future]] = []  # FIFO 隊列
        self._port: int = 0

    async def wait_for_callback(self, expected_state: str) -> AuthorizationCodeResult:
        """Wait for the OAuth callback matching the expected state.
        Supports multiple concurrent flows via FIFO queue."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        async with self._lock:
            self._futures.append((expected_state, future))

        try:
            return await future
        finally:
            async with self._lock:
                self._futures = [(s, f) for s, f in self._futures if f is not future]

    def _handle(self, request):
        # ... parse params ...
        async_state = params.get("state", [None])[0]

        async def match_and_resolve():
            async with self._lock:
                for idx, (expected_state, future) in enumerate(self._futures):
                    if expected_state == async_state and not future.done():
                        result = AuthorizationCodeResult(code=code, state=state, iss=iss)
                        future.set_result(result)
                        self._futures.pop(idx)
                        return True
                return False

        # Non-blocking: try to match, if not found discard (will be picked up by wait_for_callback)
        asyncio.create_task(match_and_resolve())
```

---

#### 任務 1.3: 資源洩漏修復（loopback、shielded task、子進程）

**影響檔案**: `backend/coworker/mcp/mcp_session.py`

**官方文檔指引**:
- AnyIO: 取消範圍內的所有資源必須在 `finally` 中清理
- `anyio.CancelScope(shield=True)` 用於保護清理操作
- Python asyncio docs: "Save a reference to tasks passed to shield()"
- MCP SDK stdio 傳輸使用 `anyio.open_process()` 自動清理

**修正方案**:

```python
# backend/coworker/mcp/mcp_session.py

async def _connect_safely(self, server: McpServerEntry, enable_browser_flow: bool) -> tuple | None:
    """Connect with safe resource cleanup on all error paths."""
    loopback = None
    wired: tuple | None = None

    try:
        if not enable_browser_flow:
            loopback = await self._start_loopback()
            try:
                probe_conn = await self._probe_remote_auth(server, loopback)
                if probe_conn:
                    wired = (probe_conn, loopback)
                    loopback = None  # Ownership transferred
            except BaseException:
                wired = None
                raise  # Re-raise to trigger finally cleanup
        else:
            wired = await self._connect_one(server, wired)

        if wired is None:
            wired = await self._connect_one(server, wired)

        return wired

    finally:
        # CRITICAL: Always clean up loopback if not transferred
        if loopback is not None:
            await self._close_quietly(loopback)


async def _ensure_connected_async(self, server_id: str, enable_browser_flow: bool = True) -> None:
    """Ensure a server session is connected, with proper cleanup on timeout."""
    if server_id in self._sessions:
        return

    task = asyncio.create_task(self._connect_one_for_server(server_id, enable_browser_flow))

    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=self._connect_timeout + 10)
    except asyncio.TimeoutError:
        # Shielded task is STILL RUNNING -- must clean up manually
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        raise
    except asyncio.CancelledError:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        raise
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._connecting.discard(task)


async def _connect_one(self, server: McpServerEntry, wired: tuple | None = None) -> tuple | None:
    """Connect to a single MCP server with proper subprocess cleanup."""
    stop = asyncio.Event()
    loopback = None
    stdio_client = None

    async def abandon():
        """Abandon this connection attempt with full cleanup."""
        nonlocal loopback, stdio_client
        stop.set()
        if loopback is not None:
            await self._close_quietly(loopback)
            loopback = None
        if stdio_client is not None:
            # MCP SDK 標準清理順序
            try:
                await asyncio.wait_for(stdio_client.aclose(), timeout=2.0)
            except asyncio.TimeoutError:
                try:
                    await stdio_client.kill()
                except Exception:
                    pass
                try:
                    await stdio_client.aclose()
                except Exception:
                    pass

    try:
        # ... connection logic ...
        if transport == TRANSPORT_STDIO:
            stdio_client = await self._create_stdio_client(server, stop)
            # stdio_client context manager handles cleanup on exit
            async with stdio_client as conn:
                await self._complete_handshake(conn, server)
                return (conn, loopback)

        # ... other transports ...

    except BaseException:
        await abandon()
        raise
```

---

#### 任務 1.4: MCP API 路由認證保護

**影響檔案**: `backend/main.py`

**官方文檔指引**:
- FastAPI: 使用 `APIRouter(dependencies=[Depends(get_current_active_user)])` 保護路由組
- JWT 認證使用 `OAuth2PasswordBearer` + `jwt.decode()`

**修正方案**:

```python
# backend/coworker/auth/jwt_auth.py -- 新建檔案

from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError

SECRET_KEY = "CHANGE_ME_TO_A_SECURE_RANDOM_KEY"  # 從環境變數讀取
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token."""
    to_encode = {"sub": subject}
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)]
) -> dict:
    """Decode JWT and return the current user."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
    except InvalidTokenError:
        raise credentials_exception

    # TODO: Look up user in DB
    return {"username": username}


async def get_current_active_user(
    current_user: Annotated[dict, Depends(get_current_user)]
) -> dict:
    """Sub-dependency: check if user is active."""
    if current_user.get("disabled"):
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user
```

```python
# backend/main.py -- 修改 MCP 路由部分

from fastapi import APIRouter
from coworker.auth.jwt_auth import get_current_active_user

# 將所有 MCP 路由移到帶認證的 router
mcp_router = APIRouter(
    prefix="/mcp",
    tags=["mcp"],
    dependencies=[Depends(get_current_active_user)],
)

@mcp_router.get("/servers")
def list_mcp_servers(): ...

@mcp_router.post("/servers")
def create_mcp_server(): ...

# ... 所有其他 MCP 路由 ...

app.include_router(mcp_router)
```

---

## 四、測試計畫

### 4.1 新增測試檔案

```
backend/tests/test_mcp_oauth.py      -- OAuth state 驗證、並發場景
backend/tests/test_mcp_session.py    -- 會話生命週期、資源清理
backend/tests/test_mcp_middleware.py -- 工具分發、審計、結果保護
backend/tests/test_mcp_api.py        -- API 路由 CRUD + 認證
backend/tests/test_mcp_loader.py     -- 傳輸類型、timeout 驗證
```

### 4.2 `test_mcp_oauth.py` 測試用例

```python
import pytest
import secrets
from unittest.mock import AsyncMock, MagicMock, patch
from mcp.shared.auth import AuthorizationCodeResult


class TestOAuthStateValidation:
    """Test OAuth state parameter validation (CSRF protection)."""

    @pytest.mark.asyncio
    async def test_valid_state_accepted(self):
        """Valid state should be accepted."""
        # Arrange
        expected_state = secrets.token_urlsafe(32)
        # ... setup callback server ...

        # Act: simulate callback with valid state
        result = await callback.wait_for_callback(expected_state)

        # Assert
        assert result.state == expected_state

    @pytest.mark.asyncio
    async def test_invalid_state_rejected(self):
        """Invalid state should be rejected."""
        # Arrange
        expected_state = secrets.token_urlsafe(32)
        wrong_state = secrets.token_urlsafe(32)

        # Act & Assert
        with pytest.raises(OAuthFlowError, match="State parameter mismatch"):
            # Simulate callback with wrong state
            ...

    @pytest.mark.asyncio
    async def test_missing_state_rejected(self):
        """Missing state should be rejected."""
        # ...

    @pytest.mark.asyncio
    async def test_iss_validation(self):
        """iss parameter should be validated per RFC 9207."""
        # ...

    @pytest.mark.asyncio
    async def test_concurrent_flows(self):
        """Multiple concurrent OAuth flows should not interfere."""
        # ...
```

### 4.3 `test_mcp_session.py` 測試用例

```python
class TestSessionLifecycle:
    """Test MCP session lifecycle management."""

    @pytest.mark.asyncio
    async def test_connect_disconnect(self):
        """Test basic connect and disconnect."""
        # ...

    @pytest.mark.asyncio
    async def test_reconnect_on_transport_error(self):
        """Test automatic reconnection after transport error."""
        # ...

    @pytest.mark.asyncio
    async def test_subprocess_cleanup_on_timeout(self):
        """Test stdio subprocess cleanup on timeout."""
        # ...

    @pytest.mark.asyncio
    async def test_loopback_cleanup_on_probe_failure(self):
        """Test loopback resource cleanup when probe fails."""
        # ...

    @pytest.mark.asyncio
    async def test_shielded_task_cleanup_on_timeout(self):
        """Test shielded task cleanup after timeout."""
        # ...
```

### 4.4 `test_mcp_middleware.py` 測試用例

```python
class TestMiddlewareExecution:
    """Test MCP middleware tool execution."""

    @pytest.mark.asyncio
    async def test_wrap_tool_call_dispatches_correctly(self):
        """Test that wrap_tool_call dispatches to correct session."""
        # ...

    @pytest.mark.asyncio
    async def test_awrap_tool_call_async_path(self):
        """Test async path of awrap_tool_call."""
        # ...

    @pytest.mark.asyncio
    async def test_audit_trail_written(self):
        """Test that audit trail is written to file."""
        # ...

    @pytest.mark.asyncio
    async def test_result_truncation(self):
        """Test that large results are truncated at 50K chars."""
        # ...

    @pytest.mark.asyncio
    async def test_base64_scrubbing(self):
        """Test that base64 data URLs are scrubbed from results."""
        # ...

    @pytest.mark.asyncio
    async def test_sensitive_args_redacted(self):
        """Test that sensitive arguments are redacted in audit."""
        # ...
```

### 4.5 `test_mcp_api.py` 測試用例

```python
class TestMCPRoutes:
    """Test MCP API routes with authentication."""

    def test_list_mcps_requires_auth(self):
        """GET /mcp/servers should require authentication."""
        # ...

    def test_create_mcp_requires_auth(self):
        """POST /mcp/servers should require authentication."""
        # ...

    def test_update_mcp_validates_server_id(self):
        """Update should reject empty server_id."""
        # ...

    def test_delete_mcp_validates_server_id(self):
        """Delete should reject empty server_id."""
        # ...

    def test_test_mcp_connection_stdio(self):
        """Test stdio MCP connection test."""
        # ...

    def test_test_mcp_connection_http(self):
        """Test HTTP MCP connection test."""
        # ...

    def test_test_mcp_invalid_timeout(self):
        """Test that invalid timeout values are rejected."""
        # ...
```

### 4.6 `test_mcp_loader.py` 測試用例

```python
class TestTransportNormalization:
    """Test transport name normalization."""

    def test_valid_transport_passthrough(self):
        """Valid transport names should pass through."""
        assert normalize_transport("stdio") == "stdio"
        assert normalize_transport("streamable_http") == "streamable_http"

    def test_transport_alias_mapping(self):
        """Transport aliases should map to canonical names."""
        assert normalize_transport("local") == "stdio"
        assert normalize_transport("ws") == "websocket"

    def test_unknown_transport_raises_error(self):
        """Unknown transports should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown transport"):
            normalize_transport("tcp")

    def test_case_insensitive(self):
        """Transport names should be case-insensitive."""
        assert normalize_transport("STDIO") == "stdio"


class TestTimeoutValidation:
    """Test timeout value validation."""

    def test_valid_timeout(self):
        """Valid timeout should be accepted."""
        conn = {"timeout": "30"}
        result = build_connection(conn)
        assert result["timeout"] == 30.0

    def test_invalid_timeout_string(self):
        """Invalid timeout string should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid timeout"):
            build_connection({"timeout": "abc"})

    def test_negative_timeout(self):
        """Negative timeout should raise ValueError."""
        with pytest.raises(ValueError, match="must be positive"):
            build_connection({"timeout": "-5"})

    def test_excessive_timeout(self):
        """Timeout > 3600 should raise ValueError."""
        with pytest.raises(ValueError, match="must not exceed 3600"):
            build_connection({"timeout": "7200"})
```

---

## 五、修正優先級與時間估算

| 階段 | 任務 | 問題 | 嚴重度 | 預估工作量 |
|------|------|------|--------|-----------|
| **階段 1** | 1.1 | OAuth state 驗證 | Critical | 2h |
| **階段 1** | 1.2 | 並發 OAuth 流程 | High | 2h |
| **階段 1** | 1.3 | 資源洩漏修復 | High | 3h |
| **階段 1** | 1.4 | API 認證保護 | Critical | 3h |
| **階段 2** | 2.1 | 傳輸類型一致性 | Medium | 1h |
| **階段 2** | 2.2 | IPC 輸入驗證 | Medium | 1h |
| **階段 2** | 2.3 | 前端類型安全 | Medium | 2h |
| **階段 2** | 2.4 | 前端競態條件 | Medium | 1h |
| **階段 2** | 2.5 | 重複代碼提取 | Medium | 1h |
| **階段 3** | 3.1 | 工具解析快取 | Medium | 2h |
| **階段 3** | 3.2 | 非阻塞連接預熱 | Medium | 2h |
| **階段 3** | 3.3 | 輸入驗證增強 | Medium | 2h |
| **階段 3** | 3.4 | 代碼品質修復 | Low | 2h |
| **測試** | T1 | 新增測試覆蓋 | High | 8h |
| **總計** | | | | **34h** |

---

## 六、執行順序與依賴關係

```
階段 1:
  1.1 (OAuth state) --> 1.2 (並發 OAuth)  [可並行]
  1.3 (資源洩漏)     [獨立]
  1.4 (API 認證)     [獨立]

階段 2:
  2.1 (傳輸類型)     [獨立]
  2.2 (IPC 驗證)     [獨立]
  2.3 (前端類型)     [依賴 2.1]
  2.4 (競態條件)     [獨立]
  2.5 (重複代碼)     [獨立]

階段 3:
  3.1 (工具快取)     [依賴階段 2 完成]
  3.2 (非阻塞預熱)   [依賴階段 2 完成]
  3.3 (輸入驗證)     [獨立]
  3.4 (代碼品質)     [獨立]

測試:
  T1 (全面測試)      [所有階段完成後執行]
```

---

## 七、驗收標準

### 7.1 安全驗收

- [ ] OAuth state 參數驗證通過所有測試用例
- [ ] 並發 OAuth 流程不互相干擾
- [ ] 所有 MCP API 路由需要認證（401 未認證）
- [ ] 無已知 CSRF 漏洞
- [ ] 無資源洩漏（記憶體/檔案描述符/子進程）

### 7.2 功能驗收

- [ ] 所有傳輸類型（含 streamable_http）在前端可用
- [ ] 空 serverId 被正確拒絕（400 錯誤）
- [ ] 無效 timeout 值被正確拒絕（400 錯誤）
- [ ] 未知傳輸類型被正確拒絕（400 錯誤）
- [ ] 前端類型檢查通過（無 `as never`）

### 7.3 性能驗收

- [ ] 工具解析時間從 O(n) 降至 O(1)
- [ ] 模型調用不再被 `ensure_connected` 阻塞
- [ ] 連接預熱在圖構建時完成

### 7.4 測試驗收

- [ ] 測試覆蓋率 >= 80%（MCP 核心模組）
- [ ] 所有新增測試用例通過
- [ ] 現有測試全部通過（無 regression）
- [ ] 測試-生產行為一致

---

## 八、風險與緩解

| 風險 | 影響 | 緩解措施 |
|------|------|----------|
| JWT 認證改變現有客戶端行為 | 中 | 提供環境變數切換（`MCP_AUTH_ENABLED=false`） |
| 資源洩漏修復引入新 bug | 高 | 充分的單元測試 + 集成測試 |
| 並發 OAuth 流程增加複雜度 | 中 | 參考 MCP SDK 的 `anyio.Lock` 模式 |
| 前端類型修改破壞現有 UI | 低 | TypeScript 編譯時檢查 |
| 測試覆蓋增加開發時間 | 低 | 優先測試核心路徑，邊緣情況可後續補充 |

---

## 九、後續改進建議（不在本計畫範圍）

1. **速率限制**: 使用 `slowapi` 對 `/mcp/test` 和 `/mcp/check-all` 添加速率限制
2. **健康監控**: 定期主動監控 MCP 伺服器健康狀態
3. **使用計量**: 將 MCP 工具調用計量發送到監控系統
4. **結果快取**: 對相同參數的重複 MCP 工具調用進行快取
5. **模板版本控制**: 為預設模板添加版本資訊
6. **自定義模板**: 允許用戶添加自定義 MCP 伺服器模板
7. **審計日誌可視化**: 提供審計軌跡的 UI 查看功能

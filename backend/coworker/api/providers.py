# -*- coding: utf-8 -*-

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from coworker.mcp.mcp import SECRET_PLACEHOLDER, STATUS_CONNECTED, STATUS_ERROR, STATUS_NEEDS_AUTH
from coworker.mcp.mcp_discover import resolve_templates
from coworker.mcp.mcp_test import test_mcp_connection_sync
from coworker.api.state import (
    _invalidate_cached_runtimes,
    app,
    config_controller,
    mcp_manager,
    mcp_sessions,
    provider_manager
)

from fastapi import APIRouter

router = APIRouter()


class ProviderCreate(BaseModel):
    name: str
    provider_type: str
    base_url: str
    api_key: str = ""
    model: str = ""
    context_window: int = 0
    max_output_tokens: int = 0
    vision: bool = False
class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    enabled: Optional[bool] = None
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    vision: Optional[bool] = None
class DefaultProviderPayload(BaseModel):
    provider_id: str
    model: str
class ProviderTestPayload(BaseModel):
    base_url: str
    api_key: str = ""
    model: str
    provider_id: str = ""
class ProviderFetchModelsPayload(BaseModel):
    base_url: str
    api_key: str = ""
    provider_type: str = "custom"
    provider_id: str = ""
class McpServerCreatePayload(BaseModel):
    name: str
    transport: str  # "stdio" | "http" | "sse" | "websocket"
    command: str = ""
    args: str = ""
    cwd: str = ""
    timeout: float | None = None
    url: str = ""
    env: dict[str, str] = {}
    headers: dict[str, str] = {}
class McpServerUpdatePayload(BaseModel):
    name: str | None = None
    transport: str | None = None
    enabled: bool | None = None
    command: str | None = None
    args: str | None = None
    cwd: str | None = None
    timeout: float | None = None
    url: str | None = None
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    trusted: bool | None = None
    disabled_tools: list[str] | None = None
class McpTestPayload(BaseModel):
    transport: str
    command: str = ""
    args: str = ""
    cwd: str = ""
    timeout: float | None = None
    url: str = ""
    env: dict[str, str] = {}
    headers: dict[str, str] = {}
    server_id: str = ""
@router.get("/providers")
def list_providers():
    return provider_manager.public_config()
@router.post("/providers")
def create_provider(request: ProviderCreate):
    try:
        provider = provider_manager.add_provider(
            name=request.name,
            provider_type=request.provider_type,
            base_url=request.base_url,
            api_key=request.api_key,
            model=request.model,
            context_window=request.context_window,
            max_output_tokens=request.max_output_tokens,
            vision=request.vision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _invalidate_cached_runtimes(provider["id"], default_runtimes=True)
    return {"status": "ok", "provider": provider}
@router.put("/providers/default")
def set_default_provider(request: DefaultProviderPayload):
    try:
        config = config_controller.update_runtime_config({
            "selected_provider_id": request.provider_id,
            "selected_model": request.model,
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _invalidate_cached_runtimes(default_runtimes=True)
    return {"status": "ok", "config": config}
@router.put("/providers/{provider_id}")
def update_provider(provider_id: str, request: ProviderUpdate):
    try:
        provider = provider_manager.update_provider(
            provider_id,
            name=request.name,
            base_url=request.base_url,
            api_key=request.api_key,
            model=request.model,
            enabled=request.enabled,
            context_window=request.context_window,
            max_output_tokens=request.max_output_tokens,
            vision=request.vision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _invalidate_cached_runtimes(provider["id"])
    return {"status": "ok", "provider": provider}
@router.post("/providers/{provider_id}/discover-context")
def discover_provider_context(provider_id: str):
    """Probe the provider's local server for its actual context window (tokens).

    For cloud providers the known-model table already covers most cases, so a
    failed probe simply returns 0 and the caller falls back to table/default.
    """
    try:
        config = provider_manager.load()
        provider = provider_manager.require_provider(config, provider_id)
        provider_manager._resolve_secret(provider)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    window, error = provider_manager._fetch_context_window_full(provider)
    if not window or window <= 0:
        raise HTTPException(status_code=404, detail=error or "could not discover context window from this provider")
    try:
        provider_manager.update_provider(provider_id, context_window=window)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _invalidate_cached_runtimes(provider_id)
    return {"status": "ok", "provider": provider_manager.public_provider(provider_manager.require_provider(provider_manager.load(), provider_id))}
@router.delete("/providers/{provider_id}")
def delete_provider(provider_id: str):
    try:
        provider_manager.delete_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _invalidate_cached_runtimes(provider_id, default_runtimes=True)
    return {"status": "ok"}
@router.post("/providers/test")
def test_provider(request: ProviderTestPayload):
    api_key = _resolve_provider_secret(request) if not request.api_key and request.provider_id else request.api_key
    try:
        result = provider_manager.test_provider_connection(request.base_url, api_key, request.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "result": result}
@router.post("/providers/fetch-models")
def fetch_provider_models(request: ProviderFetchModelsPayload):
    api_key = _resolve_provider_secret(request) if not request.api_key and request.provider_id else request.api_key
    try:
        models = provider_manager.fetch_models(request.base_url, api_key, request.provider_type)
    except Exception as exc:
        return {"status": "error", "models": [], "error": str(exc)[:300]}
    return {"status": "ok", "models": models}
@router.get("/providers/templates")
def get_provider_templates():
    """Return provider templates for the frontend picker."""
    from coworker.providers.catalog import get_catalog, to_template_list, get_ordered_keys

    catalog = get_catalog()
    return {
        "status": "ok",
        "templates": to_template_list(),
        "order": get_ordered_keys(),
        "icon_aliases": catalog.get("icon_aliases", {}),
    }
def _resolve_provider_secret(request: BaseModel) -> str:
    """Fill an empty test/fetch api_key from the Keychain-stored secret of the
    provider being edited (key_in_secrets providers keep the JSON blank)."""
    try:
        config = provider_manager.load()
        provider = provider_manager.require_provider(config, request.provider_id)
        provider_manager._resolve_secret(provider)
        return provider.api_key
    except Exception:
        return ""
MCP_CHECK_TIMEOUT_SECONDS = 25.0
def _mcp_not_found(exc: ValueError) -> HTTPException:
    detail = str(exc)
    code = 404 if "not found" in detail.lower() else 400
    return HTTPException(status_code=code, detail=detail)
def _resolve_secret_map(
    incoming: dict[str, str] | None, stored: dict[str, str] | None
) -> dict[str, str]:
    """Swap placeholder values for the real stored secret (test path only)."""
    if not incoming:
        return {}
    stored = stored or {}
    return {
        key: (stored.get(key, "") if value == SECRET_PLACEHOLDER else value)
        for key, value in incoming.items()
    }
def _check_server(server_id: str) -> dict[str, Any]:
    """Run a live connection check and persist the resulting status."""
    runtime = mcp_manager.get_runtime_config(server_id)
    result = test_mcp_connection_sync(
        transport=runtime["transport"],
        command=runtime.get("command", ""),
        args=runtime.get("args", ""),
        cwd=runtime.get("cwd", ""),
        url=runtime.get("url", ""),
        env=runtime.get("env") or {},
        headers=runtime.get("headers") or {},
        timeout=runtime.get("timeout") or MCP_CHECK_TIMEOUT_SECONDS,
    )

    if result["ok"]:
        status = STATUS_CONNECTED
    elif "auth" in (result.get("error") or "").lower() or "401" in (result.get("error") or ""):
        status = STATUS_NEEDS_AUTH
    else:
        status = STATUS_ERROR

    server = mcp_manager.update_server_status(
        server_id,
        status=status,
        error_message=result.get("error", ""),
        tool_count=result.get("tool_count", 0),
        tools=result.get("tools", []),
    )
    # Drop any live session so the next graph build reconnects with the check result.
    mcp_sessions.close_server(server_id)
    return server
@router.get("/mcp/servers")
def list_mcp_servers():
    return {"status": "ok", "servers": mcp_manager.list_servers()}
@router.post("/mcp/servers")
def create_mcp_server(request: McpServerCreatePayload):
    try:
        result = mcp_manager.add_server(
            name=request.name,
            transport=request.transport,
            command=request.command or "",
            args=request.args or "",
            cwd=request.cwd or "",
            timeout=request.timeout,
            url=request.url or "",
            env=request.env or {},
            headers=request.headers or {},
        )
    except ValueError as exc:
        raise _mcp_not_found(exc) from exc
    return {"status": "ok", "server": result}
@router.patch("/mcp/servers/{server_id}")
def update_mcp_server(server_id: str, request: McpServerUpdatePayload):
    kwargs: dict[str, Any] = {"server_id": server_id}
    for key in ("name", "transport", "enabled", "command", "args", "cwd", "timeout", "url", "env", "headers", "trusted", "disabled_tools"):
        value = getattr(request, key)
        if value is not None:
            kwargs[key] = value
    try:
        result = mcp_manager.update_server(**kwargs)
    except ValueError as exc:
        raise _mcp_not_found(exc) from exc
    # A connection-relevant field may have changed: drop the live session so the
    # next graph build reconnects with the new config.
    mcp_sessions.close_server(server_id)
    return {"status": "ok", "server": result}
@router.delete("/mcp/servers/{server_id}")
def delete_mcp_server(server_id: str):
    try:
        mcp_manager.delete_server(server_id)
    except ValueError as exc:
        raise _mcp_not_found(exc) from exc
    mcp_sessions.close_server(server_id)
    return {"status": "ok"}
@router.get("/mcp/discover")
def discover_mcp_templates():
    return {"status": "ok", "servers": resolve_templates()}
@router.post("/mcp/servers/{server_id}/check")
def check_mcp_server(server_id: str):
    try:
        server = _check_server(server_id)
    except ValueError as exc:
        raise _mcp_not_found(exc) from exc
    return {"status": "ok", "server": server}
@router.post("/mcp/check-all")
def check_all_mcp_servers():
    servers = mcp_manager.list_servers(enabled_only=True)
    ids = [entry["id"] for entry in servers]
    if ids:
        with ThreadPoolExecutor(max_workers=min(len(ids), 4)) as pool:
            results = list(pool.map(_check_server, ids))
    return {"status": "ok", "servers": mcp_manager.list_servers()}
@router.post("/mcp/test")
def test_mcp(request: McpTestPayload):
    env = request.env or {}
    headers = request.headers or {}

    # When testing an existing server, placeholder secrets resolve to the real ones.
    if request.server_id:
        try:
            stored = mcp_manager.get_runtime_config(request.server_id)
        except ValueError:
            stored = {}
        env = _resolve_secret_map(env, stored.get("env"))
        headers = _resolve_secret_map(headers, stored.get("headers"))

    result = test_mcp_connection_sync(
        transport=request.transport,
        command=request.command or "",
        args=request.args or "",
        cwd=request.cwd or "",
        url=request.url or "",
        env=env,
        headers=headers,
        timeout=request.timeout or MCP_CHECK_TIMEOUT_SECONDS,
    )
    return {"status": "ok", "result": result}
@router.post("/mcp/servers/{server_id}/reauthorize")
def reauthorize_mcp_server(server_id: str):
    """Run the OAuth 2.1+PKCE browser flow for a remote server and reconnect it."""
    try:
        mcp_manager.get_server(server_id)
    except ValueError as exc:
        raise _mcp_not_found(exc) from exc
    result = mcp_sessions.reauthorize(server_id)
    return {"status": "ok", **result}

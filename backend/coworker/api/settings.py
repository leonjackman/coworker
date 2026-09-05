# -*- coding: utf-8 -*-

import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from coworker.agent.core import (
    AgentMode,
    Language,
    _merge_event_parts,
    context_budget_chars,
    context_budget_tokens,
    format_user_message,
    is_provider_bad_request,
    normalize_autonomy,
    normalize_work_mode,
    _runtime_context_budget,
)
from coworker.goal_feature import goal_feature
from coworker.memory.memory_manager import DEFAULT_AGENT, MemoryConfig, MemoryManager
from coworker.web import (
    ALLOWED_PROVIDERS,
    delete_tavily_key,
    get_tavily_key,
    read_web_block,
    set_tavily_key,
    tavily_key_configured,
    tavily_search,
    write_web_block,
)
from coworker.logger import apply_log_config, current_session_id, get_logger, get_log_settings as _runtime_log_settings, init_logger, is_sensitive_key, redact, set_log_level as _set_log_level, truncate_log as _truncate_log
from coworker.api.state import (
    _HTTP_LOG_ENABLED,
    _invalidate_cached_runtimes,
    app,
    config_controller,
    log_path,
    logger,
    memory_manager,
    set_http_log,
    settings
)

from fastapi import APIRouter

router = APIRouter()


class RuntimeConfigResponse(BaseModel):
    workspace: str
    data_dir: str
    default_mode: AgentMode
    agent_provider: str
    available_modes: list[AgentMode]
    selected_provider_id: str = ""
    selected_model: str = ""
class RuntimeConfigUpdate(BaseModel):
    selected_provider_id: str = ""
    selected_model: str = ""
@router.get("/health")
async def health():
    return {
        "status": "ok",
        "workspace": str(settings.workspace_dir),
        "data_dir": str(settings.data_dir),
        "agent_provider": settings.agent_provider,
        "available_modes": ["single"],
    }
@router.get("/config", response_model=RuntimeConfigResponse)
def runtime_config():
    return RuntimeConfigResponse(**config_controller.runtime_config())
@router.patch("/config", response_model=RuntimeConfigResponse)
def update_runtime_config(request: RuntimeConfigUpdate):
    try:
        result = RuntimeConfigResponse(**config_controller.update_runtime_config(request.model_dump()))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _invalidate_cached_runtimes(default_runtimes=True)
    return result
class SkillReviewSettingsUpdate(BaseModel):
    """Request body for updating the auto-skills review settings (partial patch)."""
    aggressiveness: str | None = None
    approval_required: bool | None = None
def read_user_skill_review_settings() -> dict:
    """Read auto-skills review settings from .coworker_settings.json (defaults fallback)."""
    from coworker.config import read_skill_review_settings

    return read_skill_review_settings(settings.data_dir)
def save_user_skill_review_settings(patch: dict) -> dict:
    """Merge auto-skills review settings into .coworker_settings.json."""
    from coworker.config import SKILL_REVIEW_AGGRESSIVENESS, default_skill_review_settings

    existing = _load_user_settings_file()
    current = existing.get("skill_review")
    if not isinstance(current, dict):
        current = default_skill_review_settings()
    merged = dict(current)
    if "aggressiveness" in patch and patch["aggressiveness"] in SKILL_REVIEW_AGGRESSIVENESS:
        merged["aggressiveness"] = patch["aggressiveness"]
    if "approval_required" in patch and isinstance(patch["approval_required"], bool):
        merged["approval_required"] = patch["approval_required"]
    existing["skill_review"] = merged
    _save_user_settings_file(existing)
    return merged
@router.get("/api/skill-review/settings")
async def get_skill_review_settings():
    """Auto-skills review settings (the Settings page surface)."""
    return read_user_skill_review_settings()
@router.post("/api/skill-review/settings")
async def save_skill_review_settings(request: SkillReviewSettingsUpdate):
    """Persist auto-skills review settings and apply at runtime."""
    patch = {}
    if request.aggressiveness is not None:
        patch["aggressiveness"] = request.aggressiveness
    if request.approval_required is not None:
        patch["approval_required"] = request.approval_required
    try:
        merged = save_user_skill_review_settings(patch)
    except OSError as exc:  # noqa: BLE001 - settings persistence must not fail the request
        logger.warning("Failed to persist skill-review settings: %s", exc)
        return read_user_skill_review_settings()
    return merged
class SettingsUpdate(BaseModel):
    max_attachment_mb: int = 25
    revert_code: Optional[bool] = None
    goal_enabled: Optional[bool] = None
class LogSettingsUpdate(BaseModel):
    log_level: str = "INFO"
class LogConfigUpdate(BaseModel):
    log_level: str | None = None
    log_max_bytes: int | None = Field(default=None, ge=1_048_576, le=1_073_741_824)
    log_backup_count: int | None = Field(default=None, ge=0, le=100)
    json_log: bool | None = None
    http_log: bool | None = None
SETTING_FILE = str(settings.data_dir / ".coworker_settings.json")
DEFAULT_MAX_ATTACHMENT_MB = 25
MIN_MAX_ATTACHMENT_MB = 1
MAX_MAX_ATTACHMENT_MB = 1024
def read_user_max_attachment_mb() -> int:
    """Read the user-level attachment size cap (MB) from .coworker_settings.json.

    Falls back to 25 (the product default) when the file is missing or the key
    is absent. Clamped to the supported 1–1024 MB range.
    """
    try:
        data = json.loads(Path(SETTING_FILE).read_text() or "{}")
        if "max_attachment_mb" in data:
            return max(MIN_MAX_ATTACHMENT_MB, min(MAX_MAX_ATTACHMENT_MB, int(data["max_attachment_mb"])))
    except Exception:
        pass
    return DEFAULT_MAX_ATTACHMENT_MB
def read_user_revert_code() -> bool:
    """Read the user-level "edit message reverts code changes" toggle.

    Defaults to True (align with opencode/Codex: editing a message starts from
    a clean file state). Absent/legacy settings fall back to True.
    """
    try:
        data = json.loads(Path(SETTING_FILE).read_text() or "{}")
        return bool(data.get("revert_code", True))
    except Exception:
        return True
def _load_user_settings_file() -> dict:
    try:
        return json.loads(Path(SETTING_FILE).read_text() or "{}")
    except Exception:
        return {}
def read_user_memory_settings() -> dict:
    """Read the user-level memory settings from .coworker_settings.json.

    Returns only the overrides a user has saved (absent keys fall back to the
    env-var-driven MemoryConfig defaults).
    """
    data = _load_user_settings_file()
    stored = data.get("memory")
    if not isinstance(stored, dict):
        return {}
    known = {"enabled", "auto_extract"}
    return {k: v for k, v in stored.items() if k in known}
def _save_user_settings_file(payload: dict) -> None:
    """Persist .coworker_settings.json atomically (never a truncated JSON)."""
    from coworker.atomicio import atomic_write_text

    path = Path(SETTING_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False))
def save_user_memory_settings(settings: dict) -> None:
    """Merge memory settings into .coworker_settings.json without clobbering others."""
    existing = _load_user_settings_file()
    existing["memory"] = {k: v for k, v in settings.items() if v is not None}
    _save_user_settings_file(existing)
def read_user_retention_settings() -> dict:
    """Read user-level data-retention overrides (trace/audit line caps)."""
    data = _load_user_settings_file()
    stored = data.get("retention")
    if not isinstance(stored, dict):
        return {}
    known = {"trace_lines", "audit_lines"}
    return {k: v for k, v in stored.items() if k in known and isinstance(v, int)}
def save_user_retention_settings(settings: dict) -> None:
    """Merge retention settings into .coworker_settings.json and apply at runtime."""
    from coworker.traces import set_trace_retention
    from coworker.workspace import set_tool_audit_retention

    path = Path(SETTING_FILE)
    existing = _load_user_settings_file()
    merged = {**read_user_retention_settings(), **{k: v for k, v in settings.items() if v is not None}}
    existing["retention"] = merged
    _save_user_settings_file(existing)
    # Apply immediately so the running process trims at the new cap.
    set_trace_retention(merged.get("trace_lines", 0))
    set_tool_audit_retention(merged.get("audit_lines", 0))
def apply_stored_retention_settings() -> None:
    """Apply persisted retention overrides at startup."""
    from coworker.traces import set_trace_retention
    from coworker.workspace import set_tool_audit_retention

    stored = read_user_retention_settings()
    if stored:
        set_trace_retention(stored.get("trace_lines", 0))
        set_tool_audit_retention(stored.get("audit_lines", 0))
LOG_SETTING_ENV = {
    "log_level": "COWORKER_LOG_LEVEL",
    "log_max_bytes": "COWORKER_LOG_MAX_BYTES",
    "log_backup_count": "COWORKER_LOG_BACKUP_COUNT",
    "json_log": "COWORKER_JSON_LOG",
    "http_log": "COWORKER_HTTP_LOG",
}
def read_user_log_settings() -> dict:
    """Read persisted logging overrides from .coworker_settings.json."""
    data = _load_user_settings_file()
    stored = data.get("logging")
    if not isinstance(stored, dict):
        return {}
    return {k: v for k, v in stored.items() if k in LOG_SETTING_ENV and v is not None}
def save_user_log_settings(settings: dict) -> None:
    """Merge logging settings into .coworker_settings.json and apply at runtime."""
    merged = {**read_user_log_settings(), **{k: v for k, v in settings.items() if v is not None}}
    existing = _load_user_settings_file()
    existing["logging"] = {k: v for k, v in merged.items() if k in LOG_SETTING_ENV}
    _save_user_settings_file(existing)
    _apply_user_log_settings(merged)
def _apply_user_log_settings(stored: dict) -> None:
    """Apply logging overrides to the running process (env vars always win)."""
    level = stored.get("log_level") if "COWORKER_LOG_LEVEL" not in os.environ else None
    max_bytes = stored.get("log_max_bytes") if "COWORKER_LOG_MAX_BYTES" not in os.environ else None
    backup_count = stored.get("log_backup_count") if "COWORKER_LOG_BACKUP_COUNT" not in os.environ else None
    json_log = stored.get("json_log") if "COWORKER_JSON_LOG" not in os.environ else None
    apply_log_config(
        level=level,
        max_bytes=max_bytes,
        backup_count=backup_count,
        json_log=json_log,
    )
    http_log = stored.get("http_log")
    if http_log is not None and "COWORKER_HTTP_LOG" not in os.environ:
        set_http_log(bool(http_log))
def apply_stored_log_settings() -> None:
    """Apply persisted logging overrides at startup (after init_logger)."""
    _apply_user_log_settings(read_user_log_settings())
def apply_stored_memory_settings() -> None:
    """Overlay user-saved memory settings onto the runtime MemoryConfig."""
    overrides = read_user_memory_settings()
    if not overrides:
        return
    current = memory_manager.config
    memory_manager.config = MemoryConfig(
        enabled=overrides.get("enabled", current.enabled),
        inject_char_limit=current.inject_char_limit,
        auto_extract=overrides.get("auto_extract", current.auto_extract),
        nudge_interval=current.nudge_interval,
        extract_model=current.extract_model,
        max_prior_loss=current.max_prior_loss,
        dream_idle_seconds=current.dream_idle_seconds,
    )
apply_stored_memory_settings()
apply_stored_retention_settings()
@router.get("/settings")
async def get_settings():
    """Get user-level settings (attachment size cap + edit revert + goal flag)."""
    return {
        "max_attachment_mb": read_user_max_attachment_mb(),
        "revert_code": read_user_revert_code(),
        "goal_enabled": goal_feature.is_enabled(),
    }
@router.post("/settings")
async def set_settings(request: SettingsUpdate):
    """Update user-level settings (attachment size cap + edit revert + goal flag)."""
    max_attachment_mb = max(MIN_MAX_ATTACHMENT_MB, min(MAX_MAX_ATTACHMENT_MB, request.max_attachment_mb))
    try:
        # Merge so the two keys don't clobber each other across saves.
        existing: dict = _load_user_settings_file()
        existing.update({"max_attachment_mb": max_attachment_mb})
        if request.revert_code is not None:
            existing["revert_code"] = bool(request.revert_code)
        if request.goal_enabled is not None:
            existing["goal_enabled"] = bool(request.goal_enabled)
        _save_user_settings_file(existing)
    except Exception as exc:
        return {
            "status": "error",
            "max_attachment_mb": max_attachment_mb,
            "revert_code": read_user_revert_code(),
            "goal_enabled": goal_feature.is_enabled(),
            "detail": str(exc),
        }
    return {
        "status": "ok",
        "max_attachment_mb": max_attachment_mb,
        "revert_code": read_user_revert_code(),
        "goal_enabled": goal_feature.is_enabled(),
    }
class WebConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[str] = None
    browser_engine: Optional[str] = None
    max_results: Optional[int] = None
    search_depth: Optional[str] = None
    fetch_enabled: Optional[bool] = None
class TavilyKeyUpdate(BaseModel):
    api_key: str
class WebTestRequest(BaseModel):
    query: str = "daily news"
    max_results: Optional[int] = None
    api_key: Optional[str] = None
    provider: Optional[str] = None
def _web_config_payload(block: dict) -> dict:
    """Serialize one merged ``web`` settings block (defaults already merged by
    ``read_web_block``), shared by the GET/POST config endpoints."""
    return {
        "enabled": bool(block["enabled"]),
        "provider": str(block["provider"]),
        "browser_engine": str(block["browser_engine"]),
        "max_results": int(block["max_results"]),
        "search_depth": str(block["search_depth"]),
        "fetch_enabled": bool(block["fetch_enabled"]),
        "api_key_configured": tavily_key_configured(settings.data_dir),
    }
@router.get("/api/web/config")
async def get_web_config():
    """Non-secret web capability settings + whether a search key is configured."""
    return _web_config_payload(read_web_block(settings.data_dir))
@router.post("/api/web/config")
async def save_web_config(request: WebConfigUpdate):
    """Persist non-secret web settings to .coworker_settings.json (merge)."""
    patch = {
        k: getattr(request, k)
        for k in ("enabled", "provider", "browser_engine", "max_results", "search_depth", "fetch_enabled")
        if getattr(request, k) is not None
    }
    if not patch:
        return await get_web_config()
    try:
        block = write_web_block(settings.data_dir, patch)
    except OSError as exc:  # noqa: BLE001 - settings persistence must not fail the request
        logger.warning("Failed to persist web settings: %s", exc)
        block = read_web_block(settings.data_dir)
    return _web_config_payload(block)
@router.post("/api/web/tavily/key")
async def set_web_tavily_key(request: TavilyKeyUpdate):
    """Store the Tavily API key in the OS secret store (never returned)."""
    api_key = (request.api_key or "").strip()
    if not api_key:
        return {"status": "error", "detail": "API key is empty"}
    try:
        set_tavily_key(settings.data_dir, api_key)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc)}
    return {"status": "ok", "api_key_configured": True}
@router.delete("/api/web/tavily/key")
async def clear_web_tavily_key():
    """Remove the stored Tavily API key."""
    delete_tavily_key(settings.data_dir)
    return {"status": "ok", "api_key_configured": False}
_SEARCH_TEST_DEADLINE_S = 45.0
def _deadline_search(fn: Any) -> Any:
    """Run a blocking provider search with a hard deadline in a worker thread."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            return future.result(timeout=_SEARCH_TEST_DEADLINE_S)
        except FutureTimeoutError:
            future.cancel()
            raise TimeoutError(f"Search did not finish within {int(_SEARCH_TEST_DEADLINE_S)}s")
def _test_provider_search(request: WebTestRequest) -> dict:
    """Execute one provider test search and return the ``{ok,message,count}`` dict."""
    block = read_web_block(settings.data_dir)
    query = (request.query or "daily news").strip() or "daily news"
    max_results = request.max_results or int(block["max_results"])
    provider = request.provider or str(block["provider"])
    if provider not in ALLOWED_PROVIDERS:
        provider = str(block["provider"])

    if provider == "tavily":
        api_key = (request.api_key or "").strip() or get_tavily_key(settings.data_dir)
        if not api_key:
            return {"ok": False, "message": "Tavily API key is not configured", "results_count": 0}
        result = tavily_search(
            query,
            api_key,
            max_results=max_results,
            search_depth=str(block["search_depth"]),
        )
        if result.get("error"):
            return {"ok": False, "message": result["error"], "results_count": 0}
        return {"ok": True, "message": "Search succeeded", "results_count": len(result.get("results") or [])}

    if provider == "browser":
        from coworker.browser.bridge_client import browser_available
        from coworker.search.browser_engine import BrowserSearchEngine

        if not browser_available(settings.data_dir):
            return {
                "ok": False,
                "message": "Embedded browser is not reachable — open the desktop app's Browser panel",
                "results_count": 0,
            }
        engine = BrowserSearchEngine(
            settings.data_dir,
            engine=str(block["browser_engine"]),
            session_id="settings-test",
        )
        result = engine.search(query, max_results=max_results)
        if result.error:
            return {"ok": False, "message": result.error, "results_count": 0}
        return {"ok": True, "message": "Browser search succeeded", "results_count": len(result.results)}

    from coworker.search.ddgs_engine import DuckDuckGoEngine

    result = DuckDuckGoEngine().search(query, max_results=max_results)
    if result.error:
        return {"ok": False, "message": result.error, "results_count": 0}
    return {"ok": True, "message": "DuckDuckGo search succeeded", "results_count": len(result.results)}
@router.post("/api/web/test")
def test_web_search(request: WebTestRequest):
    """Run a single search through a provider to verify it works.

    Synchronous (not ``async``) so FastAPI runs it in a worker thread: provider
    searches do blocking network I/O (Tavily 30s / DuckDuckGo / embedded
    browser up to ~20s) and must never stall the event loop. A per-search
    deadline (:data:`_SEARCH_TEST_DEADLINE_S`) and a generous Electron timeout
    keep the "Test connection" button responsive even when a backend stalls.

    ``provider`` defaults to the active one; ``api_key`` is honored only for
    the ``tavily`` provider (a pending key typed before saving).
    """
    try:
        return _deadline_search(lambda: _test_provider_search(request))
    except TimeoutError as exc:
        return {"ok": False, "message": str(exc), "results_count": 0}
class BrowserBridgeUpdate(BaseModel):
    port: int
    token: str
@router.get("/api/browser/bridge")
async def get_browser_bridge():
    """Bridge info Electron registered for the embedded browser (may be absent)."""
    from coworker.browser.bridge_client import read_browser_bridge

    info = read_browser_bridge(settings.data_dir)
    if info is None:
        return {"registered": False}
    return {"registered": True, "port": info.port, "token": info.token}
@router.post("/api/browser/bridge")
async def register_browser_bridge(request: BrowserBridgeUpdate):
    """Electron main registers its loopback bridge here at startup.

    Only the desktop app writes this; the bridge client only reads it back.
    """
    from coworker.browser.bridge_client import write_browser_bridge

    write_browser_bridge(settings.data_dir, request.port, request.token)
    return {"ok": True}
@router.get("/settings/retention")
async def get_retention_settings():
    """Current data-retention caps (trace/audit line limits)."""
    from coworker.traces import ACTIVE_TRACE_RETENTION
    from coworker.workspace import ACTIVE_TOOL_AUDIT_RETENTION

    return {
        "trace_lines": ACTIVE_TRACE_RETENTION,
        "audit_lines": ACTIVE_TOOL_AUDIT_RETENTION,
    }
class RetentionUpdate(BaseModel):
    trace_lines: int | None = None
    audit_lines: int | None = None
@router.post("/settings/retention")
async def save_retention_settings(request: RetentionUpdate):
    """Save and immediately apply retention caps for trace/audit logs."""
    if request.trace_lines is not None or request.audit_lines is not None:
        save_user_retention_settings(
            {
                "trace_lines": request.trace_lines,
                "audit_lines": request.audit_lines,
            }
        )
    from coworker.traces import ACTIVE_TRACE_RETENTION
    from coworker.workspace import ACTIVE_TOOL_AUDIT_RETENTION

    return {"status": "ok", "trace_lines": ACTIVE_TRACE_RETENTION, "audit_lines": ACTIVE_TOOL_AUDIT_RETENTION}
@router.get("/settings/log")
async def get_log_settings():
    """Current logging configuration (runtime-effective + persisted)."""
    effective = _runtime_log_settings()
    effective["http_log"] = _HTTP_LOG_ENABLED
    effective["persisted"] = read_user_log_settings()
    return effective
@router.post("/settings/log-level")
async def set_log_level(request: LogSettingsUpdate):
    """Change the log level at runtime (also persisted). Returns current level."""
    level = request.log_level.strip().upper()
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if level not in valid_levels:
        raise HTTPException(status_code=400, detail=f"Invalid log level: {level}. Must be one of {', '.join(valid_levels)}")
    result = _set_log_level(level)
    if result != "ok":
        raise HTTPException(status_code=400, detail=result)
    # Persist so the level survives a restart (unless overridden by env).
    save_user_log_settings({"log_level": level})
    return {"status": "ok", "log_level": level}
@router.post("/settings/log-config")
async def set_log_config(request: LogConfigUpdate):
    """Update logging config (level/rotation/json/http) at runtime and persist."""
    fields = request.model_dump(exclude_unset=True)
    level = fields.get("log_level")
    if level is not None:
        level = str(level).strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise HTTPException(status_code=400, detail=f"Invalid log level: {level}. Must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL.")
    save_user_log_settings(fields)
    return {"status": "ok", **_runtime_log_settings(), "http_log": _HTTP_LOG_ENABLED}
class TruncateLogRequest(BaseModel):
    max_bytes: int | None = None
@router.post("/settings/truncate-log")
async def truncate_log_settings(request: TruncateLogRequest):
    """Truncate the app log file, keeping the last ``max_bytes`` bytes.

    ``max_bytes`` is read from the JSON body (matches the Electron and HTTP
    frontend clients). ``max_bytes <= 0`` clears the file completely.
    """
    mb = request.max_bytes if request.max_bytes is not None else settings.log_max_bytes
    result = _truncate_log(mb)
    return result
@router.get("/settings/log-file")
async def read_log_file(start: int = 0, count: int = 100):
    """Read log lines from the tail of the app log file.

    ``start`` is the number of newest lines to skip (0 = newest lines);
    ``count`` is how many lines to return. Together they page backwards
    from the end of the file: ``lines[-start-count:-start]``.
    ``truncated`` is True when older lines exist before this page.
    """
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        total = len(lines)
        if count <= 0 or start < 0 or total == 0:
            return {"total_lines": total, "lines": [], "truncated": total > 0}
        end = max(0, total - start)
        begin = max(0, end - count)
        page = lines[begin:end]
        return {
            "total_lines": total,
            "lines": page,
            "truncated": begin > 0,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

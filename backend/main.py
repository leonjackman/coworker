# -*- coding: utf-8 -*-
"""
FastAPI entrypoint for the CoWorker backend.

This module is intentionally thin: it bootstraps the shared singletons via
``coworker.api.state``, re-exports the public symbols the rest of the repo
references (``uvicorn main:app``, the PyInstaller spec, and the ``import main``
tests), and wires the per-domain routers registered in ``coworker.api.*``.

All stateful singletons live in exactly one module (coworker/api/state.py) and
all values are defined exactly once; every other module imports them.
"""

import asyncio
import atexit
import json
import os
import re
import time
from pathlib import Path
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from urllib.parse import parse_qsl, urlparse
from dataclasses import replace

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    import fcntl
    import pty
    import termios

    _PTY_AVAILABLE = True
except ImportError:  # pragma: no cover - non-POSIX platforms (e.g. Windows)
    pty = None  # type: ignore[assignment]
    fcntl = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]
    _PTY_AVAILABLE = False

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
from coworker.agent.runtime import AgentRuntimeRegistry
from coworker.platform import default_shell as _platform_default_shell
from coworker.config import load_settings
from coworker.config_controller import AppConfigController
from coworker.events import WorkerEventBus, session_event_bus, worker_event_bus
from coworker.projects import CHAT_MEMORY_DIR, CHAT_PROJECT_ID, ProjectStore
from coworker.providers import ProviderManager
from coworker.mcp.mcp import McpManager
from coworker.mcp.mcp_session import McpSessionManager
from coworker.sessions import SessionStore, _now
from coworker.goal_prompts import (
    is_degenerate_text,
    render_budget_limit,
    render_goal_continuation,
    render_objective_updated,
)
from coworker.goal_feature import goal_feature
from coworker.steer import SteerEntry, steer_inbox
from coworker.skills.skill_manager import SkillManager
from coworker.skills.skills import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH, MAX_SKILL_FILE_BYTES
from coworker.memory.memory_manager import DEFAULT_AGENT, MemoryConfig, MemoryManager
from coworker.memory.layout import AGENT_CORE_FILES, BASE_DIR, SYSTEM_FILES
from coworker.memory.transfer import apply_import, export_memory, preview_import
from coworker.org import (
    AGENT_STATUS_ACTIVE,
    ORG_MODE_MULTI,
    ORG_MODE_SINGLE,
    ORG_MODES,
    Org,
    OrgAgent,
    OrgError,
    OrgStore,
    OrgTeam,
)
from coworker.traces import AGENT_TRACE_FILENAME, MAX_TRACE_LINES
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
from coworker.workspace import COMMAND_APPROVAL_FILENAME, MAX_TOOL_AUDIT_LINES, TOOL_AUDIT_FILENAME, CommandApprovalStore, list_tool_audit_events, trim_jsonl_file, workspace_git_branch, workspace_git_diff
from coworker.workspace_controller import WorkspaceController
from coworker.logger import apply_log_config, current_session_id, get_logger, get_log_settings as _runtime_log_settings, init_logger, is_sensitive_key, redact, set_log_level as _set_log_level, truncate_log as _truncate_log

from coworker.api import state as _state  # noqa: F401  (compose singletons first)

from coworker.api.state import (
    HTTPRequestLogMiddleware,
    _HTTP_LOG_ENABLED,
    _HTTP_LOG_SKIP_PATHS,
    _PTY_AVAILABLE,
    _checkpoint_sweep_loop,
    _checkpoint_sweep_task,
    _create_default_context_with_certifi,
    _invalidate_cached_runtimes,
    _legacy_orphan_log,
    _masked_query,
    _memory_agent_name,
    _memory_project_name,
    _orig_create_default_context,
    _request_session_id,
    _snapshot_gc_loop,
    _snapshot_gc_task,
    _startup_checkpoint_maintenance,
    _stop_checkpoint_maintenance,
    _stream_tasks,
    agent_registry,
    app,
    command_approval_store,
    config_controller,
    fcntl,
    http_logger,
    log_path,
    logger,
    mcp_manager,
    mcp_sessions,
    memory_manager,
    org_store,
    project_store,
    provider_manager,
    pty,
    session_store,
    set_http_log,
    settings,
    skill_manager,
    termios,
    tool_audit_path,
    workspace_controller
)
from coworker.api.streaming import (
    SSE_HEARTBEAT_SECONDS,
    SSE_TIMEOUT,
    TOOL_REPLAY_MAX_TOKENS,
    _cleanup_session_screenshots,
    _emit_goal_cleared,
    _emit_goal_updated,
    _force_stop_session_stream,
    _get_monotonic,
    _goal_round_has_tool_execution,
    _guard_session_not_streaming,
    _hard_stop_session_stream,
    _history_content_chars,
    _history_content_tokens,
    _merge_message_parts,
    _parts_to_conversation,
    _provider_id_for_model,
    _provider_name_for_id,
    _publish_turn,
    _require_goal,
    _resolve_run_provider,
    _revert_turn_changes,
    _session_context_usage_snapshot,
    _session_goal,
    _session_message_history,
    _session_provider_context,
    _session_referenced_ids,
    _sse_events,
    _tracked_stream,
    _truncate_tool_result
)
from coworker.api.memory_org import (
    CHAT_PROJECT_NAME,
    MemoryExportRequest,
    MemoryFileRequest,
    MemoryImportApplyRequest,
    MemoryImportPreviewRequest,
    MemoryMoveRequest,
    MemorySettingsUpdate,
    MemoryWriteRequest,
    OrgAgentCreateRequest,
    OrgAgentDeleteRequest,
    OrgAgentUpdateRequest,
    OrgConfigUpdateRequest,
    OrgTeamCreateRequest,
    OrgTeamDeleteRequest,
    OrgTeamUpdateRequest,
    _CHAT_AGENT_MD,
    _CHAT_BASE_MD,
    _CHAT_SOUL_MD,
    _chat_context_md,
    _ensure_agent_skeleton,
    _ensure_chat_project,
    _ensure_org,
    _is_agent_core_rel,
    _is_protected_memory_file,
    _load_org,
    _memory_extract_llm,
    _memory_transcript,
    _org_public,
    _project_memory_dir,
    _require_multi,
    _scoped_single_project_view,
    _seed_chat_memory,
    _unique_memory_dir,
    apply_import_api,
    delete_memory,
    export_memory_api,
    get_memory_file,
    get_memory_settings,
    memory_discover,
    memory_register_agent,
    memory_register_project,
    memory_status,
    memory_write,
    move_memory,
    org_create_agent,
    org_create_team,
    org_delete_agent,
    org_delete_team,
    org_get,
    org_update_agent,
    org_update_config,
    org_update_team,
    preview_import_api,
    resolve_memory_path,
    save_memory_file,
    save_memory_settings,
    search_memory
)
from coworker.api.settings import (
    BrowserBridgeUpdate,
    DEFAULT_MAX_ATTACHMENT_MB,
    LOG_SETTING_ENV,
    LogConfigUpdate,
    LogSettingsUpdate,
    MAX_MAX_ATTACHMENT_MB,
    MIN_MAX_ATTACHMENT_MB,
    RetentionUpdate,
    RuntimeConfigResponse,
    RuntimeConfigUpdate,
    SETTING_FILE,
    SettingsUpdate,
    SkillReviewSettingsUpdate,
    TavilyKeyUpdate,
    TruncateLogRequest,
    WebConfigUpdate,
    WebTestRequest,
    _SEARCH_TEST_DEADLINE_S,
    _apply_user_log_settings,
    _deadline_search,
    _load_user_settings_file,
    _save_user_settings_file,
    _test_provider_search,
    _web_config_payload,
    apply_stored_log_settings,
    apply_stored_memory_settings,
    apply_stored_retention_settings,
    clear_web_tavily_key,
    get_browser_bridge,
    get_log_settings,
    get_retention_settings,
    get_settings,
    get_skill_review_settings,
    get_web_config,
    health,
    read_log_file,
    read_user_log_settings,
    read_user_max_attachment_mb,
    read_user_memory_settings,
    read_user_retention_settings,
    read_user_revert_code,
    read_user_skill_review_settings,
    register_browser_bridge,
    runtime_config,
    save_retention_settings,
    save_skill_review_settings,
    save_user_log_settings,
    save_user_memory_settings,
    save_user_retention_settings,
    save_user_skill_review_settings,
    save_web_config,
    set_log_config,
    set_log_level,
    set_settings,
    set_web_tavily_key,
    test_web_search,
    truncate_log_settings,
    update_runtime_config
)
from coworker.api.chat import (
    ChatRequest,
    ChatStreamRequest,
    InterjectRequest,
    _build_stream_runtime,
    _cached_provider_unreachable,
    _resolve_references,
    chat_stream,
    interject,
    resolve_request_autonomy
)
from coworker.api.sessions import (
    EditBeginRequest,
    EditMessageRequest,
    GenerateTitleRequest,
    GoalControlRequest,
    GoalEditRequest,
    GoalSetRequest,
    RegenerateRequest,
    SessionCreateRequest,
    SessionRenameRequest,
    create_session,
    delete_session,
    edit_message,
    edit_message_begin,
    edit_message_cancel,
    generate_title_endpoint,
    get_session,
    goal_clear,
    goal_edit,
    goal_get,
    goal_pause,
    goal_resume,
    goal_set,
    list_active_sessions,
    list_sessions,
    mark_session_read,
    redo_message,
    regenerate_message,
    rename_session,
    session_changes,
    session_context_usage,
    stop_session_stream
)
from coworker.api.workspace import (
    ProjectCreateRequest,
    ProjectRenameRequest,
    WorkspaceCommandRequest,
    create_project,
    delete_project,
    diffs_current,
    list_projects,
    project_dashboard,
    rename_project,
    workspace_branch,
    workspace_command,
    workspace_dir,
    workspace_file,
    workspace_file_preview,
    workspace_tree
)
from coworker.api.ops import (
    agent_traces,
    clear_agent_traces,
    clear_checkpoints,
    clear_tool_audit,
    export_agent_traces,
    export_checkpoints,
    export_tool_audit,
    tool_audit
)
from coworker.api.approvals import (
    ApprovalDecisionPayload,
    ApprovalEventBus,
    CommandApprovalResolve,
    _resume_in_background,
    approval_event_bus,
    list_command_approvals,
    resolve_command_approval,
    stream_approval_events,
    stream_worker_events
)
from coworker.api.providers import (
    DefaultProviderPayload,
    MCP_CHECK_TIMEOUT_SECONDS,
    McpServerCreatePayload,
    McpServerUpdatePayload,
    McpTestPayload,
    ProviderCreate,
    ProviderFetchModelsPayload,
    ProviderTestPayload,
    ProviderUpdate,
    _check_server,
    _mcp_not_found,
    _resolve_provider_secret,
    _resolve_secret_map,
    check_all_mcp_servers,
    check_mcp_server,
    create_mcp_server,
    create_provider,
    delete_mcp_server,
    delete_provider,
    discover_mcp_templates,
    discover_provider_context,
    fetch_provider_models,
    get_provider_templates,
    list_mcp_servers,
    list_providers,
    reauthorize_mcp_server,
    set_default_provider,
    test_mcp,
    test_provider,
    update_mcp_server,
    update_provider
)
from coworker.api.terminal import (
    _pipe_terminal,
    ws_terminal
)
from coworker.api.skills import (
    MarketInstallRequest,
    SkillInstallRequest,
    SkillPendingUpdateRequest,
    SkillUpdatePayload,
    SkillValidatePayload,
    _backfill_market_provenance_loop,
    _backfill_market_provenance_once,
    _mark_market_installed,
    _norm_market_key,
    _pending_skill_404,
    approve_pending_skill,
    delete_skill_route,
    get_market_skill_detail,
    get_pending_skill,
    get_skill,
    install_market_skill,
    install_skill_from_content,
    list_hot_market_skills,
    list_market_categories,
    list_market_sources,
    list_pending_skills,
    list_skills,
    reject_pending_skill,
    scan_skills,
    search_market_skills,
    skill_market_manager,
    update_pending_skill,
    update_skill,
    validate_skill
)

from coworker import api as _api

# Register routers in the order the endpoints used to appear in this file so
# FastAPI's route matching order is preserved.
app.include_router(_api.settings.router)
app.include_router(_api.memory_org.router)
app.include_router(_api.chat.router)
app.include_router(_api.sessions.router)
app.include_router(_api.workspace.router)
app.include_router(_api.ops.router)
app.include_router(_api.approvals.router)
app.include_router(_api.providers.router)
app.include_router(_api.terminal.router)
app.include_router(_api.skills.router)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9527)

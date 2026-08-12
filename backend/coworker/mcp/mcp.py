"""MCP (Model Context Protocol) server management.

Handles CRUD operations for MCP server configurations and stores them
in the user's application data directory as JSON.

Secret handling
---------------
``list_servers()`` never returns raw secret values. Every non-empty ``env`` /
``headers`` value is replaced by :data:`SECRET_PLACEHOLDER`. When the client
sends that placeholder back on update, the stored value is preserved. This lets
the UI round-trip a config without destroying API keys, while still allowing a
key to be removed (omit it) or replaced (send a new value).
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONFIG_VERSION = 1

#: Sent to the client in place of any stored secret value.
SECRET_PLACEHOLDER = "__CW_SECRET_KEPT__"

STATUS_UNKNOWN = "unknown"
STATUS_CONNECTING = "connecting"
STATUS_CONNECTED = "connected"
STATUS_ERROR = "error_connecting"
STATUS_NEEDS_AUTH = "needs_auth"
STATUS_DISABLED = "disabled"

VALID_TRANSPORTS = {"stdio", "http", "sse", "streamable_http", "websocket"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class McpServerEntry:
    id: str
    name: str
    transport: str  # "stdio" | "http" | "sse" | "streamable_http" | "websocket"
    command: str = ""
    args: str = ""
    cwd: str = ""
    timeout: float | None = None
    url: str = ""
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    status: str = STATUS_UNKNOWN
    error_message: str = ""
    tool_count: int = 0
    tools: list[dict[str, str]] = field(default_factory=list)
    trusted: bool = False
    disabled_tools: list[str] = field(default_factory=list)
    last_checked_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "McpServerEntry":
        """Build an entry from arbitrary JSON, ignoring unknown keys.

        Hand-edited config files and forward/backward version skew must never
        take down the whole backend, so anything unexpected is dropped and
        anything missing falls back to the dataclass default.
        """
        known = {f.name for f in fields(cls)}
        data = {key: value for key, value in payload.items() if key in known}

        server_id = str(data.get("id") or "").strip() or str(uuid.uuid4())
        name = str(data.get("name") or "").strip() or "Unnamed server"
        transport = str(data.get("transport") or "stdio").strip() or "stdio"

        def _str_map(value: Any) -> dict[str, str]:
            if not isinstance(value, dict):
                return {}
            return {str(k): "" if v is None else str(v) for k, v in value.items()}

        def _tool_list(value: Any) -> list[dict[str, str]]:
            if not isinstance(value, list):
                return []
            out: list[dict[str, str]] = []
            for item in value:
                if isinstance(item, dict):
                    out.append(
                        {
                            "name": str(item.get("name", "")),
                            "description": str(item.get("description", "")),
                        }
                    )
            return out

        try:
            tool_count = int(data.get("tool_count") or 0)
        except (TypeError, ValueError):
            tool_count = 0

        def _timeout(value: Any) -> float | None:
            if value is None or value == "":
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def _disabled_tools(value: Any) -> list[str]:
            if not isinstance(value, list):
                return []
            out: list[str] = []
            for item in value:
                name = str(item or "").strip()
                if name:
                    out.append(name)
            return out

        return cls(
            id=server_id,
            name=name,
            transport=transport,
            command=str(data.get("command") or ""),
            args=str(data.get("args") or ""),
            cwd=str(data.get("cwd") or ""),
            timeout=_timeout(data.get("timeout")),
            url=str(data.get("url") or ""),
            env=_str_map(data.get("env")),
            headers=_str_map(data.get("headers")),
            enabled=bool(data.get("enabled", True)),
            status=str(data.get("status") or STATUS_UNKNOWN),
            error_message=str(data.get("error_message") or ""),
            tool_count=tool_count,
            tools=_tool_list(data.get("tools")),
            trusted=bool(data.get("trusted", False)),
            disabled_tools=_disabled_tools(data.get("disabled_tools")),
            last_checked_at=str(data.get("last_checked_at") or ""),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
        )


@dataclass
class McpConfig:
    version: int = CONFIG_VERSION
    servers: list[McpServerEntry] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "McpConfig":
        if not isinstance(payload, dict):
            payload = {}
        raw_servers = payload.get("servers")
        if not isinstance(raw_servers, list):
            raw_servers = []

        servers: list[McpServerEntry] = []
        for item in raw_servers:
            if not isinstance(item, dict):
                continue
            try:
                servers.append(McpServerEntry.from_dict(item))
            except Exception:  # noqa: BLE001 - one bad row must not kill the file
                continue

        try:
            version = int(payload.get("version", CONFIG_VERSION))
        except (TypeError, ValueError):
            version = CONFIG_VERSION

        return cls(
            version=version,
            servers=servers,
            created_at=str(payload.get("created_at") or _now()),
            updated_at=str(payload.get("updated_at") or _now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class McpManager:
    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ── persistence ────────────────────────────────────────────────

    def load(self) -> McpConfig:
        with self._lock:
            if not self.config_path.exists():
                config = McpConfig()
                self._write(config)
                return config
            try:
                payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - corrupt file falls back to empty
                payload = {}
            return McpConfig.from_dict(payload)

    def save(self, config: McpConfig) -> None:
        with self._lock:
            self._write(config)

    def _write(self, config: McpConfig) -> None:
        config.updated_at = _now()
        tmp_path = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(tmp_path, 0o600)  # secrets in env/headers must not be world-readable
        except OSError:
            pass
        tmp_path.replace(self.config_path)

    # ── CRUD ───────────────────────────────────────────────────────

    def add_server(
        self,
        *,
        name: str,
        transport: str,
        command: str = "",
        args: str = "",
        cwd: str = "",
        timeout: float | None = None,
        url: str = "",
        env: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Server name is required")

        clean_transport = (transport or "stdio").strip().lower()
        if clean_transport not in VALID_TRANSPORTS:
            raise ValueError(f"Unsupported transport: {transport}")

        self._validate_transport_fields(clean_transport, command, url)

        with self._lock:
            config = self.load()
            self._assert_unique_name(config, clean_name, None)

            now = _now()
            server = McpServerEntry(
                id=str(uuid.uuid4()),
                name=clean_name,
                transport=clean_transport,
                command=(command or "").strip(),
                args=args or "",
                cwd=(cwd or "").strip(),
                timeout=timeout,
                url=(url or "").strip(),
                env=self._clean_map(env),
                headers=self._clean_map(headers),
                enabled=True,
                status=STATUS_UNKNOWN,
                tool_count=0,
                created_at=now,
                updated_at=now,
            )
            config.servers.append(server)
            self._write(config)
            return self._public_server(server)

    def update_server(
        self,
        server_id: str,
        *,
        name: str | None = None,
        transport: str | None = None,
        enabled: bool | None = None,
        command: str | None = None,
        args: str | None = None,
        cwd: str | None = None,
        timeout: float | None = None,
        url: str | None = None,
        env: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        trusted: bool | None = None,
        disabled_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            config = self.load()
            server = self._require_server(config, server_id)

            if name is not None:
                clean_name = name.strip()
                if not clean_name:
                    raise ValueError("Server name is required")
                self._assert_unique_name(config, clean_name, server_id)
                server.name = clean_name

            if transport is not None:
                clean_transport = transport.strip().lower()
                if clean_transport not in VALID_TRANSPORTS:
                    raise ValueError(f"Unsupported transport: {transport}")
                server.transport = clean_transport

            if command is not None:
                server.command = command.strip()
            if args is not None:
                server.args = args or ""
            if cwd is not None:
                server.cwd = (cwd or "").strip()
            if timeout is not None:
                try:
                    server.timeout = None if timeout == "" else float(timeout)
                except (TypeError, ValueError):
                    raise ValueError("Timeout must be a number of seconds")
            if url is not None:
                server.url = url.strip()

            self._validate_transport_fields(server.transport, server.command, server.url)

            if env is not None:
                server.env = self._resolve_secrets(self._clean_map(env), server.env)
            if headers is not None:
                server.headers = self._resolve_secrets(self._clean_map(headers), server.headers)

            if enabled is not None:
                server.enabled = enabled
                if not enabled:
                    server.status = STATUS_DISABLED
                elif server.status == STATUS_DISABLED:
                    server.status = STATUS_UNKNOWN

            if trusted is not None:
                server.trusted = trusted

            if disabled_tools is not None:
                server.disabled_tools = [str(item).strip() for item in disabled_tools if str(item).strip()]

            # Any connection-relevant change invalidates the cached health state.
            if any(value is not None for value in (transport, command, args, cwd, timeout, url, env, headers)):
                if server.enabled:
                    server.status = STATUS_UNKNOWN
                server.error_message = ""
                server.tool_count = 0
                server.tools = []
                server.last_checked_at = ""

            server.updated_at = _now()
            self._write(config)
            return self._public_server(server)

    def delete_server(self, server_id: str) -> None:
        with self._lock:
            config = self.load()
            for index, server in enumerate(config.servers):
                if server.id == server_id:
                    config.servers.pop(index)
                    self._write(config)
                    return
            raise ValueError(f"MCP server {server_id} not found")

    # ── status ─────────────────────────────────────────────────────

    def update_server_status(
        self,
        server_id: str,
        status: str,
        error_message: str = "",
        tool_count: int = 0,
        tools: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            config = self.load()
            server = self._require_server(config, server_id)
            server.status = status
            server.error_message = (error_message or "")[:300]
            server.tool_count = tool_count
            server.tools = tools or []
            server.last_checked_at = _now()
            server.updated_at = server.last_checked_at
            self._write(config)
            return self._public_server(server)

    # ── reads ──────────────────────────────────────────────────────

    def list_servers(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        config = self.load()
        servers = config.servers
        if enabled_only:
            servers = [s for s in servers if s.enabled and s.status != STATUS_DISABLED]
        return [self._public_server(s) for s in servers]

    def get_server(self, server_id: str) -> dict[str, Any]:
        return self._public_server(self._require_server(self.load(), server_id))

    def get_runtime_config(self, server_id: str) -> dict[str, Any]:
        """Return a server dict with *real* secrets, for connection use only."""
        server = self._require_server(self.load(), server_id)
        return self._runtime_server(server)

    def list_runtime_configs(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        config = self.load()
        servers = config.servers
        if enabled_only:
            servers = [s for s in servers if s.enabled and s.status != STATUS_DISABLED]
        return [self._runtime_server(s) for s in servers]

    # ── helpers ────────────────────────────────────────────────────

    @staticmethod
    def _clean_map(value: dict[str, str] | None) -> dict[str, str]:
        if not value:
            return {}
        return {
            str(key).strip(): "" if item is None else str(item)
            for key, item in value.items()
            if str(key).strip()
        }

    @staticmethod
    def _resolve_secrets(incoming: dict[str, str], existing: dict[str, str]) -> dict[str, str]:
        """Replace placeholder values with the stored secret.

        The incoming map fully replaces the old one, so omitted keys are
        deleted -- but a key whose value is :data:`SECRET_PLACEHOLDER` keeps the
        value already on disk instead of being wiped.
        """
        resolved: dict[str, str] = {}
        for key, value in incoming.items():
            if value == SECRET_PLACEHOLDER:
                resolved[key] = existing.get(key, "")
            else:
                resolved[key] = value
        return resolved

    @staticmethod
    def _validate_transport_fields(transport: str, command: str, url: str) -> None:
        if transport == "stdio":
            if not (command or "").strip():
                raise ValueError("Command is required for stdio transport")
        elif not (url or "").strip():
            raise ValueError("URL is required for remote transports")

    @staticmethod
    def _assert_unique_name(config: McpConfig, name: str, skip_id: str | None) -> None:
        lowered = name.strip().lower()
        for server in config.servers:
            if server.id == skip_id:
                continue
            if server.name.strip().lower() == lowered:
                raise ValueError(f'An MCP server named "{name}" already exists')

    @staticmethod
    def _require_server(config: McpConfig, server_id: str) -> McpServerEntry:
        for server in config.servers:
            if server.id == server_id:
                return server
        raise ValueError(f"MCP server {server_id} not found")

    @staticmethod
    def _mask(values: dict[str, str]) -> dict[str, str]:
        return {key: (SECRET_PLACEHOLDER if value else "") for key, value in values.items()}

    @classmethod
    def _public_server(cls, server: McpServerEntry) -> dict[str, Any]:
        return {
            "id": server.id,
            "name": server.name,
            "transport": server.transport,
            "command": server.command,
            "args": server.args,
            "cwd": server.cwd,
            "timeout": server.timeout,
            "url": server.url,
            "env": cls._mask(server.env),
            "headers": cls._mask(server.headers),
            "enabled": server.enabled,
            "status": server.status,
            "error_message": server.error_message,
            "tool_count": server.tool_count,
            "tools": list(server.tools),
            "trusted": server.trusted,
            "disabled_tools": list(server.disabled_tools),
            "last_checked_at": server.last_checked_at,
            "created_at": server.created_at,
            "updated_at": server.updated_at,
        }

    @staticmethod
    def _runtime_server(server: McpServerEntry) -> dict[str, Any]:
        return {
            "id": server.id,
            "name": server.name,
            "transport": server.transport,
            "command": server.command,
            "args": server.args,
            "cwd": server.cwd,
            "timeout": server.timeout,
            "url": server.url,
            "env": dict(server.env),
            "headers": dict(server.headers),
            "trusted": server.trusted,
            "disabled_tools": list(server.disabled_tools),
        }

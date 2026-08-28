"""ProviderManager — CRUD, secret management, context-window discovery.

Migrated from the former monolithic ``providers.py``.
Only the parts that reference provider behaviour (ollama /v1 suffix,
repetition_penalty) now go through ``catalog.py``.
"""

from __future__ import annotations

import ipaddress
import json
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..atomicio import atomic_write_text
from .catalog import get_provider_meta
from .models import (
    CONFIG_VERSION,
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_OUTPUT_TOKENS,
    MAX_OUTPUT_TOKENS_MAX,
    MAX_OUTPUT_TOKENS_MIN,
    ProviderEntry,
    ProviderConfig,
)

# TTL for context-window discovery results. Unreachable local servers (e.g. a
# switched-off LAN vLLM box) are expensive to probe, so we never re-probe more
# often than this even while the UI polls /config every few seconds.
_CTX_DISCOVERY_TTL_SECONDS = 60.0
# Timeout (seconds) for a single discovery probe.
_CTX_DISCOVERY_TIMEOUT_SECONDS = 3.0

# Last discovery outcome per provider key: ``(monotonic_ts, window, error)``.
_CTX_DISCOVERY_CACHE: dict[str, tuple[float, int, str | None]] = {}

# A user-configured context window above this many tokens is only trusted when
# the server reports at least that much via live discovery.
_UNVERIFIED_CONTEXT_WINDOW_WARN = 131_072  # 128k


class ProviderManager:
    SECRET_SERVICE = "coworker-provider"

    def __init__(self, config_path: Path, data_dir: Path | None = None):
        self.config_path = config_path
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_dir = data_dir
        # In-memory api_key cache so repeated load() calls don't spawn a
        # `security` subprocess per provider on every request.
        self._key_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Secret store helpers
    # ------------------------------------------------------------------

    def _resolve_secret(self, provider: ProviderEntry) -> None:
        """Fill provider.api_key from the secret store (cached)."""
        if not provider.key_in_secrets:
            return
        cached = self._key_cache.get(provider.id)
        if cached is not None:
            provider.api_key = cached
            return
        if self.data_dir is None:
            return
        from ..secrets import get_secret

        value = get_secret(self.data_dir, self.SECRET_SERVICE, provider.id)
        if value:
            provider.api_key = value
            self._key_cache[provider.id] = value

    def _store_secret(self, provider: ProviderEntry) -> None:
        """Move a plaintext api_key into the secret store, leaving the JSON empty."""
        if not provider.api_key:
            return
        if self.data_dir is None:
            return
        from ..secrets import set_secret

        set_secret(self.data_dir, self.SECRET_SERVICE, provider.id, provider.api_key)
        self._key_cache[provider.id] = provider.api_key
        provider.api_key = ""
        provider.key_in_secrets = True

    def _clear_secret(self, provider: ProviderEntry) -> None:
        """Remove the stored secret and the key_in_secrets marker."""
        if self.data_dir is None:
            return
        from ..secrets import delete_secret, get_secret

        if get_secret(self.data_dir, self.SECRET_SERVICE, provider.id) is None:
            return
        delete_secret(self.data_dir, self.SECRET_SERVICE, provider.id)
        self._key_cache.pop(provider.id, None)
        provider.key_in_secrets = False

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> ProviderConfig:
        if not self.config_path.exists():
            config = ProviderConfig()
            self.save(config)
            return config
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        config = ProviderConfig.from_dict(payload)
        # Resolve secrets into memory; legacy plaintext keys get migrated once.
        migrated = False
        for provider in config.providers:
            self._resolve_secret(provider)
            if provider.api_key and not provider.key_in_secrets:
                self._store_secret(provider)
                migrated = True
        # Migration: remove deprecated 'temperature' field from stored config.
        if payload.get("providers"):
            for p in payload["providers"]:
                if "temperature" in p:
                    migrated = True
                    break
        if migrated:
            self._write_config(config)
            for provider in config.providers:
                self._resolve_secret(provider)
        return config

    def save(self, config: ProviderConfig) -> None:
        config.updated_at = datetime.now(timezone.utc).isoformat()
        for provider in config.providers:
            if provider.api_key:
                self._store_secret(provider)
            elif provider.key_in_secrets:
                self._clear_secret(provider)
        self._key_cache.clear()
        self._write_config(config)

    def _write_config(self, config: ProviderConfig) -> None:
        atomic_write_text(
            self.config_path,
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n",
        )

    # ------------------------------------------------------------------
    # Public views
    # ------------------------------------------------------------------

    def public_config(self) -> dict[str, Any]:
        config = self.load()
        return {
            "status": "ok",
            "providers": [self.public_provider(provider) for provider in config.providers],
            "default_provider_id": config.default_provider_id,
            "default_model": config.default_model,
        }

    def default_provider(self) -> ProviderEntry | None:
        config = self.load()
        if not config.default_provider_id:
            return None
        provider = config.find_enabled(config.default_provider_id)
        if provider and config.default_model and provider.model != config.default_model:
            provider.model = config.default_model
        return provider

    @staticmethod
    def public_provider(provider: ProviderEntry) -> dict[str, Any]:
        window, source, error = ProviderManager._resolve_context_window_full(provider)
        return {
            "id": provider.id,
            "name": provider.name,
            "provider_type": provider.provider_type,
            "base_url": provider.base_url,
            "api_key_present": bool(provider.api_key),
            "api_key_preview": ProviderManager.secret_preview(provider.api_key),
            "model": provider.model,
            "enabled": provider.enabled,
            "context_window": window,
            "context_source": source,
            "context_error": error,
            "max_output_tokens": provider.max_output_tokens if provider.max_output_tokens > 0 else DEFAULT_MAX_OUTPUT_TOKENS,
            "vision": bool(provider.vision),
            "created_at": provider.created_at,
            "updated_at": provider.updated_at,
        }

    @staticmethod
    def secret_preview(secret: str) -> str:
        if not secret:
            return ""
        if len(secret) <= 8:
            return "****"
        return f"{secret[:4]}...{secret[-4:]}"

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_provider(self, *, name: str, provider_type: str, base_url: str, api_key: str = "", model: str = "", context_window: int = 0, max_output_tokens: int = 0, vision: bool = False) -> dict[str, Any]:
        base_url = self.validate_base_url(base_url, provider_type)
        if not name.strip():
            raise ValueError("provider name is required")
        if not model.strip():
            raise ValueError("model is required")

        config = self.load()
        now = datetime.now(timezone.utc).isoformat()
        provider = ProviderEntry(
            id=str(uuid.uuid4()),
            name=name.strip(),
            provider_type=provider_type.strip() or "custom",
            base_url=base_url,
            api_key=api_key,
            model=model.strip(),
            enabled=True,
            created_at=now,
            updated_at=now,
            context_window=max(0, int(context_window or 0)),
            max_output_tokens=max(MAX_OUTPUT_TOKENS_MIN, min(MAX_OUTPUT_TOKENS_MAX, int(max_output_tokens or 0))),
            vision=bool(vision),
        )
        config.providers.append(provider)
        if not config.default_provider_id:
            config.default_provider_id = provider.id
            config.default_model = provider.model
        self.save(config)
        return self.public_provider(provider)

    def update_provider(self, provider_id: str, *, name: str | None = None, base_url: str | None = None, api_key: str | None = None, model: str | None = None, enabled: bool | None = None, context_window: int | None = None, max_output_tokens: int | None = None, vision: bool | None = None) -> dict[str, Any]:
        config = self.load()
        provider = self.require_provider(config, provider_id)
        if name is not None:
            if not name.strip():
                raise ValueError("provider name is required")
            provider.name = name.strip()
        if base_url is not None:
            provider.base_url = self.validate_base_url(base_url, provider.provider_type)
        if api_key is not None:
            provider.api_key = api_key
        if model is not None:
            provider.model = model.strip()
            if provider.model and config.default_provider_id == provider.id:
                config.default_model = provider.model
            if not provider.model and config.default_provider_id == provider.id:
                config.default_provider_id = ""
                config.default_model = ""
        if context_window is not None:
            provider.context_window = max(0, int(context_window))
        if max_output_tokens is not None:
            provider.max_output_tokens = max(MAX_OUTPUT_TOKENS_MIN, min(MAX_OUTPUT_TOKENS_MAX, int(max_output_tokens)))
        if vision is not None:
            provider.vision = bool(vision)
        if enabled is not None:
            provider.enabled = enabled
            if not enabled and config.default_provider_id == provider.id:
                config.default_provider_id = ""
                config.default_model = ""
        provider.updated_at = datetime.now(timezone.utc).isoformat()
        self.save(config)
        return self.public_provider(provider)

    def delete_provider(self, provider_id: str) -> None:
        config = self.load()
        for index, provider in enumerate(config.providers):
            if provider.id != provider_id:
                continue
            config.providers.pop(index)
            if config.default_provider_id == provider_id:
                config.default_provider_id = config.providers[0].id if config.providers else ""
                config.default_model = config.providers[0].model if config.providers and config.providers[0].model else ""
            self.save(config)
            self._key_cache.pop(provider_id, None)
            if self.data_dir is not None and provider.key_in_secrets:
                from ..secrets import delete_secret

                delete_secret(self.data_dir, self.SECRET_SERVICE, provider.id)
            return
        raise ValueError(f"provider {provider_id} not found")

    def set_default_provider(self, provider_id: str, model: str) -> dict[str, Any]:
        config = self.load()
        provider = config.find_enabled(provider_id)
        if not provider:
            raise ValueError(f"provider {provider_id} is not enabled or not found")
        if model != provider.model:
            raise ValueError(f"model \"{model}\" does not match provider \"{provider.name}\"")
        config.default_provider_id = provider.id
        config.default_model = provider.model
        self.save(config)
        return self.public_provider(provider)

    # ------------------------------------------------------------------
    # Connection testing & model fetching
    # ------------------------------------------------------------------

    def test_provider_connection(self, base_url: str, api_key: str, model: str) -> dict[str, Any]:
        self.validate_base_url(base_url, "custom")
        endpoint = self.chat_completions_url(base_url, "custom")
        body = {
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        started_at = time.monotonic()
        try:
            request = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers=headers, method="POST")
            response = urllib.request.urlopen(request, timeout=15)
            latency_ms = round((time.monotonic() - started_at) * 1000)
            if response.status == 200:
                return {"ok": True, "latency_ms": latency_ms}
            return {"ok": False, "latency_ms": latency_ms, "error": f"HTTP {response.status}"}
        except Exception as exc:
            return {"ok": False, "latency_ms": None, "error": str(exc)[:240]}

    def fetch_models(self, base_url: str, api_key: str = "", provider_type: str = "custom") -> list[str]:
        self.validate_base_url(base_url, provider_type)
        base = base_url.rstrip("/")
        meta = get_provider_meta(provider_type)
        is_ollama = provider_type == "ollama" or (meta and meta.get("api_mode") == "ollama")
        if is_ollama:
            url = f"{base}/api/tags"
            headers = {}
        else:
            import re
            has_versioned_path = bool(re.search(r'/v\d+', base))
            url = f"{base}/models" if has_versioned_path else f"{base}/v1/models"
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        request = urllib.request.Request(url, headers=headers, method="GET")
        response = urllib.request.urlopen(request, timeout=15)
        payload = json.loads(response.read().decode())
        if is_ollama:
            return [str(model["name"]) for model in payload.get("models", []) if model.get("name")]
        return [str(model["id"]) for model in payload.get("data", []) if model.get("id")]

    # ------------------------------------------------------------------
    # Context window resolution
    # ------------------------------------------------------------------

    @staticmethod
    def table_context_window(model: str) -> int:
        """Look up a model's context window from the known-model table."""
        from .context_table import MODEL_CONTEXT_TABLE

        name = (model or "").strip().lower()
        if not name:
            return 0
        for prefix, window in MODEL_CONTEXT_TABLE:
            if name.startswith(prefix):
                return window
        return 0

    @staticmethod
    def resolve_context_window(provider: ProviderEntry, model: str | None = None) -> tuple[int, str]:
        window, source, _ = ProviderManager._resolve_context_window_full(provider, model=model)
        return window, source

    @staticmethod
    def _resolve_context_window_full(provider: ProviderEntry, model: str | None = None) -> tuple[int, str, str | None]:
        model = (model or provider.model or "").strip()
        discovered = 0
        discovered_error: str | None = None
        try:
            discovered, discovered_error = ProviderManager._discover_context_window_cached(provider, model)
        except Exception:  # noqa: BLE001 - discovery is best-effort
            discovered, discovered_error = 0, None
        from .context_table import MODEL_CONTEXT_TABLE

        from_table = ProviderManager.table_context_window(model)
        if provider.context_window and provider.context_window > 0:
            effective = provider.context_window
            note: str | None = None
            if discovered and discovered > 0:
                if discovered < effective:
                    effective = discovered
                    note = (
                        f"服务端实际 max_model_len={discovered}，上下文窗口已按 {discovered} 计算"
                    )
            elif ProviderManager._is_local(provider) and discovered_error:
                note = discovered_error
            elif effective > _UNVERIFIED_CONTEXT_WINDOW_WARN and not from_table:
                note = (
                    f"上下文窗口 {effective} 未经服务端验证且偏大。若出现卡死或流式超时，"
                    "请在下方把“上下文窗口”调小（例如 128000），并点“重新探测”验证。"
                )
            return effective, "user", note
        if from_table:
            return from_table, "table", None
        if discovered and discovered > 0:
            return discovered, "discovered", None
        if ProviderManager._is_local(provider) and discovered_error:
            return DEFAULT_CONTEXT_WINDOW, "unreachable", discovered_error
        return DEFAULT_CONTEXT_WINDOW, "default", None

    @staticmethod
    def _is_local(provider: ProviderEntry) -> bool:
        try:
            parsed = urllib.parse.urlparse(provider.base_url)
            hostname = parsed.hostname or ""
            if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
                return True
            address = ipaddress.ip_address(hostname)
            return address.is_private or address.is_loopback
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _discover_context_window_cached(provider: ProviderEntry, model: str | None = None) -> tuple[int, str | None]:
        model = (model or provider.model or "").strip()
        key = f"{provider.provider_type}|{(provider.base_url or '').rstrip('/')}|{model}"
        entry = _CTX_DISCOVERY_CACHE.get(key)
        if entry is not None:
            ts, window, error = entry
            if time.monotonic() - ts < _CTX_DISCOVERY_TTL_SECONDS:
                return window, error
            _CTX_DISCOVERY_CACHE.pop(key, None)
        window, error = ProviderManager._fetch_context_window_full(provider, model)
        _CTX_DISCOVERY_CACHE[key] = (time.monotonic(), window, error)
        return window, error

    @staticmethod
    def cached_context_error(provider: ProviderEntry) -> str | None:
        key = f"{provider.provider_type}|{(provider.base_url or '').rstrip('/')}|{provider.model}"
        entry = _CTX_DISCOVERY_CACHE.get(key)
        if entry is None:
            return None
        ts, _window, error = entry
        if time.monotonic() - ts >= _CTX_DISCOVERY_TTL_SECONDS:
            return None
        return error

    @staticmethod
    def fetch_context_window(provider: ProviderEntry) -> int:
        window, _ = ProviderManager._fetch_context_window_full(provider)
        return window

    @staticmethod
    def _fetch_context_window_full(provider: ProviderEntry, model: str | None = None) -> tuple[int, str | None]:
        base = (provider.base_url or "").rstrip("/")
        if not base:
            return 0, None
        try:
            meta = get_provider_meta(provider.provider_type)
            is_ollama = provider.provider_type == "ollama" or (meta and meta.get("api_mode") == "ollama")
            if is_ollama:
                window, error = ProviderManager._fetch_ollama_ctx(base, model)
                if window and window > 0:
                    return window, None
                return 0, error
            if provider.provider_type in {"llamacpp", "llmstudio", "lmstudio"}:
                props = ProviderManager._http_get(f"{base}/props")
                if props:
                    n_ctx = (
                        props.get("default_generation_settings", {})
                        .get("n_ctx")
                        or props.get("n_ctx")
                    )
                    if isinstance(n_ctx, int) and n_ctx > 0:
                        return n_ctx, None
                return 0, None
            import re
            has_versioned_path = bool(re.search(r'/v\d+', base))
            url = f"{base}/models" if has_versioned_path else f"{base}/v1/models"
            payload = ProviderManager._http_get(url, provider)
            if isinstance(payload, dict):
                for item in payload.get("data", []):
                    if model and item.get("id") != model:
                        continue
                    for key in ("max_model_len", "context_window", "context_length"):
                        value = item.get(key)
                        if isinstance(value, int) and value > 0:
                            return value, None
        except Exception as exc:  # noqa: BLE001 - discovery is best-effort
            return 0, ProviderManager._probe_error_message(provider, exc)
        return 0, None

    @staticmethod
    def _probe_error_message(provider: ProviderEntry, exc: Exception) -> str:
        endpoint = provider.base_url or ""
        try:
            parsed = urllib.parse.urlparse(endpoint)
            if parsed.hostname:
                port = parsed.port
                endpoint = f"{parsed.hostname}{':' + str(port) if port else ''}"
        except (ValueError, TypeError):
            pass
        reason = "timeout" if isinstance(exc, TimeoutError) else str(exc)
        return f"LLM服务不可达 {endpoint}（{reason}）"

    @staticmethod
    def _fetch_ollama_ctx(base: str, model: str) -> tuple[int, str | None]:
        try:
            payload = json.dumps({"model": model or ""}).encode()
            request = urllib.request.Request(f"{base}/api/show", data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=_CTX_DISCOVERY_TIMEOUT_SECONDS) as response:
                data = json.loads(response.read().decode())
            model_info = data.get("model_info") or {}
            for key, value in model_info.items():
                if isinstance(key, str) and key.endswith(".context_length") and isinstance(value, int) and value > 0:
                    return value, None
            params = data.get("parameters") or ""
            import re

            match = re.search(r"num_ctx\s+(\d+)", str(params))
            if match:
                return int(match.group(1)), None
        except Exception as exc:  # noqa: BLE001
            return 0, str(exc)
        return 0, None

    @staticmethod
    def _http_get(url: str, provider: ProviderEntry | None = None) -> dict | None:
        headers = {"Content-Type": "application/json"}
        if provider and provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=_CTX_DISCOVERY_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode())

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    @staticmethod
    def validate_base_url(base_url: str, provider_type: str = "") -> str:
        normalized = base_url.strip().rstrip("/")
        if not normalized:
            raise ValueError("base_url is required")
        parsed = urllib.parse.urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("provider base_url must be http or https")
        hostname = parsed.hostname.lower()
        local_hostname = hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local")
        private_or_loopback = False
        try:
            address = ipaddress.ip_address(hostname)
            private_or_loopback = address.is_private or address.is_loopback
        except ValueError:
            private_or_loopback = False
        if parsed.scheme == "http" and provider_type != "custom" and not (local_hostname or private_or_loopback):
            raise ValueError("public providers must use https")
        return normalized

    @staticmethod
    def chat_completions_url(base_url: str, provider_type: str = "") -> str:
        base = base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        # Detect if base_url already contains a versioned path segment (e.g. /v1, /v1beta/openai)
        # to avoid double-appending /v1.
        import re
        has_versioned_path = bool(re.search(r'/v\d+', base))
        if has_versioned_path:
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    @staticmethod
    def require_provider(config: ProviderConfig, provider_id: str) -> ProviderEntry:
        for provider in config.providers:
            if provider.id == provider_id:
                return provider
        raise ValueError(f"provider {provider_id} not found")

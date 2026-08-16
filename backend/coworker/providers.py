import json
import ipaddress
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomicio import atomic_write_text

CONFIG_VERSION = 1


@dataclass
class ProviderEntry:
    id: str
    name: str
    provider_type: str
    base_url: str
    api_key: str = ""
    model: str = ""
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""
    # When True the real api_key lives in the OS secret store (Keychain) and the
    # JSON only carries an empty placeholder; load() resolves it in memory.
    key_in_secrets: bool = False
    # Context window in tokens. 0 = unknown: resolved at runtime via
    # resolve_context_window() (user override > MODEL_CONTEXT_TABLE > discover > 128k).
    context_window: int = 0


# ---------------------------------------------------------------------------
# Known cloud model context windows (id-prefix matched, tokens).
# Order matters: more specific prefixes must come before broader ones.
# ---------------------------------------------------------------------------
MODEL_CONTEXT_TABLE: list[tuple[str, int]] = [
    # ---- OpenAI ----------------------------------------------------------
    ("gpt-5.6", 1_050_000),
    ("gpt-5.5", 1_050_000),
    ("gpt-5.4", 1_050_000),
    ("gpt-5.3", 1_050_000),
    ("gpt-5.2", 1_050_000),
    ("gpt-5.1", 1_050_000),
    ("gpt-5", 400_000),
    ("gpt-4.1", 1_000_000),
    ("gpt-4o-mini", 128_000),
    ("gpt-4o", 128_000),
    ("gpt-4", 128_000),
    ("o4-mini", 200_000),
    ("o3-mini", 200_000),
    ("o3", 200_000),
    ("o1", 200_000),
    ("gpt-oss-120b", 131_072),
    ("gpt-oss-20b", 131_072),
    ("gpt-oss", 131_072),
    # ---- Anthropic (haiku before claude so the narrower prefix wins) ------
    ("claude-haiku", 200_000),
    ("claude-sonnet", 1_000_000),
    ("claude-opus", 1_000_000),
    ("claude-fable", 1_000_000),
    ("claude-mythos", 1_000_000),
    ("claude", 1_000_000),
    # ---- Google Gemini / Gemma -------------------------------------------
    ("gemini-3", 1_000_000),
    ("gemini-2.5", 1_000_000),
    ("gemini-2.0", 1_000_000),
    ("gemini", 1_000_000),
    ("gemma4:12b", 262_144),
    ("gemma4:26b", 262_144),
    ("gemma4:31b", 262_144),
    ("gemma4", 131_072),
    ("gemma3", 131_072),
    ("gemma2", 8_192),
    ("gemma", 8_192),
    # ---- DeepSeek ---------------------------------------------------------
    ("deepseek-v4", 1_000_000),
    ("deepseek-v3.1", 131_072),
    ("deepseek-v3", 131_072),
    ("deepseek-r1", 131_072),
    ("deepseek-coder-v2", 131_072),
    ("deepseek", 1_000_000),
    # ---- Meta Llama -------------------------------------------------------
    ("llama4:scout", 10_000_000),
    ("llama4:maverick", 1_000_000),
    ("llama4", 10_000_000),
    ("llama3.3", 128_000),
    ("llama3.2", 128_000),
    ("llama3.1", 128_000),
    ("llama3", 8_192),
    # ---- Qwen (specific variants before broad prefixes) --------------------
    ("qwen3.8", 262_144),
    ("qwen3.6", 262_144),
    ("qwen3.5", 262_144),
    ("qwen3:4b", 262_144),
    ("qwen3:30b", 262_144),
    ("qwen3:235b", 262_144),
    ("qwen3:32b", 262_144),
    ("qwen3", 40_960),
    ("qwen2.5-coder", 131_072),
    ("qwen2.5", 131_072),
    ("qwen2", 131_072),
    ("qwq", 131_072),
    # ---- Z.AI GLM ---------------------------------------------------------
    ("glm-5.2", 1_000_000),
    ("glm-5.1", 198_000),
    ("glm-5", 198_000),
    ("glm-4.7", 198_000),
    ("glm-4.6", 198_000),
    ("glm-4.5", 128_000),
    ("glm-4", 128_000),
    ("glm", 128_000),
    # ---- Moonshot / Kimi --------------------------------------------------
    ("kimi-k3", 1_000_000),
    ("kimi-k2.7", 262_144),
    ("kimi-k2.6", 262_144),
    ("kimi-k2.5", 131_072),
    ("kimi", 131_072),
    # ---- MiniMax ----------------------------------------------------------
    ("minimax-m3", 1_000_000),
    ("minimax-m2.7", 131_072),
    ("minimax-m2.5", 131_072),
    ("minimax", 131_072),
    # ---- Mistral ----------------------------------------------------------
    ("mistral-medium-3.5", 262_144),
    ("mistral-medium", 262_144),
    ("mistral-large-3", 131_072),
    ("mistral-large", 128_000),
    ("mistral-small", 128_000),
    ("codestral", 32_000),
    ("mixtral", 32_000),
    ("mistral", 32_000),
    # ---- xAI Grok ---------------------------------------------------------
    ("grok-4.5", 262_144),
    ("grok-4", 131_072),
    ("grok-3", 131_072),
    ("grok", 131_072),
    # ---- Microsoft Phi ----------------------------------------------------
    ("phi-4", 128_000),
    ("phi4", 128_000),
    ("phi-3", 128_000),
    ("phi3", 128_000),
    # ---- IBM Granite ------------------------------------------------------
    ("granite4.1", 131_072),
    ("granite4", 131_072),
    ("granite3.3", 128_000),
    ("granite3", 128_000),
    ("granite", 128_000),
    # ---- ByteDance Doubao / Baidu Ernie / others (cloud) -------------------
    ("doubao", 262_144),
    ("ernie", 128_000),
    ("wenxin", 128_000),
    ("internlm", 1_000_000),
    ("yi-", 200_000),
    ("yi", 32_000),
]

DEFAULT_CONTEXT_WINDOW = 128_000


@dataclass
class ProviderConfig:
    version: int = CONFIG_VERSION
    providers: list[ProviderEntry] = field(default_factory=list)
    default_provider_id: str = ""
    default_model: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ProviderConfig":
        providers = [ProviderEntry(**item) for item in payload.get("providers", [])]
        config = cls(
            version=int(payload.get("version", CONFIG_VERSION)),
            providers=providers,
            default_provider_id=str(payload.get("default_provider_id", "")),
            default_model=str(payload.get("default_model", "")),
            created_at=str(payload.get("created_at", datetime.now(timezone.utc).isoformat())),
            updated_at=str(payload.get("updated_at", datetime.now(timezone.utc).isoformat())),
        )
        if config.default_provider_id and not config.find_enabled(config.default_provider_id):
            config.default_provider_id = ""
            config.default_model = ""
        return config

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def find_enabled(self, provider_id: str) -> ProviderEntry | None:
        for provider in self.providers:
            if provider.id == provider_id and provider.enabled:
                return provider
        return None


class ProviderManager:
    SECRET_SERVICE = "coworker-provider"

    def __init__(self, config_path: Path, data_dir: Path | None = None):
        self.config_path = config_path
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_dir = data_dir
        # In-memory api_key cache so repeated load() calls don't spawn a
        # `security` subprocess per provider on every request.
        self._key_cache: dict[str, str] = {}

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
        from .secrets import get_secret

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
        from .secrets import set_secret

        set_secret(self.data_dir, self.SECRET_SERVICE, provider.id, provider.api_key)
        self._key_cache[provider.id] = provider.api_key
        provider.api_key = ""
        provider.key_in_secrets = True

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
        if migrated:
            # Write directly (NOT via save()): save() would see the just-emptied
            # api_key + key_in_secrets and _clear_secret() the brand-new Keychain
            # entry, silently losing the legacy plaintext key.
            self._write_config(config)
            # Repopulate the in-memory key for the callers of this load() so the
            # very first request after the upgrade still sees the provider's key.
            for provider in config.providers:
                self._resolve_secret(provider)
        return config

    def save(self, config: ProviderConfig) -> None:
        config.updated_at = datetime.now(timezone.utc).isoformat()
        # Move any plaintext keys into the secret store; an explicit empty key
        # clears the stored secret instead of leaving a stale Keychain entry.
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

    def _clear_secret(self, provider: ProviderEntry) -> None:
        """Remove the stored secret and the key_in_secrets marker.

        Only when a secret actually exists — if the Keychain entry was deleted
        out-of-band (e.g. the user emptied the keychain), keep ``key_in_secrets``
        True so a restored entry is still resolved instead of silently flipping
        the provider to "no key".
        """
        if self.data_dir is None:
            return
        from .secrets import delete_secret, get_secret

        if get_secret(self.data_dir, self.SECRET_SERVICE, provider.id) is None:
            return
        delete_secret(self.data_dir, self.SECRET_SERVICE, provider.id)
        self._key_cache.pop(provider.id, None)
        provider.key_in_secrets = False

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

    def add_provider(self, *, name: str, provider_type: str, base_url: str, api_key: str = "", model: str = "", context_window: int = 0) -> dict[str, Any]:
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
        )
        config.providers.append(provider)
        if not config.default_provider_id:
            config.default_provider_id = provider.id
            config.default_model = provider.model
        self.save(config)
        return self.public_provider(provider)

    def update_provider(self, provider_id: str, *, name: str | None = None, base_url: str | None = None, api_key: str | None = None, model: str | None = None, enabled: bool | None = None, context_window: int | None = None) -> dict[str, Any]:
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
                from .secrets import delete_secret

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

    def test_provider_connection(self, base_url: str, api_key: str, model: str) -> dict[str, Any]:
        # Apply the same URL guard as create/update so the test endpoint cannot
        # be used as an arbitrary-network scanner. Custom type keeps plain-http
        # LAN/Ollama endpoints working (see validate_base_url).
        self.validate_base_url(base_url, "custom")
        endpoint = self.chat_completions_url(base_url)
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
        if provider_type == "ollama":
            url = f"{base}/api/tags"
            headers = {}
        else:
            url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        request = urllib.request.Request(url, headers=headers, method="GET")
        response = urllib.request.urlopen(request, timeout=15)
        payload = json.loads(response.read().decode())
        if provider_type == "ollama":
            return [str(model["name"]) for model in payload.get("models", []) if model.get("name")]
        return [str(model["id"]) for model in payload.get("data", []) if model.get("id")]

    @staticmethod
    def table_context_window(model: str) -> int:
        """Look up a model's context window from the known-model table.

        Prefix-matched from most specific to most generic; returns 0 when the
        model is unknown.
        """
        name = (model or "").strip().lower()
        if not name:
            return 0
        for prefix, window in MODEL_CONTEXT_TABLE:
            if name.startswith(prefix):
                return window
        return 0

    @staticmethod
    def resolve_context_window(provider: ProviderEntry) -> tuple[int, str]:
        """Resolve the effective context window (tokens) for a provider.

        Priority: user override > known-model table > local-server discovery >
        default. Returns ``(window, source)`` where source is one of
        ``"user"``, ``"table"``, ``"discovered"``, ``"default"``.
        """
        if provider.context_window and provider.context_window > 0:
            return provider.context_window, "user"
        from_table = ProviderManager.table_context_window(provider.model)
        if from_table:
            return from_table, "table"
        if provider.provider_type in {"ollama", "llamacpp", "llmstudio", "lmstudio", "vllm"} or ProviderManager._is_local(provider):
            discovered = ProviderManager.fetch_context_window(provider)
            if discovered and discovered > 0:
                return discovered, "discovered"
        return DEFAULT_CONTEXT_WINDOW, "default"

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
    def fetch_context_window(provider: ProviderEntry) -> int:
        """Best-effort discovery of the context window from a local server.

        - ollama: ``POST {base}/api/show`` → ``model_info.*.context_length`` or
          ``parameters`` containing ``num_ctx``.
        - llamacpp: ``GET {base}/props`` → ``default_generation_settings.n_ctx``.
        - OpenAI-compatible local servers: ``GET /v1/models`` extended fields.

        Returns 0 when the value cannot be determined (caller falls back).
        """
        base = (provider.base_url or "").rstrip("/")
        if not base:
            return 0
        try:
            if provider.provider_type == "ollama":
                return ProviderManager._fetch_ollama_ctx(base, provider.model)
            if provider.provider_type in {"llamacpp", "llmstudio", "lmstudio"}:
                props = ProviderManager._http_get(f"{base}/props")
                if props:
                    n_ctx = (
                        props.get("default_generation_settings", {})
                        .get("n_ctx")
                        or props.get("n_ctx")
                    )
                    if isinstance(n_ctx, int) and n_ctx > 0:
                        return n_ctx
                return 0
            # Generic OpenAI-compatible: try /v1/models extended fields.
            url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
            payload = ProviderManager._http_get(url, provider)
            if isinstance(payload, dict):
                for item in payload.get("data", []):
                    if item.get("id") != provider.model:
                        continue
                    for key in ("max_model_len", "context_window", "context_length"):
                        value = item.get(key)
                        if isinstance(value, int) and value > 0:
                            return value
        except Exception:  # noqa: BLE001 - discovery is best-effort
            return 0
        return 0

    @staticmethod
    def _fetch_ollama_ctx(base: str, model: str) -> int:
        try:
            payload = json.dumps({"model": model or ""}).encode()
            request = urllib.request.Request(f"{base}/api/show", data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode())
            model_info = data.get("model_info") or {}
            for key, value in model_info.items():
                if isinstance(key, str) and key.endswith(".context_length") and isinstance(value, int) and value > 0:
                    return value
            params = data.get("parameters") or ""
            import re

            match = re.search(r"num_ctx\s+(\d+)", str(params))
            if match:
                return int(match.group(1))
        except Exception:  # noqa: BLE001
            return 0
        return 0

    @staticmethod
    def _http_get(url: str, provider: ProviderEntry | None = None) -> dict | None:
        headers = {"Content-Type": "application/json"}
        if provider and provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode())

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
        # Custom providers are explicitly configured by the user and may legitimately
        # run over plain http (e.g. internal/LAN OpenAI-compatible servers). The
        # "public providers must use https" rule therefore only applies to non-custom
        # provider types (localhost/private addresses are still always allowed).
        if parsed.scheme == "http" and provider_type != "custom" and not (local_hostname or private_or_loopback):
            raise ValueError("public providers must use https")
        return normalized

    @staticmethod
    def chat_completions_url(base_url: str) -> str:
        base = base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    @staticmethod
    def public_provider(provider: ProviderEntry) -> dict[str, Any]:
        window, source = ProviderManager.resolve_context_window(provider)
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

    @staticmethod
    def require_provider(config: ProviderConfig, provider_id: str) -> ProviderEntry:
        for provider in config.providers:
            if provider.id == provider_id:
                return provider
        raise ValueError(f"provider {provider_id} not found")

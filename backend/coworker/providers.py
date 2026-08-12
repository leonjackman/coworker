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
            self.save(config)
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
        atomic_write_text(
            self.config_path,
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n",
        )

    def _clear_secret(self, provider: ProviderEntry) -> None:
        """Remove the stored secret and the key_in_secrets marker."""
        if self.data_dir is None:
            return
        from .secrets import delete_secret

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

    def add_provider(self, *, name: str, provider_type: str, base_url: str, api_key: str = "", model: str = "") -> dict[str, Any]:
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
        )
        config.providers.append(provider)
        if not config.default_provider_id:
            config.default_provider_id = provider.id
            config.default_model = provider.model
        self.save(config)
        return self.public_provider(provider)

    def update_provider(self, provider_id: str, *, name: str | None = None, base_url: str | None = None, api_key: str | None = None, model: str | None = None, enabled: bool | None = None) -> dict[str, Any]:
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
        return {
            "id": provider.id,
            "name": provider.name,
            "provider_type": provider.provider_type,
            "base_url": provider.base_url,
            "api_key_present": bool(provider.api_key),
            "api_key_preview": ProviderManager.secret_preview(provider.api_key),
            "model": provider.model,
            "enabled": provider.enabled,
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

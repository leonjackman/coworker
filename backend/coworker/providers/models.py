"""Provider data classes and constants.

This module is a leaf in the import DAG: catalog → context_table → models → manager.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

CONFIG_VERSION = 1
DEFAULT_CONTEXT_WINDOW = 128_000
DEFAULT_MAX_OUTPUT_TOKENS = 8192
MAX_OUTPUT_TOKENS_MIN = 0
MAX_OUTPUT_TOKENS_MAX = 1_000_000


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
    # Per-request max output tokens. 0 = unset → DEFAULT_MAX_OUTPUT_TOKENS (8192).
    max_output_tokens: int = 0
    # Multimodal (vision) capability.
    vision: bool = False


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
        _known = ProviderEntry.__dataclass_fields__
        providers = [ProviderEntry(**{k: v for k, v in item.items() if k in _known}) for item in payload.get("providers", [])]
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

"""Provider catalog loader and query helpers.

Loads ``catalog.json`` once at startup, caches in memory.
All query functions fall back gracefully for unknown provider types
(e.g. user-created ``"custom"`` providers).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Module-level singleton: loaded once on first access.
_catalog: dict[str, Any] | None = None


def _load_catalog() -> dict[str, Any]:
    """Load catalog.json from the same directory as this module."""
    global _catalog
    if _catalog is not None:
        return _catalog
    _catalog_path = Path(__file__).parent / "catalog.json"
    with open(_catalog_path, encoding="utf-8") as f:
        _catalog = json.load(f)
    return _catalog


def get_catalog() -> dict[str, Any]:
    """Return the full catalog dict."""
    return _load_catalog()


def get_provider_meta(provider_type: str) -> dict[str, Any] | None:
    """Return metadata for *provider_type*, or ``None`` if unknown."""
    return get_catalog().get("providers", {}).get(provider_type)


def get_icon(provider_type: str) -> str | None:
    """Return the icon key for *provider_type*.

    Priority: catalog provider entry > icon_aliases > None.
    """
    providers = get_catalog().get("providers", {})
    aliases = get_catalog().get("icon_aliases", {})
    if provider_type in providers:
        return providers[provider_type].get("icon")
    return aliases.get(provider_type)


def get_ordered_keys() -> list[str]:
    """Return provider keys in the configured display order."""
    return get_catalog().get("order", list(get_catalog().get("providers", {}).keys()))


def to_template_list() -> list[dict[str, Any]]:
    """Return a list suitable for the frontend template picker."""
    providers = get_catalog().get("providers", {})
    return [
        {
            "key": key,
            "name": meta["name"],
            "base_url": meta["base_url"],
            "icon": meta.get("icon"),
        }
        for key, meta in providers.items()
    ]


def merge_model_context_prefixes() -> list[tuple[str, int]]:
    """Merge all provider ``model_context_prefixes`` + global prefixes into a single list.

    Returns ``(prefix, tokens)`` tuples sorted by prefix length (longest
    first) so that more specific prefixes match before broader ones.
    """
    catalog = get_catalog()
    providers = catalog.get("providers", {})
    prefixes: list[tuple[str, int]] = []
    # Provider-specific prefixes
    for meta in providers.values():
        for prefix, tokens in meta.get("model_context_prefixes", {}).items():
            prefixes.append((prefix, tokens))
    # Global prefixes (apply to any provider)
    for prefix, tokens in catalog.get("global_prefixes", {}).items():
        prefixes.append((prefix, tokens))
    # Sort by prefix length descending so longer (more specific) prefixes
    # are checked first — same invariant as the old MODEL_CONTEXT_TABLE.
    prefixes.sort(key=lambda x: len(x[0]), reverse=True)
    return prefixes

"""Provider configuration package.

Layout:
- catalog.json / catalog.py — provider metadata (loaded at startup from JSON)
- models.py — ProviderEntry / ProviderConfig dataclasses + constants
- context_table.py — MODEL_CONTEXT_TABLE (merged from catalog.json)
- manager.py — ProviderManager (CRUD, secret management, context-window discovery)

Import DAG: catalog → context_table → models → manager

All public symbols are re-exported so existing import paths continue to work:
    from coworker.providers import ProviderManager, ProviderEntry, ...
"""

from .catalog import get_catalog, get_provider_meta, get_icon, to_template_list, get_ordered_keys
from .models import (
    CONFIG_VERSION,
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_OUTPUT_TOKENS,
    MAX_OUTPUT_TOKENS_MIN,
    MAX_OUTPUT_TOKENS_MAX,
    ProviderEntry,
    ProviderConfig,
)
from .context_table import MODEL_CONTEXT_TABLE
from .manager import ProviderManager

__all__ = [
    # Constants
    "CONFIG_VERSION",
    "DEFAULT_CONTEXT_WINDOW",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "MAX_OUTPUT_TOKENS_MIN",
    "MAX_OUTPUT_TOKENS_MAX",
    # Data classes
    "ProviderEntry",
    "ProviderConfig",
    # Catalog data
    "MODEL_CONTEXT_TABLE",
    # Catalog query helpers
    "get_catalog",
    "get_provider_meta",
    "get_icon",
    "to_template_list",
    "get_ordered_keys",
    # Manager
    "ProviderManager",
]

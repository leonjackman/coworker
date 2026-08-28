"""Merged model context window table.

Built from all providers' ``model_context_prefixes`` in catalog.json.
"""

from .catalog import merge_model_context_prefixes

MODEL_CONTEXT_TABLE = merge_model_context_prefixes()

"""Per-call context-editing middleware using Coworker's CJK-aware counter.

LangChain's ``ContextEditingMiddleware`` counts tokens with
``count_tokens_approximately`` (pure ``chars/4``), which is ASCII-biased and
under-counts dense CJK by ~2.4x — so its 75% clear trigger disagreed with the
CJK-counted compaction/guard layers on the SAME message list. This middleware
is behaviour-identical but drives every edit with ``cjk_token_counter`` so all
per-call clearing shares one ruler.
"""

from __future__ import annotations

import copy
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from .base import cjk_token_counter


class CoworkerContextEditingMiddleware(AgentMiddleware):
    """Apply context edits (tool-result clearing) with the CJK-aware counter."""

    def __init__(self, *, edits: Any | None = None) -> None:
        from langchain.agents.middleware import ClearToolUsesEdit

        self.edits = list(edits or (ClearToolUsesEdit(),))

    @staticmethod
    def _count_tokens(messages: Any) -> int:
        return cjk_token_counter(messages)

    def _apply(self, request: Any) -> Any:
        if not request.messages:
            return request
        edited_messages = copy.deepcopy(list(request.messages))
        for edit in self.edits:
            edit.apply(edited_messages, count_tokens=self._count_tokens)
        return request.override(messages=edited_messages)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._apply(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._apply(request))

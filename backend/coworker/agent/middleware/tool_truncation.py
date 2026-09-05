"""Content-aware tool-result truncation + recovery-aware tool clearing.

Two cooperating pieces used by the per-call editing layer, the context guard
(S0/S3) and the summarization prune path:

* ``truncate_tool_content`` — field-aware, tail-preserving elision. When a tool
  result is a JSON object (read_file previews, run_command outputs, search
  results) the big TEXT fields are cut head+tail while structural keys are kept
  verbatim, so the actionable pointers Coworker puts at the END of its payloads
  survive: ``read_file``'s ``next_offset``/``hint`` and ``run_command``'s
  ``[stdout truncated; full output saved to: …]`` note. Raw text results fall
  back to a whole-content head+tail elision (codex ``TruncationPolicy`` style).

* ``RecoveryToolClearEdit`` — like LangChain ``ClearToolUsesEdit`` but clears
  with a RECOVERY placeholder instead of a bare ``[cleared]``: the model is told
  what was dropped and how to get it back (persisted command output path, or
  "re-run read_file with the same offset"). Clearing stays cheap and bounded,
  but it no longer reads as silent amnesia.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain.agents.middleware import ClearToolUsesEdit
from langchain_core.messages import AIMessage, ToolMessage

# Total char budget a truncated tool result should stay near. Guard S0/S3 reuse
# this as the elision budget (same magnitude as the historical
# TOOL_RESULT_KEEP_CHARS; semantics changed from "keep first N chars" to
# "keep ~N chars spread across head + tail").
TOOL_TRUNCATION_BUDGET_CHARS = 2_000
# Elide the tail share of that budget (head = budget - tail).
TOOL_TRUNCATION_TAIL_CHARS = 800

_SPILL_RE = re.compile(r"full output saved to:\s*([^\s\]\]]+)")


def _try_json_dict(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(text)
    except Exception:  # noqa: BLE001 - content is arbitrary tool text
        return None
    return obj if isinstance(obj, dict) else None


def elide_text(text: str, keep_head: int, keep_tail: int) -> str:
    """Middle-elide ``text`` keeping ``keep_head`` + ``keep_tail`` chars.

    Returns the input unchanged when it already fits. The omission marker
    carries the number of dropped characters so the model can reason about how
    much was lost (codex middle-truncation style).
    """
    if not isinstance(text, str) or not text:
        return text
    head = max(0, int(keep_head))
    tail = max(0, int(keep_tail))
    if head + tail <= 0:
        return ""
    if len(text) <= head + tail:
        return text
    marker = f"\n…[{len(text) - head - tail} chars omitted]…\n"
    if head <= 0:
        return marker + text[-tail:]
    if tail <= 0:
        return text[:head] + marker
    return text[:head] + marker + text[-tail:]


def truncate_tool_content(content: str, budget_chars: int = TOOL_TRUNCATION_BUDGET_CHARS) -> str:
    """Truncate a tool-result string, preserving JSON structure + tail pointers.

    Dict results: large text fields (``content``/``stdout``/``stderr``/…) are
    elided head+tail; all other keys — read_file's ``next_offset``/``hint``,
    run_command's ``stdout_truncated``, ``return_code`` — survive verbatim so
    the message stays parseable and the recovery pointer at the payload's end
    is never cut off. Non-JSON text falls back to whole-content head+tail.
    Returns ``content`` unchanged when it already fits.
    """
    if not isinstance(content, str) or not content:
        return content
    budget = max(256, int(budget_chars or TOOL_TRUNCATION_BUDGET_CHARS))
    if len(content) <= budget:
        return content

    obj = _try_json_dict(content)
    if obj is None:
        return elide_text(content, budget - TOOL_TRUNCATION_TAIL_CHARS, TOOL_TRUNCATION_TAIL_CHARS)

    head = max(64, budget - TOOL_TRUNCATION_TAIL_CHARS)
    tail = TOOL_TRUNCATION_TAIL_CHARS
    # Repeatedly elide the single largest text field until the serialized body
    # is comfortably bounded. Structural keys are never touched, so the loop
    # converges and the output is always valid JSON.
    for _ in range(8):
        serialized = json.dumps(obj, ensure_ascii=False)
        if len(serialized) <= int(budget * 1.25):
            break
        key, value, best = None, None, 0
        for k, v in obj.items():
            if isinstance(v, str) and len(v) > best:
                key, value, best = k, v, len(v)
        if key is None or best <= head + tail:
            break
        replaced = elide_text(value, head, tail)
        if replaced == value:
            break
        obj[key] = replaced
    # Referenced-session payloads (read_session) carry the transcript under a
    # nested list, not a top-level string field — elide the MIDDLE of that list
    # (keeping oldest/newest ends + an omitted-marker) so the guard can still
    # shrink an oversized page without breaking the JSON or losing the paging
    # pointers (next_offset lives at the top level and is preserved).
    for _ in range(6):
        serialized = json.dumps(obj, ensure_ascii=False)
        if len(serialized) <= int(budget * 1.5):
            break
        items = obj.get("messages")
        if not isinstance(items, list) or len(items) <= 6:
            break
        keep = max(2, min(int(len(items) * 0.25), 6))
        removed = len(items) - keep * 2
        if removed <= 0:
            break
        obj["messages"] = (
            list(items[:keep])
            + [{"role": "…", "content": f"[{removed} messages omitted — re-call read_session with a next_offset to page through them]"}]
            + list(items[-keep:])
        )
    return json.dumps(obj, ensure_ascii=False)


def _spill_path(content: Any) -> str | None:
    """Find the persisted-output path in a tool result (dict or raw text)."""
    if isinstance(content, str):
        m = _SPILL_RE.search(content)
        if m:
            return m.group(1)
        obj = _try_json_dict(content)
        if obj is not None:
            for field in ("stdout", "stderr"):
                value = obj.get(field)
                if isinstance(value, str):
                    m = _SPILL_RE.search(value)
                    if m:
                        return m.group(1)
    elif isinstance(content, dict):
        for field in ("stdout", "stderr"):
            value = content.get(field)
            if isinstance(value, str):
                m = _SPILL_RE.search(value)
                if m:
                    return m.group(1)
    return None


def recovery_placeholder(tool_name: str, content: Any) -> str:
    """Recovery-aware placeholder for a cleared tool result.

    The model learns WHAT was cleared and HOW to get it back instead of a bare
    ``[cleared]``: persisted run_command output keeps its on-disk path,
    read_file results point back at a re-read of the same page.
    """
    name = tool_name or ""
    spill = _spill_path(content)
    if spill:
        return (
            "[command output cleared to save context — full output is still "
            f"persisted at: {spill}; read it there if you still need it]"
        )
    if name == "read_file":
        return (
            "[read_file output cleared to save context — call read_file again "
            "with the same file_path/offset to re-view that page]"
        )
    return "[tool result cleared to save context — re-run the tool if you still need its output]"


class RecoveryToolClearEdit(ClearToolUsesEdit):
    """ClearToolUsesEdit whose placeholder is per-message recovery guidance.

    Mirrors LangChain's clear-tool-uses semantics (trigger / keep / exclusion /
    AI-Tool pairing / idempotency via ``context_editing.cleared``) and only
    changes the placeholder text: computed from the tool name + content so a
    cleared result is never a dead end.
    """

    def apply(self, messages: list[Any], *, count_tokens: Any) -> None:  # type: ignore[override]
        """Clear stale tool outputs with recovery placeholders."""
        tokens = count_tokens(messages)
        if tokens <= self.trigger:
            return

        candidates = [(idx, msg) for idx, msg in enumerate(messages) if isinstance(msg, ToolMessage)]
        if self.keep >= len(candidates):
            candidates = []
        elif self.keep:
            candidates = candidates[: -self.keep]

        excluded_tools = set(self.exclude_tools)
        for idx, tool_message in candidates:
            if tool_message.response_metadata.get("context_editing", {}).get("cleared"):
                continue
            ai_message = next((m for m in reversed(messages[:idx]) if isinstance(m, AIMessage)), None)
            if ai_message is None:
                continue
            tool_call = next(
                (call for call in ai_message.tool_calls if call.get("id") == tool_message.tool_call_id),
                None,
            )
            if tool_call is None:
                continue
            tool_name = tool_message.name or tool_call.get("name") or ""
            if tool_name in excluded_tools:
                continue
            content = getattr(tool_message, "content", None)
            placeholder = (
                recovery_placeholder(tool_name, content)
                if content is not None
                else self.placeholder
            )
            messages[idx] = tool_message.model_copy(
                update={
                    "artifact": None,
                    "content": placeholder,
                    "response_metadata": {
                        **tool_message.response_metadata,
                        "context_editing": {
                            "cleared": True,
                            "strategy": "recovery_tool_clear",
                        },
                    },
                }
            )

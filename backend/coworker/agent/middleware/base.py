"""Shared constants, helpers, and utilities for all middleware modules.

This module is the foundation — every other middleware module imports from here.
"""

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from ...goal_prompts import is_degenerate_text
from ..core import (
    AskUserOption,
    Language,
    CoworkerAgentState,
    _estimate_tokens,
    _msg_chars,
    _msg_tokens,
    context_budget_chars,
    context_budget_tokens,
)

from ...logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Compaction / summarization constants
# ---------------------------------------------------------------------------

KEEP_RECENT_TOKENS = 8_000
# Summary output cap (opencode SUMMARY_OUTPUT_TOKENS=4096): the compacted summary
# must stay small so repeated anchored compactions do not bloat the resident set.
SUMMARY_OUTPUT_TOKENS = 4_096
# Serialized summarizer input budget. Tool results are truncated to
# TOOL_OUTPUT_MAX_CHARS before formatting; if the serialized head still exceeds
# this, the oldest messages are dropped until it fits (opencode feeds the full
# head subject to the summarizer's own context window). 20k aligns with codex
# COMPACT_USER_MESSAGE_MAX_TOKENS (C2).
SUMMARY_INPUT_MAX_TOKENS = 20_000
# Tool output truncation length when serializing messages for the summary
# (opencode TOOL_OUTPUT_MAX_CHARS=2000).
TOOL_OUTPUT_MAX_CHARS = 2_000


# ---------------------------------------------------------------------------
# Summary validation
# ---------------------------------------------------------------------------

def _summary_ok(text: str) -> bool:
    """Reject degenerate summaries before they are injected into the context.

    Guards against the observed failure mode where the summarizer was fed a
    numeric transcript (character counts) and "summarized" it into a wall of
    numbers. A real summary must contain substantive language.
    """
    if not text:
        return False
    t = text.strip()
    if len(t) < 20:
        return False
    if "error generating summary" in t.lower():
        return False
    letters = sum(1 for ch in t if ch.isalpha())
    return letters >= max(10, int(len(t) * 0.2))


# ---------------------------------------------------------------------------
# Compaction prompts
# ---------------------------------------------------------------------------

# Structured compaction prompt (SESSION INTENT / SUMMARY / ARTIFACTS / NEXT STEPS
# skeleton — same sections LangChain's SummarizationMiddleware uses, localized to
# the session language). The ``<messages>`` marker + ``{messages}`` placeholder
# are part of the framework contract (get_buffer_string feeds the transcript).
COMPACTION_PROMPTS: dict[str, str] = {
    "zh": (
        "你的任务是从下面的会话历史中提炼出最关键的信息，生成一份紧凑的摘要，"
        "用它替换掉这段旧历史，以便在有限上下文窗口内继续当前任务。\n\n"
        "只保留对继续当前目标仍然重要的内容，不要重复已经完成的操作。"
        "请按以下小节组织摘要，每一节都填入相关信息；若无相关内容请写「无」：\n\n"
        "## 会话意图\n"
        "用户的总体目标/诉求是什么？本次会话要完成什么任务？"
        "（简洁但完整到足以理解整个会话的目的）\n\n"
        "## 摘要\n"
        "记录对话中最重要的上下文：关键结论、已做的决策及其理由、"
        "讨论过的被否决方案及否决原因。\n\n"
        "## 产物\n"
        "本次会话创建/修改/访问了哪些文件或资源？对文件修改，列出具体路径并简述改动。"
        "此节用于防止产物信息静默丢失。\n\n"
        "## 后续步骤\n"
        "要达成会话意图还需要完成哪些具体任务？下一步应该做什么？\n\n"
        "只输出提取出的上下文本身，不要输出任何额外说明或前后缀文本。\n\n"
        "<messages>\n需要总结的消息：\n{messages}\n</messages>"
    ),
    "en": (
        "Your task is to extract the most important information from the "
        "conversation history below and produce a compact summary that replaces "
        "it, so work can continue within the context window.\n\n"
        "Keep only what still matters for the current goal; do not repeat work "
        "already completed. Structure the summary with the following sections — "
        "populate each with relevant info or write 'None':\n\n"
        "## SESSION INTENT\n"
        "What is the user's overall goal or request? What task is this session "
        "trying to accomplish? (Concise but complete enough to understand the "
        "purpose of the whole session.)\n\n"
        "## SUMMARY\n"
        "Record the most important context: key conclusions, decisions made and "
        "their rationale, rejected options and why they were not pursued.\n\n"
        "## ARTIFACTS\n"
        "What files or resources were created/modified/accessed in this session? "
        "For file changes, list the specific paths and briefly describe the "
        "changes. This prevents silent loss of artifact information.\n\n"
        "## NEXT STEPS\n"
        "What specific tasks remain to achieve the session intent? What should "
        "be done next?\n\n"
        "Respond ONLY with the extracted context, with no extra text before or "
        "after it.\n\n"
        "<messages>\nMessages to summarize:\n{messages}\n</messages>"
    ),
}


def _compaction_summary_prefix(language: Language) -> str:
    return "先前对话摘要：" if language == "zh" else "[Earlier conversation summary] "


# Anchored-update preamble prepended to the compaction prompt when a previous
# summary exists. Instructs the model to UPDATE (not rewrite) so repeated
# compactions stay small instead of re-summarizing overlapping history (mirrors
# opencode's buildPrompt "Update the anchored summary below ...").
_ANCHORED_PREAMBLES: dict[str, str] = {
    "zh": (
        "以下是本会话上一次压缩时生成的摘要。请基于它更新这份摘要："
        "保留仍然成立的内容，删除已过时的内容，并把下面新对话中出现的新的关键信息并入其中。"
        "保持整体紧凑，不要重复摘要中已有的内容。\n\n"
        "<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
    ),
    "en": (
        "Update the anchored summary below using the conversation history that "
        "follows. Preserve still-true details, remove stale details, and merge "
        "in the new facts. Keep it terse; do not repeat what is already in the "
        "anchored summary.\n\n"
        "<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
    ),
}


def _anchored_summary_prompt(base_prompt: str, previous_summary: str) -> str:
    """Return the compaction prompt, prefixed with the anchored-update
    instructions when a previous summary exists."""
    if not previous_summary or not previous_summary.strip():
        return base_prompt
    preamble = _ANCHORED_PREAMBLES.get(
        "zh" if "会话意图" in base_prompt else "en",
        _ANCHORED_PREAMBLES["en"],
    )
    return preamble.format(previous_summary=previous_summary.strip()) + base_prompt


def _cap_summary(text: str) -> str:
    """Hard-cap a summary to ``SUMMARY_OUTPUT_TOKENS`` (CJK-aware) so a
    degenerate long output can never bloat the compacted resident set.

    Guarantees the cap even when the summarizer model ignores ``max_tokens``.
    """
    if not text:
        return text
    if _estimate_tokens(text) <= SUMMARY_OUTPUT_TOKENS:
        return text
    marker = "\n[summary truncated by Coworker to fit context]"
    budget = max(1, SUMMARY_OUTPUT_TOKENS - _estimate_tokens(marker))
    # Trim trailing characters until the estimate fits. CJK is dense (~0.6
    # tokens/char), so walk in small steps to avoid over-trimming.
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if _estimate_tokens(text[:mid]) <= budget:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip() + marker


_COMPACTION_FLUSH: dict[str, str] = {
    "zh": (
        "注意：为保持上下文紧凑，这段对话中最早的部分已被压缩成上面的摘要。"
        "如果其中仍有对未来会话重要的持久事实，请现在通过记忆工具将其保存。"
    ),
    "en": (
        "Note: the oldest part of this conversation was summarized above to keep "
        "the context compact. If any durable fact in it still matters for future "
        "sessions, persist it via the memory tool now."
    ),
}


# ---------------------------------------------------------------------------
# JSON-safe serialization helpers (used by HITL / MCP)
# ---------------------------------------------------------------------------

def _json_safe(value: Any) -> Any:
    if isinstance(value, AskUserOption):
        return value.model_dump()
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _mcp_context(policy: dict[str, Any] | None) -> dict[str, Any]:
    """JSON-safe MCP descriptor stored on the approval record.

    ``digest`` is what "always allow" writes to the approval allowlist, so it
    has to survive the round-trip through the store.
    """
    if not policy:
        return {}
    return {
        "mcp": {
            "server_id": str(policy.get("server_id") or ""),
            "server_name": str(policy.get("server_name") or ""),
            "remote_name": str(policy.get("remote_name") or ""),
            "digest": str(policy.get("digest") or ""),
            "read_only": bool(policy.get("read_only")),
            "trusted": bool(policy.get("trusted")),
            "annotations": _json_safe(policy.get("annotations") or {}),
        }
    }


# ---------------------------------------------------------------------------
# Compaction echo stripping
# ---------------------------------------------------------------------------

def _strip_compaction_echo(content: str, summary: str) -> str:
    """Remove a model's verbatim echo of the injected compaction summary.

    Local models sometimes "continue" the injected summary HumanMessage as part
    of their answer (the observed failure mode). The summary body is exact, so a
    targeted replacement cleans the persisted/displayed reply without touching
    legitimate content.
    """
    if not content or not summary:
        return content
    s = summary.strip()
    if len(s) < 20:
        return content
    if s in content:
        return content.replace(s, "").strip()
    return content


# ---------------------------------------------------------------------------
# Degenerate text detection (used by RepeatedToolCallMiddleware)
# ---------------------------------------------------------------------------

def _is_degenerate_text(content: str, min_repeat: int = 5) -> bool:
    """True when a single message repeats one unit several times — the
    qwen3 greedy-decoding collapse (e.g. '讓我搜索一下...' × 40)."""
    return is_degenerate_text(content, min_repeat=min_repeat)


# ---------------------------------------------------------------------------
# Token / char counters (re-exported for convenience)
# ---------------------------------------------------------------------------

def cjk_token_counter(messages: Iterable[Any]) -> int:
    """CJK-aware batch token counter used by trim/cutoff logic."""
    return sum(_msg_tokens(m) for m in messages)

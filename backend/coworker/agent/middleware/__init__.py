"""Agent middleware — modular package.

Re-exports everything that was previously available from
``coworker.agent.middleware`` so that downstream code (graph.py, runtime.py,
worker_summarize.py, selftest.py) continues to work without import changes.
"""

# HITL / approval layer
from .hitl import (
    _DynamicInterruptOn,
    _mcp_context,
    command_approval_middleware,
    interrupt_action_kind,
    interrupt_action_requests,
    interrupt_command_details,
    interrupt_id,
    interrupt_payload,
    mcp_policy_resolver,
    record_runtime_interrupts,
    stream_event_from_interrupt,
)

# Message normalization & steer injection
from .message_processor import NormalizeMessagesMiddleware, SteerInjectionMiddleware

# Context compaction / summarization
from .context_compaction import CoworkerSummarizationMiddleware, _summarizer_candidates

# Context guard (final line of defense)
from .context_guard import ContextGuardMiddleware, ContextOverflowError

# Loop protection
from .loop_guard import (
    IdleLoopMiddleware,
    RepeatedToolCallMiddleware,
    StallRetryMiddleware,
    ToolCallCleanerMiddleware,
)

# Phase-gated tool selection
from .phase_gate import PhaseToolGateMiddleware

# Single system-prompt assembler (fragment model, codex-style)
from .system_assembler import SystemAssembler, SYSTEM_FIXED_BUDGET_TOKENS

# Shared constants & utilities (re-exported for backward compatibility)
from .base import (
    COMPACTION_PROMPTS,
    _COMPACTION_FLUSH,
    KEEP_RECENT_TOKENS,
    SUMMARY_INPUT_MAX_TOKENS,
    SUMMARY_OUTPUT_TOKENS,
    TOOL_OUTPUT_MAX_CHARS,
    _anchored_summary_prompt,
    _cap_summary,
    _compaction_summary_prefix,
    _json_safe,
    _strip_compaction_echo,
    _summary_ok,
    cjk_token_counter,
)

__all__ = [
    # HITL
    "_DynamicInterruptOn",
    "_mcp_context",
    "command_approval_middleware",
    "interrupt_action_kind",
    "interrupt_action_requests",
    "interrupt_command_details",
    "interrupt_id",
    "interrupt_payload",
    "mcp_policy_resolver",
    "record_runtime_interrupts",
    "stream_event_from_interrupt",
    # Message
    "NormalizeMessagesMiddleware",
    "SteerInjectionMiddleware",
    # Compaction
    "CoworkerSummarizationMiddleware",
    "_summarizer_candidates",
    # Guard
    "ContextGuardMiddleware",
    "ContextOverflowError",
    # Loop
    "IdleLoopMiddleware",
    "RepeatedToolCallMiddleware",
    "StallRetryMiddleware",
    "ToolCallCleanerMiddleware",
    # Phase
    "PhaseToolGateMiddleware",
    # Assembler
    "SystemAssembler",
    "SYSTEM_FIXED_BUDGET_TOKENS",
    # Constants / utilities
    "COMPACTION_PROMPTS",
    "_COMPACTION_FLUSH",
    "KEEP_RECENT_TOKENS",
    "SUMMARY_INPUT_MAX_TOKENS",
    "SUMMARY_OUTPUT_TOKENS",
    "TOOL_OUTPUT_MAX_CHARS",
    "_anchored_summary_prompt",
    "_cap_summary",
    "_compaction_summary_prefix",
    "_json_safe",
    "_strip_compaction_echo",
    "_summary_ok",
    "cjk_token_counter",
]

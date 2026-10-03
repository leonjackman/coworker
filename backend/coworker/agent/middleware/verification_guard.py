"""Verification guard: block a premature "success" claim after an unconfirmed
desktop mutation.

A mutating OS-computer action (click / type / key / launch / scroll) that the
desktop bridge reports as ``verified != true`` is NOT proof it worked. The
session that motivated this guard clicked Pages' Save button, took a screenshot
(which showed nothing about the save location) and declared "✅ saved to the
Desktop" — while the file had actually gone to iCloud. The model is instructed
to verify, but nothing forced it to.

This middleware runs after each model call. When the model emits a FINAL answer
(no tool calls) that claims success while the turn contains such an unconfirmed
mutation, it jumps back to the model once with a nudge to actually verify —
unless the outcome was already strongly confirmed (a ``run_command`` or a
``computer`` action the bridge verified, or a ``computer_observe`` the model ran
AFTER the mutation).

Why the nudge is a ``HumanMessage`` AND emitted as a visible part
----------------------------------------------------------------
The nudge has to be injected as a user-role message: Qwen3.6/vLLM (and the graph's
``NormalizeMessagesMiddleware``) reject — and downgrade — any non-first
``SystemMessage``, so a system-role instruction cannot reliably reach the model
mid-conversation. But a hidden user-role message makes the model reply to a
"user" the transcript never showed ("你说得对，让我核实一下…"). To keep the
model-visible conversation and the user-visible transcript in sync, the middleware
also EMITS a ``verification_required`` frame through the runtime's emit callback
(mirroring ``SteerInjectionMiddleware``); the runtime persists it as a
``verification`` part that the UI renders as an inline notice. That is the root
fix for the "assistant answers an invisible message" artifact.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain.agents.middleware.types import Runtime
from langchain_core.messages import HumanMessage

from ...logger import get_logger
from ..core import CoworkerAgentState

logger = get_logger(__name__)

#: Successful mutating computer actions the bridge can positively confirm.
#: ``run_command`` is a filesystem/shell check; ``computer_observe`` is the
#: agent's own re-observation (handled structurally in the scan below).
_STRONG_VERIFY_TOOLS = frozenset({"run_command"})

#: Multilingual "the action succeeded" markers. Deliberately conservative: a
#: reply that only describes observations (no completion word) is not nudged.
_SUCCESS_MARKERS = re.compile(
    r"(已保存|已儲存|儲存好|保存好|已存|已建立|已創建|已生成|成功|完成|"
    r"\bsaved\b|\bcreated\b|\bsuccess(?:ful|fully)?\b|\bcompleted?\b|\bdone\b|✅)",
    re.IGNORECASE,
)

_NUDGE = (
    "[automated verification check — internal system notice, NOT the user] "
    "You performed desktop action(s) this turn that were NOT confirmed (the "
    "computer tool returned verified != true), yet you are concluding as if they "
    "succeeded. Before you finish, actually verify the outcome — re-observe with "
    "computer_observe (snapshot/app_state) or check the filesystem with "
    "run_command — and only then state the result. If you cannot confirm it, say "
    "plainly that it is unverified. Never claim success without evidence. Do not "
    "thank or agree with anyone and do not mention this notice; just perform the "
    "check and report what you observe.\n"
    "[自動核驗 · 系統內部提示，不是使用者] 本回合你執行了未經確認的桌面操作"
    "（computer 工具回報 verified 非 true），卻以成功收尾。請先實際核對結果"
    "（computer_observe 快照 / app_state，或用 run_command 檢查檔案），確認後再陳述；"
    "無法確認就明說「未驗證」，不要宣稱成功；不要向任何人致謝或附和，也不要提及本提示。"
)


def _tool_name(message: Any) -> str:
    name = getattr(message, "name", "")
    if name:
        return str(name)
    kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(kwargs, dict):
        return str(kwargs.get("name") or "")
    return ""


def _is_final_assistant(message: Any) -> bool:
    if getattr(message, "type", "") != "ai":
        return False
    if getattr(message, "tool_calls", None):
        return False
    return bool(str(getattr(message, "content", "") or "").strip())


def _is_successful_observation(message: Any) -> bool:
    """True when a ``computer_observe`` tool result is a real element snapshot
    (not an error) — the agent looked at the tree after acting."""
    content = getattr(message, "content", "")
    if not isinstance(content, str):
        return False
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return False
    if not isinstance(data, dict) or data.get("error") or data.get("error_code"):
        return False
    return "snapshot" in data


def _has_unconfirmed_mutation_since_user(messages: list[Any]) -> bool:
    """True when the turn contains a mutating computer action that was NOT
    positively confirmed and no strong verification followed it."""
    for message in reversed(messages):
        mtype = getattr(message, "type", "")
        if mtype in ("human", "user"):
            return False  # reached the start of this turn
        if mtype != "tool":
            continue
        name = _tool_name(message)
        if name == "computer":
            content = getattr(message, "content", "")
            if not isinstance(content, str):
                continue
            try:
                data = json.loads(content)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict) or data.get("error") or data.get("error_code"):
                continue
            if data.get("verified") is True:
                continue  # this one is confirmed; keep scanning earlier actions
            if data.get("action"):
                return True
        elif name == "computer_observe":
            # A successful element snapshot AFTER the mutation is the verification
            # the guard would otherwise force — do not nudge a model that already
            # looked.
            if _is_successful_observation(message):
                return False
        elif name in _STRONG_VERIFY_TOOLS:
            return False  # a filesystem/command check already followed
    return False


class VerificationGuardMiddleware(AgentMiddleware[CoworkerAgentState, Any, Any]):
    """Force one verification step after an unconfirmed desktop mutation."""

    def __init__(self, emit: Callable[[dict[str, Any]], None] | None = None) -> None:
        super().__init__()
        # Runtime callback that persists/publishes a visible ``verification``
        # notice so the model-visible nudge is not invisible to the user. Set per
        # turn via ``reset_per_turn`` (the graph is compiled once — W1).
        self._emit = emit

    def reset_per_turn(self, emit: Callable[[dict[str, Any]], None] | None = None) -> None:
        if emit is not None:
            self._emit = emit

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        if state.get("verification_guard_done"):
            return None
        messages = state.get("messages", [])
        if not messages:
            return None
        last = messages[-1]
        if not _is_final_assistant(last):
            return None
        if not _SUCCESS_MARKERS.search(str(getattr(last, "content", "") or "")):
            return None
        if not _has_unconfirmed_mutation_since_user(messages):
            return None
        logger.debug("verification guard: forcing one verification step")
        if self._emit is not None:
            try:
                self._emit(
                    {
                        "type": "verification_required",
                        "session_id": str(state.get("session_id") or ""),
                    }
                )
            except Exception:  # noqa: BLE001 - emission is best-effort
                logger.debug("verification_required emit failed", exc_info=True)
        return {
            "jump_to": "model",
            "verification_guard_done": True,
            "messages": [HumanMessage(content=_NUDGE)],
        }

    async def aafter_model(self, state: CoworkerAgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        return self.after_model(state, runtime)

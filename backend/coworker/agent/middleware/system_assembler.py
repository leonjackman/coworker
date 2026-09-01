"""SystemAssembler: single point that composes the FULL system prompt.

Codex-style fragment assembly (cf. codex ``core/src/context`` + BaseInstructions):
every dynamic block is a small, individually-bounded fragment, and the assembler
orders them by priority under a TOTAL fixed budget. This replaces the old chain
where PhaseToolGate / SkillMiddleware / MemoryMiddleware each OVERRODE
``system_message`` on every model call (B1: the growing prompt was copied and
re-concatenated several times per request).

Order (behaviour first, dynamic last — B2):
    base behaviour -> phase block -> capabilities -> MCP attribution
    -> workspace -> memory index -> skills catalog (+activated bodies)

Fragments:
  * ``base``: the graph-level behaviour prompt.
  * ``phase_block``: phase/autonomy behavioural contract (discuss vs execute).
  * ``capabilities``: platform / web / browser capability notes.
  * ``mcp``: MCP server attribution (which tools belong to which server) — the
    MCP middleware no longer PREPENDS this above the behaviour core; it is a
    regular fragment placed after capabilities (codex/openqueue put
    instructions before MCP), hidden in the read-only ``discuss`` phase.
  * ``workspace``: project layout tree — TURN-CACHED (P3), never re-walked on
    every model call while the top-level tree is unchanged.
  * ``memory index``: token-budgeted resident index (already fingerprint-cached
    by MemoryManager, M2); full files load on demand via ``memory_read``.
  * ``skills``: bounded catalog + explicitly activated bodies (P4), hidden in
    the read-only ``discuss`` phase.

Total budget (V5): if the composed text exceeds ``SYSTEM_FIXED_BUDGET_TOKENS``
the lowest-priority fragments are dropped (skills, then workspace) so the fixed
overhead can never stack into a context bomb; the behaviour core and the phase
contract are never dropped.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from ...logger import get_logger
from ..core import normalize_autonomy, normalize_phase
from ..prompts import phase_system_prompt
from ..system_prompt import build_workspace_context
from ...skills.skill_middleware import _phase_is_discuss, build_skill_section

logger = get_logger(__name__)

# Hard ceiling for the composed fixed system prompt (behaviour + phase +
# capabilities + workspace + memory + skills). Individual fragments are already
# bounded; this guarantees the SUM cannot blow the window. Never reached in
# practice (typical compose ≈ 4-6k tokens), purely a safety net.
SYSTEM_FIXED_BUDGET_TOKENS = 16_000


def _system_text(msg: Any) -> str:
    """Extract the text of a system message, tolerating list content.

    LangChain ``SystemMessage.content`` is usually a string, but multimodal /
    plugin paths can set a list of parts. Reading only ``.text`` would return ''
    there and silently drop the behaviour core (B1 edge).
    """
    try:
        content = msg.content
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text:
                    out.append(text)
            elif isinstance(item, str) and item:
                out.append(item)
        return "\n".join(out)
    return str(content)


class SystemAssembler(AgentMiddleware):

    def __init__(
        self,
        *,
        capabilities: str = "",
        workspace: Any | None = None,
        memory_manager: Any | None = None,
        skill_manager: Any | None = None,
        mcp_summary_provider: Callable[[], str | None] | None = None,
        chat_mode: bool = False,
    ):
        self.capabilities = capabilities
        self.workspace = workspace
        self.memory_manager = memory_manager
        self.skill_manager = skill_manager
        self.mcp_summary_provider = mcp_summary_provider
        # 聊天项目（__chat__）：替换 phase 片段为聊天契约，去掉"可修改文件/运行
        # 命令/write_todos"的编码执行指令（base 已由 build_cw_chat_system_prompt 接管）。
        self.chat_mode = chat_mode
        # P3: workspace layout is turn-cached per phase; invalidated when the
        # root dir mtime changes (top-level files/dirs added or removed).
        self._ws_cache: dict[tuple[Any, float], str] = {}
        # Per-turn memo of SKILL.md body reads (shared with build_skill_section).
        self._skill_body_cache: dict[tuple[str, str], tuple[str, str] | None] = {}

    def reset_per_turn(self) -> None:
        """W1 (compile-cache prerequisite): clear per-turn fragment caches so a
        reused assembler never serves last turn's workspace/skill reads."""
        self._ws_cache.clear()
        self._skill_body_cache.clear()

    # -- fragment providers ---------------------------------------------------

    def _workspace_section(self, phase: Any) -> str:
        if self.workspace is None:
            return ""
        try:
            mtime = self.workspace.root.stat().st_mtime_ns
        except Exception:  # noqa: BLE001 - a stat failure just disables caching
            mtime = 0
        try:
            if (phase, mtime) in self._ws_cache:
                return self._ws_cache[(phase, mtime)]
            ctx = build_workspace_context(self.workspace)
            self._ws_cache[(phase, mtime)] = ctx
            return ctx
        except Exception as exc:  # noqa: BLE001 - a tree walk must never break chat
            logger.warning("workspace tree unavailable: %s", exc)
            return ""

    def _memory_section(self) -> str:
        if self.memory_manager is None:
            return ""
        try:
            if getattr(self.memory_manager, "bound_project", None):
                return self.memory_manager.render_for(
                    self.memory_manager.bound_project, self.memory_manager.bound_agent
                )
            return self.memory_manager.render_prompt()
        except Exception as exc:  # noqa: BLE001 - a memory load must never break chat
            logger.warning("memory load failed: %s", exc)
            return ""

    def _skills_section(self, messages: list[Any], is_discuss: bool) -> str:
        if self.skill_manager is None or is_discuss:
            return ""
        return build_skill_section(self.skill_manager, messages, self._skill_body_cache)

    def _mcp_section(self, is_discuss: bool) -> str:
        if self.mcp_summary_provider is None or is_discuss:
            return ""
        try:
            summary = self.mcp_summary_provider()
        except Exception:  # noqa: BLE001 - an MCP summary hiccup must never break chat
            return ""
        if not summary:
            return ""
        return (
            "## MCP 服务与工具归属 / MCP Server Attribution\n\n"
            "以下工具来自已连接的 MCP 服务（按服务器分组）。"
            "When identifying tools, tools listed below belong to the named MCP server.\n\n"
            f"{summary}\n\n"
            "If asked which tools belong to MCP servers, use this section as your reference."
        )

    # -- assembly --------------------------------------------------------------

    def _overrides(self, request: Any) -> dict[str, Any]:
        state = request.state
        language = state.get("language", "zh")
        phase = normalize_phase(state.get("phase"), state.get("work_mode"))
        autonomy = normalize_autonomy(state.get("autonomy"))

        base = ""
        try:
            base_sys = getattr(request, "system_message", None)
            if base_sys is not None:
                base = _system_text(base_sys)
        except Exception:  # noqa: BLE001 - never break on a missing base prompt
            base = ""

        # 聊天项目：phase 片段退化为"语言跟随 + 聊天语气"，不注入编码执行契约。
        if self.chat_mode:
            phase_frag = (
                "Reply in the same language as the user's message. "
                "Also keep ALL intermediate narration and step-by-step commentary in that "
                "same language — never switch to another language mid-turn.\n"
                "这是一个轻松的聊天对话。保持自然随和，直接凭知识回答；"
                "除非用户明确要求，否则不要修改文件、运行命令或调用工具。"
            )
        else:
            phase_frag = phase_system_prompt(language, phase, autonomy)

        # (priority, label, text) — higher priority = kept first under budget.
        fragments: list[tuple[int, str, str]] = [
            (100, "behaviour", base),
            (90, "phase", phase_frag),
        ]
        if self.capabilities:
            fragments.append((80, "capabilities", self.capabilities))
        is_discuss = _phase_is_discuss(state)
        mcp = self._mcp_section(is_discuss)
        if mcp:
            fragments.append((75, "mcp", mcp))
        ws = self._workspace_section(phase)
        if ws:
            fragments.append((70, "workspace", ws))
        memory = self._memory_section()
        if memory:
            fragments.append((60, "memory", memory))
        messages = state.get("messages", []) or []
        skills = self._skills_section(messages, is_discuss)
        if skills:
            fragments.append((50, "skills", skills))

        fragments.sort(key=lambda f: f[0], reverse=True)
        content, budget_ok = self._compose(fragments)
        if not budget_ok:
            # V5: drop lowest-priority non-core fragments (skills, then memory,
            # workspace, ...) so the fixed overhead can never stack into a bomb.
            droppable = [f[1] for f in sorted(fragments, key=lambda f: f[0]) if f[1] not in ("behaviour", "phase")]
            for name in droppable:
                fragments = [f for f in fragments if f[1] != name]
                content, budget_ok = self._compose(fragments)
                if budget_ok:
                    logger.warning("SystemAssembler budget guard dropped fragment: %s", name)
                    break
        return {"system_message": SystemMessage(content)}

    def _compose(self, fragments: list[tuple[int, str, str]]) -> tuple[str, bool]:
        parts = [text for _, _, text in fragments if text and text.strip()]
        if not parts:
            return "", True
        content = "\n\n".join(parts)
        try:
            from coworker.context import estimate_text_tokens

            over = estimate_text_tokens(content) > SYSTEM_FIXED_BUDGET_TOKENS
        except Exception:  # noqa: BLE001 - an estimator failure never gates chat
            over = False
        return content, not over

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(request.override(**self._overrides(request)))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(request.override(**self._overrides(request)))

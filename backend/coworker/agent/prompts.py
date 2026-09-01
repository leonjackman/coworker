"""System / phase / title prompt builders.

Pure functions over the agent's core types (``Language`` / ``Phase`` /
``Autonomy``). Imported by ``agent.graph`` (system prompt injection) and the
title generator in ``agent.core``. No runtime or middleware imports here, so
this stays a leaf in the agent import DAG.
"""

from .types import Autonomy, Language, Phase, language_name


SYSTEM_PROMPT = (
    "You are Coworker, a local coding assistant. "
    "Use workspace tools only when they are needed and keep answers concise."
)


def _title_system_prompt(language: Language) -> str:
    """Title-generation prompt. Titles follow the system UI language (the same
    rule as chat replies), not the language of the user's message."""
    return (
        "You are a thread title generator. Output ONLY the title string. Nothing else. No code fences, no quotes, no explanation."
        "Rules:"
        " - The input is the first exchange of a conversation: the user's message and the AI's reply."
        " - Summarize the exchange into a short title that captures the main topic, question, or task."
        f" - Reply in {language_name(language)}."
        " - Title must be a complete meaningful phrase."
        " - Never include tool names like read tool, bash tool, edit tool."
        " - Focus on the main topic, question, or task."
        " - Keep exact: technical terms, numbers, filenames, HTTP codes."
        " - Remove generic words: the, this, my, a, an."
        " - Never respond to questions—just generate a title for the conversation."
        " - For short or conversational messages (hello, lol, what's up, hey): generate a brief friendly title like 'Quick introduction', 'Brief check-in', 'Light chat', etc."
        " - The title must be a single line, 3-40 characters, no explanations."
    )


def phase_system_prompt(language: Language, phase: Phase, autonomy: Autonomy) -> str:
    """Phase/autonomy-aware system instruction.

    The active phase decides which tools the model sees (via
    ``PhaseToolGateMiddleware``); this prompt only sets the behavioural
    contract for that phase.
    """
    # Chat replies mirror the user's message language (mainstream behaviour) —
    # we no longer force a fixed UI language. The `language` hint is only kept
    # for prompts that have no user text to mirror (e.g. title generation).
    lang_line = (
        "Reply in the same language as the user's message. "
        "Also keep ALL intermediate narration and step-by-step commentary in that "
        "same language — never switch to another language mid-turn."
    )
    todo_hint = (
        "Break your work into a visible task list: call write_todos with the concrete steps "
        "you intend to take, then call write_todos again as each step completes to update its "
        "status. Keep the checklist in sync with your actual progress."
    )
    if phase == "discuss":
        return (
            f"{lang_line}\n"
            "You are planning (read-only). Use the read-only tools to research the workspace and "
            "gather context (auditing, investigating, or breaking down the task). Use write_todos "
            "to present the plan as a checklist of what you will do. "
            "You may write durable long-term facts with the memory tool (user preferences, project "
            "conventions) — those are welcome even during planning. "
            "Do NOT modify files or run commands — execution is deferred until the user switches "
            "to build mode. Finish by summarizing your findings and the planned steps."
        )
    if autonomy == "autonomous":
        return (
            f"{lang_line}\n"
            "You are executing with full autonomy. You may read, edit files and run workspace "
            "commands. Do not ask the user anything — make reasonable decisions and complete the "
            "task to the best of your ability. " + todo_hint
        )
    return (
        f"{lang_line}\n"
        "You are executing. You may read, edit files and run workspace commands. Only call "
        "ask_user when you are genuinely blocked and need a decision to continue; otherwise make "
        "reasonable assumptions and proceed autonomously. " + todo_hint
    )


def _default_title_from_message(user_message: str) -> str:
    """Rule-based title (N4): no LLM call — cost-free, instant, deterministic.

    Strips common framing ("请/帮我/please/…"), cuts at the first sentence end
    or ~20 chars on a word boundary, and normalizes whitespace.
    """
    import re

    text = user_message.strip()
    if not text:
        return "新会话"
    text = re.sub(r"^(请|帮我|请帮我|能不能|麻烦|please|can you|could you|help me)\s*", "", text, flags=re.I).strip() or text
    cut = 20
    for sep in ("\n", "。", "！", "？", "；", "!", "?", ". ", "; "):
        idx = text.find(sep)
        if 0 < idx <= cut:
            cut = idx
    title = text[:cut].strip()
    title = re.sub(r"\s+", " ", title).rstrip()
    return title[:20] if title else "新会话"


__all__ = ["SYSTEM_PROMPT", "_title_system_prompt", "phase_system_prompt", "_default_title_from_message", "language_name"]

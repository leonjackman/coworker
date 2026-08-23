"""Core agent type aliases.

Leaf module: defines the shared Literal type aliases (and the pure
``language_name`` helper) so both ``agent.core`` and ``agent.prompts`` can
import them without a circular dependency.
"""

from typing import Literal

AgentMode = Literal["single"]
Language = Literal["zh", "en"]
WorkMode = Literal["plan", "build"]
Phase = Literal["discuss", "execute"]
Autonomy = Literal["supervised", "guarded", "autonomous"]


def language_name(language: Language) -> str:
    return "Chinese" if language == "zh" else "English"


__all__ = ["AgentMode", "Language", "WorkMode", "Phase", "Autonomy", "language_name"]

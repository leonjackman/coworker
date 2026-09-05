"""Core agent type aliases.

Leaf module: defines the shared Literal type aliases (and the pure
``language_name`` helper) so both ``agent.core`` and ``agent.prompts`` can
import them without a circular dependency.
"""

from typing import Literal

AgentMode = Literal["single"]
# UI / reply languages the app supports. Keep in sync with the frontend
# `Language` union in frontend/src/lib/i18n.ts.
Language = Literal[
    "zh",
    "en",
    "zh-TW",
    "zh-HK",
    "ja",
    "ko",
    "fr",
    "de",
    "es",
    "pt-BR",
    "ru",
]
WorkMode = Literal["plan", "build"]
Phase = Literal["discuss", "execute"]
# Two-level permission model (产品文案「默認權限/完整權限」):
#   guarded    → default permission: free inside workspace, ask at the boundary
#   autonomous → full permission: never ask (Codex bypassPermissions)
# The legacy "supervised" level was retired and folds into ``guarded``.
Autonomy = Literal["guarded", "autonomous"]

# Every value the `Language` Literal can take. Used by `normalize_language`
# to accept the full set instead of collapsing everything to zh/en.
VALID_LANGUAGES = set(Language.__args__)  # type: ignore[attr-defined]

# Human-readable English names used in prompts that need an explicit language
# hint (e.g. title generation, which has no user message to mirror).
_LANGUAGE_NAMES: dict[Language, str] = {
    "zh": "Chinese",
    "zh-TW": "Traditional Chinese",
    "zh-HK": "Traditional Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt-BR": "Portuguese",
    "ru": "Russian",
}


def language_name(language: Language) -> str:
    return _LANGUAGE_NAMES.get(language, "Chinese")


__all__ = ["AgentMode", "Language", "WorkMode", "Phase", "Autonomy", "VALID_LANGUAGES", "language_name"]

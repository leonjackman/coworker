"""The system prompt must always carry a fresh local date/time (the agent's only
reliable clock — `run_command date` is unreliable on Windows and Computer Use may
be off)."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.agent.middleware.system_assembler import SystemAssembler, time_context_fragment  # noqa: E402


class _Req:
    def __init__(self, state):
        self.state = state
        self.system_message = None

    def override(self, **kwargs):
        self.kw = kwargs
        return self


def test_time_context_fragment_language_and_content():
    year = str(datetime.now().year)
    en = time_context_fragment("en")
    assert "Current local date/time" in en and "UTC" in en and year in en
    zh = time_context_fragment("zh")
    assert "当前本机日期时间" in zh and "UTC" in zh and year in zh


def test_time_fragment_is_in_assembled_system_prompt():
    for lang in ("en", "zh"):
        asm = SystemAssembler(chat_mode=False)
        out = asm._overrides(_Req({"language": lang, "work_mode": "build", "autonomy": "guarded"}))
        content = out["system_message"].content
        assert time_context_fragment(lang) in content


def test_time_fragment_survives_budget_drop():
    """It is lowest-priority (assembled last) but must never be dropped."""
    asm = SystemAssembler(chat_mode=False)
    # Force an over-budget composition by appending a huge low-priority fragment.
    original = asm._skills_section

    def huge(messages, is_discuss):  # noqa: ANN001
        return "SKILL " * 200000

    asm.skill_manager = object()
    asm._skills_section = huge  # type: ignore[method-assign]
    try:
        out = asm._overrides(_Req({"language": "en", "work_mode": "build", "autonomy": "guarded"}))
        content = out["system_message"].content
        assert "Current local date/time" in content
    finally:
        asm._skills_section = original  # type: ignore[method-assign]

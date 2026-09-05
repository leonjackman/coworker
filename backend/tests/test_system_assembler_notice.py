"""F4: SystemAssembler budget-drop notice.

Guards the evidence-backed defect: over the fixed system-prompt budget the
assembler dropped whole fragments (skills/memory/...) silently — only a dev
log line. After the fix the model is told WHICH fragments were omitted and how
to fetch them (memory_read / load_skill).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from coworker.agent.middleware.system_assembler import (  # noqa: E402
    SystemAssembler,
    _fragment_drop_notice,
)


class FakeWorkspace:
    root = None


class FakeMemMgr:
    bound_project = None

    def render_for(self, project_dir, agent):
        return "<memory>" + ("记忆正文内容 " * 500) + "</memory>"

    def render_prompt(self):
        return "<memory>m</memory>"


class _FakeRequest:
    def __init__(self, language="zh"):
        self.state = {
            "language": language,
            "phase": "build",
            "work_mode": "build",
            "autonomy": "autonomous",
            "messages": [HumanMessage(content="hi")],
        }
        self.system_message = SystemMessage(content="You are Coworker behaviour core. ")

    def override(self, **kw):
        return kw


def _assembler():
    import coworker.agent.middleware.system_assembler as sa

    return SystemAssembler(
        capabilities="caps",
        workspace=FakeWorkspace(),
        memory_manager=FakeMemMgr(),
        skill_manager=object(),
    )


@pytest.fixture
def _stub_skills(monkeypatch):
    import coworker.agent.middleware.system_assembler as sa

    monkeypatch.setattr(
        sa, "build_skill_section", lambda manager, messages, cache: "<skills>" + ("skill body " * 600) + "</skills>"
    )


def test_drop_notice_text_routes_language():
    zh = _fragment_drop_notice("zh", ["skills", "memory"])
    assert "技能目录" in zh and "memory_read" in zh and "上下文提示" in zh
    en = _fragment_drop_notice("en", ["memory"])
    assert "memory index" in en and "context notice" in en


def test_drop_appends_model_visible_notice(monkeypatch, _stub_skills):
    import coworker.agent.middleware.system_assembler as sa

    monkeypatch.setattr(sa, "SYSTEM_FIXED_BUDGET_TOKENS", 300)
    out = _assembler()._overrides(_FakeRequest("zh"))
    text = out["system_message"].content
    assert "上下文提示" in text  # model learns context was omitted
    assert "Coworker behaviour core" in text  # behaviour never dropped
    assert "<skills>" not in text  # skills dropped to fit budget


def test_no_notice_when_under_budget(monkeypatch, _stub_skills):
    import coworker.agent.middleware.system_assembler as sa

    monkeypatch.setattr(sa, "SYSTEM_FIXED_BUDGET_TOKENS", 40_000)
    out = _assembler()._overrides(_FakeRequest("zh"))
    text = out["system_message"].content
    assert "上下文提示" not in text
    assert "<memory>" in text and "<skills>" in text

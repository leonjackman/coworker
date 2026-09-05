"""F2/F3: field-aware tail-preserving tool truncation + recovery placeholders.

Covers the evidence-backed defects:
* guard head-cuts destroyed JSON tail pointers (read_file ``next_offset`` /
  run_command persisted-output path);
* ``[cleared]`` gave the model no recovery path;
* per-call clearing used the ASCII-biased langchain counter.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402

from coworker.agent.middleware import (  # noqa: E402
    CoworkerContextEditingMiddleware,
    RecoveryToolClearEdit,
    recovery_placeholder,
    truncate_tool_content,
)


def _read_file_payload(n_lines=200):
    body = "\n".join(f"line {i} " + "x" * 40 for i in range(n_lines))
    return json.dumps(
        {
            "content": body,
            "total_lines": n_lines + 100,
            "offset": 1,
            "end_line": n_lines,
            "next_offset": n_lines + 1,
            "hint": f"(Showing lines 1-{n_lines} of {n_lines + 100}. Use offset={n_lines + 1} to continue reading.)",
        },
        ensure_ascii=False,
    )


def _run_command_payload(stdout_chars=9_000):
    return json.dumps(
        {
            "return_code": 0,
            "stdout": "x" * stdout_chars
            + "\n\n[stdout truncated; full output saved to: /ws/.coworker-tool-output/run_stdout_abc123.txt]",
            "stderr": "",
            "stdout_truncated": True,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# truncate_tool_content — structure + pointer preservation
# ---------------------------------------------------------------------------

def test_truncate_read_file_keeps_json_and_paging_pointers():
    payload = _read_file_payload()
    out = truncate_tool_content(payload)
    assert len(out) < len(payload)
    parsed = json.loads(out)  # still valid JSON
    assert parsed["next_offset"] == 201
    assert parsed["total_lines"] == 300
    assert "offset=201" in parsed["hint"]
    assert "chars omitted" in parsed["content"]


def test_truncate_run_command_keeps_persisted_output_tail():
    payload = _run_command_payload()
    out = truncate_tool_content(payload)
    parsed = json.loads(out)
    assert parsed["return_code"] == 0
    assert parsed["stdout_truncated"] is True
    assert "run_stdout_abc123.txt" in parsed["stdout"]  # tail pointer survives


def test_truncate_plain_text_fallback_head_tail():
    text = "start " + "y" * 20_000 + " end"
    out = truncate_tool_content(text)
    assert out.startswith("start ")
    assert out.rstrip().endswith("end")
    assert "chars omitted" in out
    assert len(out) < 2_500


def test_truncate_short_content_unchanged():
    assert truncate_tool_content("small result") == "small result"


# ---------------------------------------------------------------------------
# recovery_placeholder routing
# ---------------------------------------------------------------------------

def test_placeholder_routes_by_content_and_tool():
    cmd = _run_command_payload()
    assert "persisted at: /ws/.coworker-tool-output/run_stdout_abc123.txt" in recovery_placeholder(
        "run_command", cmd
    )
    assert "read_file" in recovery_placeholder("read_file", _read_file_payload())
    generic = recovery_placeholder("browser", "ordinary text")
    assert "re-run the tool" in generic


# ---------------------------------------------------------------------------
# RecoveryToolClearEdit end-to-end
# ---------------------------------------------------------------------------

def _counter(messages):
    from coworker.agent.middleware.base import cjk_token_counter

    return cjk_token_counter(messages)


def test_recovery_clear_uses_recovery_placeholders_and_preserves_exclusions():
    messages = [
        HumanMessage(content="start"),
        AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"file_path": "a.py", "offset": 1}, "id": "c1", "type": "tool_call"}]),
        ToolMessage(content=_read_file_payload(400), tool_call_id="c1"),
        AIMessage(content="", tool_calls=[{"name": "run_command", "args": {"command": "pytest"}, "id": "c2", "type": "tool_call"}]),
        ToolMessage(content=_run_command_payload(12_000), tool_call_id="c2"),
        AIMessage(content="", tool_calls=[{"name": "memory_read", "args": {"file": "x/MEMORY.md"}, "id": "c3", "type": "tool_call"}]),
        ToolMessage(content="記憶檔案的完整內容 " * 200, tool_call_id="c3", name="memory_read"),
        HumanMessage(content="continue"),
    ]
    edit = RecoveryToolClearEdit(
        trigger=0,
        keep=0,
        exclude_tools=("write_todos", "memory", "memory_read", "ask_user"),
    )
    edit.apply(messages, count_tokens=_counter)

    read_ph = messages[2].content
    cmd_ph = messages[4].content
    mem = messages[6].content
    assert "read_file" in read_ph and "coworker-tool-output" in cmd_ph
    assert cmd_ph != _run_command_payload(12_000)
    assert mem == "記憶檔案的完整內容 " * 200  # memory_read excluded


def test_recovery_clear_is_idempotent_via_metadata():
    messages = [
        HumanMessage(content="start"),
        AIMessage(content="", tool_calls=[{"name": "browser", "args": {}, "id": "c1", "type": "tool_call"}]),
        ToolMessage(content="z" * 5_000, tool_call_id="c1"),
    ]
    edit = RecoveryToolClearEdit(trigger=0, keep=0)
    edit.apply(messages, count_tokens=_counter)
    first = messages[2].content
    assert "re-run the tool" in first
    edit.apply(messages, count_tokens=_counter)  # second pass must not churn
    assert messages[2].content == first


# ---------------------------------------------------------------------------
# CJK-counted per-call editing middleware
# ---------------------------------------------------------------------------

def test_coworker_context_editing_middleware_counts_with_cjk():
    seen = []

    class FakeRequest:
        def __init__(self):
            self.messages = [
                HumanMessage(content="start"),
                AIMessage(content="", tool_calls=[{"name": "run_command", "args": {}, "id": "c1", "type": "tool_call"}]),
                ToolMessage(content="字" * 8_000, tool_call_id="c1"),
            ]

        def override(self, **kw):
            return kw

    request = FakeRequest()
    mw = CoworkerContextEditingMiddleware(edits=[RecoveryToolClearEdit(trigger=0, keep=0)])
    out = mw.wrap_model_call(request, lambda req: seen.append(req) or "ok")
    assert out == "ok"
    assert len(seen) == 1
    assert any("re-run the tool" in str(m.content) for m in seen[0]["messages"])


# ---------------------------------------------------------------------------
# Guard S3/S0 tail preservation on JSON payloads
# ---------------------------------------------------------------------------

@pytest.fixture
def guard():
    from coworker.agent.middleware import ContextGuardMiddleware

    return ContextGuardMiddleware(window_tokens=10_000, max_output_tokens=0)


def _json_tool_messages(n, payload):
    out = []
    for i in range(n):
        out.append(AIMessage(content="", tool_calls=[{"name": "read_file", "args": {}, "id": f"c{i}", "type": "tool_call"}]))
        out.append(ToolMessage(content=payload, tool_call_id=f"c{i}"))
    return out


def test_guard_s3_truncation_preserves_read_file_pointers(guard):
    payload = _read_file_payload(300)
    messages = _json_tool_messages(6, payload)
    new_messages, changed = guard._truncate_old_tool_results(messages)
    assert changed == 6
    parsed = json.loads(new_messages[1].content)
    assert parsed["next_offset"] == 301
    assert "chars omitted" in parsed["content"]


def test_guard_s0_windowing_preserves_tail_pointers(guard):
    payload = _read_file_payload(300)
    messages = [HumanMessage(content="start"), *_json_tool_messages(8, payload), HumanMessage(content="go")]
    new_messages, changed = guard._window_stale_tool_results(messages, measured=900_000, limit_tokens=1_000_000)
    assert changed == 4  # oldest 4 windowed, newest 4 intact
    tail = new_messages[-2].content  # newest read_file result stays whole
    assert json.loads(tail)["next_offset"] == 301

"""Unified context accounting + pre-send guard tests.

Guards the root-cause fix for the "meter said 130k, provider 400'd at 254k"
incident: a single calibrated measurement for every budget decision, output
reservation subtracted from the window, binary blobs scrubbed out of text
context, and a final-request guard that reduces stagedly instead of leaking
the provider's raw 400.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402

from coworker.context import (  # noqa: E402
    BASE64_CHARS_PER_TOKEN,
    CALIBRATION_BOOTSTRAP,
    CALIBRATION_MAX,
    CalibrationStore,
    effective_input_limit,
    estimate_text_tokens,
    measure_request,
    message_tokens,
    parse_overflow_actual_tokens,
    scrub_text,
)

# The real vLLM error from the incident (window 262144, output 8192).
INCIDENT_ERROR = (
    "Error code: 400 - {'error': {'message': \"This model's maximum context "
    "length is 262144 tokens. However, you requested 8192 output tokens and "
    "your prompt contains at least 253953 input tokens, for a total of at "
    "least 262145 tokens. (parameter=input_tokens, value=253953)\""
)


# ---------------------------------------------------------------------------
# Base estimator
# ---------------------------------------------------------------------------

def _real_base64(chars: int) -> str:
    import base64 as _b64
    import os

    raw = _b64.b64encode(os.urandom(chars // 4 * 3 + 16)).decode("ascii")
    return (raw * 2)[:chars]


def test_estimator_base64_runs_count_at_true_density():
    blob = _real_base64(50_000)
    est = estimate_text_tokens(blob)
    # ~1.4 chars/token, NOT the prose ~3.8 — the incident's 2.8x blind spot.
    assert est == pytest.approx(50_000 / BASE64_CHARS_PER_TOKEN, rel=0.08)
    prose = "hello world, this is a normal sentence. " * 1250  # 48k chars
    assert estimate_text_tokens(prose) < est * 0.5


def test_estimator_does_not_swallow_single_char_runs():
    # A long run of ONE repeated char is prose/ASCII-art, not base64 — it must
    # be counted at the prose rate, not scrubbed or charged at 1.4 chars/token.
    filler = "x" * 50_000
    assert estimate_text_tokens(filler) == pytest.approx(50_000 / 3.8, rel=0.1)
    out, count = scrub_text(filler)
    assert count == 0
    assert "x" * 1000 in out


def test_estimator_data_url_counted_as_blob():
    data_url = "data:image/jpeg;base64," + _real_base64(30_000)
    est = estimate_text_tokens(data_url)
    assert est > 30_000 / BASE64_CHARS_PER_TOKEN * 0.9


def test_estimator_cjk_denser_than_flat_rate():
    cjk = "测" * 1000
    assert estimate_text_tokens(cjk) == pytest.approx(600, rel=0.05)


def test_estimator_empty():
    assert estimate_text_tokens("") == 0


# ---------------------------------------------------------------------------
# Blob scrubbing
# ---------------------------------------------------------------------------

def test_scrub_text_removes_data_urls():
    text = f'see {{"image": "data:image/png;base64,{"Q" * 5000}"}} ok'
    out, count = scrub_text(text)
    assert count >= 1
    assert "Q" * 100 not in out
    assert "[binary content removed from context]" in out


def test_scrub_text_removes_bare_base64_runs():
    text = "prefix " + _real_base64(3000) + " suffix"
    out, count = scrub_text(text)
    assert count == 1
    assert "[binary content removed from context]" in out
    assert "prefix " in out and " suffix" in out


def test_scrub_text_leaves_clean_content():
    text = "perfectly normal tool result 你好"
    out, count = scrub_text(text)
    assert out == text
    assert count == 0


# ---------------------------------------------------------------------------
# Calibration store (closed loop)
# ---------------------------------------------------------------------------

def test_calibration_bootstrap():
    store = CalibrationStore(None)
    assert store.get("p::m") == CALIBRATION_BOOTSTRAP


def test_calibration_first_observation_trusted():
    store = CalibrationStore(None)
    factor = store.update("p::m", actual_tokens=2600, estimated_tokens=1300)
    assert factor == pytest.approx(2.0, rel=0.01)
    assert store.get("p::m") == pytest.approx(2.0, rel=0.01)


def test_calibration_clamped_high():
    store = CalibrationStore(None)
    factor = store.update("p::m", actual_tokens=10_000_000, estimated_tokens=100)
    assert factor == CALIBRATION_MAX


def test_calibration_never_below_one():
    store = CalibrationStore(None)
    factor = store.update("p::m", actual_tokens=10, estimated_tokens=1000)
    assert factor >= 1.0


def test_calibration_persisted_across_instances(tmp_path: Path):
    path = tmp_path / "cal.json"
    store_a = CalibrationStore(path)
    store_a.update("prov::model", actual_tokens=2000, estimated_tokens=1000)
    store_b = CalibrationStore(path)
    assert store_b.get("prov::model") == pytest.approx(2.0, rel=0.01)


def test_calibration_keys_isolated():
    store = CalibrationStore(None)
    store.update("a::m1", actual_tokens=2000, estimated_tokens=1000)
    assert store.get("a::m2") == CALIBRATION_BOOTSTRAP


# ---------------------------------------------------------------------------
# Budget math (output reservation)
# ---------------------------------------------------------------------------

def test_effective_input_limit_matches_incident():
    # 262144 - 8192 = 253952 — the exact ceiling the failed request exceeded.
    assert effective_input_limit(262_144, 8192) == 253_952


def test_budget_subtracts_max_output():
    from coworker.agent.core import CONTEXT_SAFETY_FACTOR, context_budget_tokens

    assert context_budget_tokens(262_144, 8192) == int((262_144 - 8192) * CONTEXT_SAFETY_FACTOR)
    assert context_budget_tokens(262_144, 8192) < context_budget_tokens(262_144, 0)


# ---------------------------------------------------------------------------
# Overflow error parsing (learn from the failure)
# ---------------------------------------------------------------------------

def test_parse_overflow_actual_tokens_vllm():
    assert parse_overflow_actual_tokens(INCIDENT_ERROR) == 253_953


def test_parse_overflow_actual_tokens_at_least_form():
    assert parse_overflow_actual_tokens("prompt contains at least 99999 input tokens") == 99_999


def test_parse_overflow_actual_tokens_none():
    assert parse_overflow_actual_tokens("connection refused") is None


# ---------------------------------------------------------------------------
# Message measurement (media blocks)
# ---------------------------------------------------------------------------

def test_message_tokens_counts_image_blocks():
    msg = HumanMessage(content=[
        {"type": "text", "text": "what is on this page?"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBB"}},
    ])
    # Text is tiny; the two images must dominate at their per-item cost.
    assert message_tokens(msg) >= 2_000


def test_measure_request_includes_tools_and_system():
    from langchain_core.tools import tool

    @tool
    def big_tool(query: str) -> str:
        """A tool with a deliberately long description that adds real weight to
        the tool schema on every request, so measuring it must push the total
        estimate up beyond just the message text."""
        return query

    messages = [HumanMessage(content="hi")]
    base = measure_request(messages)
    full = measure_request(messages, system_text="system " + "y" * 3000, tools=[big_tool])
    assert full > base + 800


# ---------------------------------------------------------------------------
# ContextGuardMiddleware
# ---------------------------------------------------------------------------

class FakeRequest:
    """Duck-typed ModelRequest: only what the guard touches."""

    def __init__(self, messages, tools=None, system_message=None, runtime=None):
        self.messages = messages
        self.tools = tools
        self.system_message = system_message
        self.runtime = runtime

    def override(self, **kwargs):
        return FakeRequest(
            messages=kwargs.get("messages", self.messages),
            tools=kwargs.get("tools", self.tools),
            system_message=kwargs.get("system_message", self.system_message),
            runtime=self.runtime,
        )


def _guard(window=100_000, max_output=8192, mcp_names=None):
    from coworker.agent.middleware import ContextGuardMiddleware

    return ContextGuardMiddleware(
        window_tokens=window,
        max_output_tokens=max_output,
        calibration_store=None,  # factor 1.0 → deterministic
        calibration_key="",
        mcp_tool_names_provider=(lambda: set(mcp_names)) if mcp_names else None,
    )


def _tool_msgs(n, size=9_000, prefix="tool"):
    out = []
    for i in range(n):
        out.append(AIMessage(content="", tool_calls=[{"name": "browser", "args": {"action": "snapshot"}, "id": f"c{i}"}]))
        out.append(ToolMessage(content=f"{prefix}{i} " + "z" * size, tool_call_id=f"c{i}"))
    return out


def test_guard_passes_small_request():
    guard = _guard()
    seen = []

    def handler(request):
        seen.append(request)
        return "ok"

    request = FakeRequest([HumanMessage(content="small talk")])
    assert guard.wrap_model_call(request, handler) == "ok"
    assert len(seen) == 1
    assert seen[0] is request  # untouched
    assert guard.last_steps == []


def test_guard_strips_base64_blobs_first():
    guard = _guard(window=30_000, max_output=8192)
    blob = "data:image/jpeg;base64," + "R" * 40_000
    messages = [
        HumanMessage(content="rewrite the landing page"),
        AIMessage(content="", tool_calls=[{"name": "browser", "args": {"action": "screenshot"}, "id": "c1"}]),
        ToolMessage(content=f'{{"image": "{blob}"}}', tool_call_id="c1"),
        *_tool_msgs(2, size=500),
    ]
    seen = []

    def handler(request):
        seen.append(request)
        return "ok"

    guard.wrap_model_call(FakeRequest(messages), handler)
    assert len(seen) == 1
    sent = seen[0]
    text = json.dumps([str(m.content) for m in sent.messages])
    assert "R" * 100 not in text  # blob scrubbed
    assert any(s.startswith("blobs/images") for s in guard.last_steps)


def test_guard_keeps_only_recent_images():
    # Window small enough that 4 image blocks (1200 tokens each) push the
    # request over the effective limit → S1 must degrade the stale ones.
    guard = _guard(window=12_000, max_output=8192)
    img = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "I" * 300}}
    messages = []
    for i in range(4):
        messages.append(HumanMessage(content=[{"type": "text", "text": f"look {i}"}, dict(img)]))
        messages.append(AIMessage(content=f"ok {i}"))
    messages.append(HumanMessage(content="final question"))
    seen = []

    def handler(request):
        seen.append(request)
        return "ok"

    guard.wrap_model_call(FakeRequest(messages), handler)
    sent_images = sum(
        1
        for m in seen[0].messages
        if isinstance(getattr(m, "content", None), list)
        for part in m.content
        if isinstance(part, dict) and part.get("type") == "image_url"
    )
    assert sent_images == 2  # KEEP_RECENT_IMAGES


def test_guard_caps_images_even_under_token_budget():
    # 6 in-turn screenshots fit comfortably inside the token window, yet the
    # provider's per-prompt image ceiling (e.g. vLLM --limit-mm-per-prompt.image)
    # would 400 the request — the guard must cap image count even when the
    # calibrated token measurement is under the effective limit.
    from coworker.agent.core import MAX_IMAGES_PER_PROMPT

    guard = _guard(window=200_000, max_output=8192)
    img = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "I" * 300}}
    messages = [
        HumanMessage(
            content=[{"type": "text", "text": "screenshots"}]
            + [dict(img) for _ in range(MAX_IMAGES_PER_PROMPT + 1)]
        )
    ]
    seen = []

    def handler(request):
        seen.append(request)
        return "ok"

    guard.wrap_model_call(FakeRequest(messages), handler)
    sent = seen[0]
    assert guard.last_steps == []  # never entered the token-reduction ladder
    images = sum(
        1
        for m in sent.messages
        if isinstance(getattr(m, "content", None), list)
        for part in m.content
        if isinstance(part, dict) and part.get("type") == "image_url"
    )
    assert images == MAX_IMAGES_PER_PROMPT
    note = sum(
        1
        for m in sent.messages
        if isinstance(getattr(m, "content", None), list)
        for part in m.content
        if isinstance(part, dict) and part.get("type") == "text" and "removed from context" in part.get("text", "")
    )
    assert note == 1  # the dropped image was replaced with a text note


def test_guard_raises_when_single_message_exceeds_window():
    guard = _guard(window=10_000, max_output=8192)
    from coworker.agent.middleware import ContextOverflowError

    def handler(request):  # pragma: no cover - must not run
        raise AssertionError("handler must not be called on unreducible overflow")

    request = FakeRequest([HumanMessage(content="w" * 100_000)])
    with pytest.raises(ContextOverflowError) as excinfo:
        guard.wrap_model_call(request, handler)
    assert excinfo.value.limit_tokens == 10_000 - 8192
    assert excinfo.value.measured_tokens > excinfo.value.limit_tokens


def test_guard_drops_mcp_tools_when_schemas_bloat():
    # Window small enough that the MCP tool's huge schema alone pushes the
    # request over the effective limit; with no clearable tool results or
    # blobs available, S4 (drop MCP schemas) is the only way back under.
    guard = _guard(window=12_000, max_output=8192, mcp_names={"mcp__deepwiki__ask"})
    mcp_tool = {
        "type": "function",
        "function": {
            "name": "mcp__deepwiki__ask",
            "description": "Ask the DeepWiki MCP server a question. " + "d" * 18_000,
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    }
    messages = [
        HumanMessage(content="hi"),
        AIMessage(content="", tool_calls=[{"name": "mcp__deepwiki__ask", "args": {"query": "x"}, "id": "c1"}]),
        ToolMessage(content="short reply", tool_call_id="c1"),
    ]
    seen = []

    def handler(request):
        seen.append(request)
        return "ok"

    guard.wrap_model_call(FakeRequest(messages, tools=[mcp_tool]), handler)
    assert len(seen) == 1
    sent_tools = seen[0].tools or []
    assert not any(
        isinstance(t, dict) and t.get("function", {}).get("name") == "mcp__deepwiki__ask"
        for t in sent_tools
    )
    assert any(s.startswith("drop_mcp_tools") for s in guard.last_steps)


def test_guard_clears_stale_tool_results():
    # 12 oversized tool results (~8k chars each ≈ 2.1k tokens) put the request
    # ~27k tokens over the effective limit of 21,808 — S2 must clear the stale
    # ones (keeping the newest 3) to fit.
    guard = _guard(window=30_000, max_output=8192)
    messages = [HumanMessage(content="start"), *_tool_msgs(12, size=8_000), HumanMessage(content="now continue")]
    seen = []

    def handler(request):
        seen.append(request)
        return "ok"

    guard.wrap_model_call(FakeRequest(messages), handler)
    assert len(seen) == 1
    cleared = sum(1 for m in seen[0].messages if getattr(m, "content", "") == "[cleared]")
    assert cleared > 0
    assert any(s.startswith("clear_tools") for s in guard.last_steps)


@pytest.mark.asyncio
async def test_guard_async_path():
    guard = _guard(window=30_000, max_output=8192)
    blob = "data:image/png;base64," + "S" * 40_000
    messages = [
        HumanMessage(content="check"),
        AIMessage(content="", tool_calls=[{"name": "browser", "args": {}, "id": "x"}]),
        ToolMessage(content=blob, tool_call_id="x"),
        *_tool_msgs(2, size=500),
    ]
    seen = []

    async def handler(request):
        seen.append(request)
        return "ok"

    result = await guard.awrap_model_call(FakeRequest(messages), handler)
    assert result == "ok"
    assert len(seen) == 1


def test_guard_incident_shape_regression():
    """The incident: flattened history + truncated screenshot base64 + code
    writes drifted to 253,953 real tokens while the meter showed ~130k. With
    the guard armed on the SAME window/output reservation AND the bootstrap
    calibration factor (base64 tokenizes ~2.8x denser than prose), the request
    must be reduced below the effective limit and contain no base64 at all."""
    from coworker.agent.middleware import ContextGuardMiddleware

    store = CalibrationStore(None)  # fresh → bootstrap factor applies
    guard = ContextGuardMiddleware(
        window_tokens=262_144,
        max_output_tokens=8192,
        calibration_store=store,
        calibration_key="prov::model",
        mcp_tool_names_provider=None,
    )
    messages = [HumanMessage(content="完全重寫")]
    # 6 truncated screenshots (50k chars of base64 each) + code-writing churn.
    for i in range(6):
        blob = "data:image/jpeg;base64," + "K" * 49_000
        messages.append(AIMessage(content="", tool_calls=[{"name": "browser", "args": {"action": "screenshot"}, "id": f"s{i}"}]))
        messages.append(ToolMessage(content=f'{{"image": "{blob}"}}', tool_call_id=f"s{i}"))
    messages.extend(_tool_msgs(30, size=2_000, prefix="write"))

    seen = []

    def handler(request):
        seen.append(request)
        return "ok"

    guard.wrap_model_call(FakeRequest(messages), handler)
    assert len(seen) == 1
    raw, measured, _ = guard._measure(seen[0])
    assert measured <= effective_input_limit(262_144, 8192)
    joined = "".join(str(m.content) for m in seen[0].messages)
    assert "K" * 100 not in joined


def test_guard_emits_context_usage_telemetry():
    """The context_usage UI telemetry is emitted by ContextGuardMiddleware via
    request.runtime.stream_writer (not by the summarizer)."""
    from langchain_core.messages import HumanMessage

    from coworker.agent.middleware.context_guard import ContextGuardMiddleware

    emitted: list[dict] = []

    class _Runtime:
        stream_writer = emitted.append

    class _Request:
        def __init__(self):
            self.runtime = _Runtime()
            self.messages = [HumanMessage(content="hello")]
            self.tools = []

        def override(self, **kwargs):
            return kwargs

    guard = ContextGuardMiddleware(
        window_tokens=128_000,
        max_output_tokens=8192,
        mcp_tool_names_provider=lambda: set(),
    )
    guard._emit_context_usage_event(_Request(), raw=100, measured=95, factor=1.2, over=False)
    assert len(emitted) == 1
    ev = emitted[0]
    assert ev["type"] == "context_usage"
    assert ev["used_tokens"] == 100
    assert ev["used_tokens_calibrated"] == 95
    assert ev["calibration_factor"] == 1.2
    assert ev["budget_tokens"] == guard.limit_tokens
    assert ev["window_tokens"] == 128_000

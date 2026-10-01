"""Intent/Binding two-layer node schema (parse flatten + nested render)."""

from __future__ import annotations

from coworker.workflows.parser import parse_workflow, render_workflow

NESTED = """
name: nested
description: intent/binding round trip
steps:
- id: id:1
  kind: browser
  intent:
    what: 点击导出按钮
    absolute: true
  binding:
    action: click_text
    target: {text: 导出, exact: true}
    params: {timeout: 5}
    verify: [result.changed]
- id: id:2
  kind: file
  intent:
    what: 确认文件
  binding:
    action: exists
    params: {path: /tmp/x}
"""


def test_nested_intent_binding_flattens():
    wf, diags = parse_workflow(NESTED)
    assert wf is not None, diags
    s1 = wf.steps[0]
    assert s1.description == "点击导出按钮"          # intent.what
    assert s1.goal == "点击导出按钮"                  # goal == description (single intent)
    assert s1.absolute is True                        # intent.absolute
    assert s1.do == "click_text"                      # binding.action
    assert s1.locator == {"text": "导出", "exact": True}  # binding.target
    assert s1.params.get("timeout") == 5              # binding.params
    assert s1.post == ["result.changed"]              # binding.verify
    assert "intent" not in s1.params and "binding" not in s1.params


def test_nested_render_roundtrip():
    wf, _ = parse_workflow(NESTED)
    rendered = render_workflow(wf)
    assert "intent:" in rendered and "binding:" in rendered
    reparsed, diags = parse_workflow(rendered)
    assert not diags
    s1 = reparsed.steps[0]
    assert s1.description == "点击导出按钮"
    assert s1.goal == "点击导出按钮"
    assert s1.do == "click_text"
    assert s1.absolute is True
    assert s1.post == ["result.changed"]


def test_flat_authoring_still_parses():
    flat = """
name: flat
description: d
steps:
- id: id:1
  kind: file
  do: exists
  params: {path: /tmp/x}
  description: check
  post: [result.exists]
"""
    wf, diags = parse_workflow(flat)
    assert wf is not None and not diags
    assert wf.steps[0].do == "exists"
    assert wf.steps[0].post == ["result.exists"]

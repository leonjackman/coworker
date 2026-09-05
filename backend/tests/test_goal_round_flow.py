"""Goal round-flow regression tests (post goal-chain 修復).

Covers the accounting / round-count / idle-stop / continuation-template fixes:
- update_goal_round records the COMPLETED round count (round_index+1) so the
  blocked audit (goal.round < 2 → 需 ≥3 輪) works and round doesn't stick at 0.
- account_goal_usage accumulates the round's ACTUAL consumption (prompt+completion).
- the continuation template instructs the model to call update_goal(complete)
  when done (完成信號).
- idle-stop (连续 2 轮纯文字、无工具、未 done) 推断为 complete 并停止续跑，
  前端收到 complete 后自动关闭 GoalCard（恢复 11cd0313 行为），不再 nudge 续命。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main as _main_module  # noqa: E402
from coworker.goal_prompts import render_goal_continuation  # noqa: E402
from coworker.sessions import GoalState, SessionStore  # noqa: E402


def _make_session(tmp_path) -> tuple[SessionStore, str]:
    store = SessionStore(tmp_path / "sessions")
    s = store.create("s1")
    store.set_goal(s.id, "objective", token_budget=100_000)
    return store, s.id


def test_update_goal_round_records_completed_count(tmp_path):
    store, sid = _make_session(tmp_path)
    # round_index 是 0-based 当前轮；完成 2 轮 → round_index+1 = 2。
    store.update_goal_round(sid, 0 + 1)
    assert store.get_goal(sid).round == 1
    store.update_goal_round(sid, 1 + 1)
    assert store.get_goal(sid).round == 2
    # blocked 审计：round=2（≥2）→ 允许 blocked（3 轮后）。
    store.update_goal_status(sid, "active")
    assert store.get_goal(sid).round == 2


def test_account_goal_usage_accumulates_actual_consumption(tmp_path):
    store, sid = _make_session(tmp_path)
    # 每轮实际消耗 = prompt + completion（该轮所有 model call 之和）。
    store.account_goal_usage(sid, 12_000 + 3_000, 30.5)
    store.account_goal_usage(sid, 8_000 + 2_000, 20.0)
    g = store.get_goal(sid)
    assert g.tokens_used == 25_000
    assert g.time_used_seconds == 50


def test_continuation_template_instructs_completion_signal():
    goal = GoalState(objective="o", status="active", tokens_used=10, time_used_seconds=1, round=1)
    txt = render_goal_continuation(goal)
    assert 'update_goal(status="complete")' in txt


def test_idle_stop_infers_complete():
    backend = Path(__file__).resolve().parents[1]
    # Goal round-loop logic moved out of the monolith into coworker/api/chat.py;
    # scan the split API modules instead of main.py.
    text = (backend / "coworker" / "api" / "chat.py").read_text(encoding="utf-8")
    # nudge 续命机制已移除（不再劝模型继续跑工具）。
    assert "_IDLE_NUDGE" not in text
    # 空闲计数仍保留。
    assert "idle_rounds += 1" in text
    # 模型侧完成信号仍在续跑模板里（goal_prompts.py）。
    prompts = backend / "coworker" / "goal_prompts.py"
    assert 'update_goal(status="complete")' in prompts.read_text(encoding="utf-8")
    # 连续 2 轮纯文字 → 推断 complete（前端自动关卡片，不再卡 active / paused）。
    assert 'update_goal_status(session_id, "complete")' in text

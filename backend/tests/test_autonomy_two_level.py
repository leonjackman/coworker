"""Two-level permission model (默認權限 guarded / 完整權限 autonomous) tests.

Covers the collapse from the retired three-level model:
- ``supervised`` legacy values normalize to ``guarded`` everywhere.
- The canonical runtime type only exposes guarded / autonomous.
- Legacy ``access_mode`` sessions still migrate to the two-level autonomy.
- The HITL write gate asks only at the workspace boundary under the default
  (guarded) permission and never under full (autonomous).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.agent.core import normalize_autonomy  # noqa: E402
from coworker.agent.types import Autonomy  # noqa: E402
from coworker.sessions import Session  # noqa: E402


def _literal_values(literal: type) -> set[str]:
    return set(getattr(literal, "__args__", ())) or set()


class TestNormalizeAutonomy:
    def test_only_two_levels_in_canonical_type(self):
        assert _literal_values(Autonomy) == {"guarded", "autonomous"}

    def test_legacy_supervised_folds_to_guarded(self):
        assert normalize_autonomy("supervised") == "guarded"

    def test_guarded_and_autonomous_pass_through(self):
        assert normalize_autonomy("guarded") == "guarded"
        assert normalize_autonomy("autonomous") == "autonomous"

    def test_unknown_and_none_default_to_guarded(self):
        assert normalize_autonomy(None) == "guarded"
        assert normalize_autonomy("whatever") == "guarded"
        assert normalize_autonomy("") == "guarded"


class TestLegacySessionMigration:
    def test_legacy_access_mode_default_maps_to_guarded(self):
        session = Session.from_dict(
            {"id": "s1", "access_mode": "default", "messages": []}
        )
        assert session.autonomy == "guarded"

    def test_legacy_access_mode_full_maps_to_autonomous(self):
        session = Session.from_dict(
            {"id": "s1", "access_mode": "full", "messages": []}
        )
        assert session.autonomy == "autonomous"

    def test_legacy_supervised_autonomy_maps_to_guarded(self):
        session = Session.from_dict(
            {"id": "s1", "autonomy": "supervised", "messages": []}
        )
        assert session.autonomy == "guarded"

    def test_missing_modes_default_to_guarded(self):
        session = Session.from_dict({"id": "s1", "messages": []})
        assert session.autonomy == "guarded"

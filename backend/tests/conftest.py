"""Global test isolation — protect the user's REAL data directory.

Several test modules ``import main`` (the FastAPI app). Its module-level
singletons (``settings``, ``session_store``, ``project_store``, …) resolve their
data dir from ``COWORKER_DATA_DIR`` at IMPORT time, exactly once per process.

If a module imports ``main`` before any test sets ``COWORKER_DATA_DIR``, those
singletons point at the user's REAL data dir — and any test that clears
sessions/projects then destroys real user data. That is not hypothetical: a full
``pytest`` run once wiped real sessions because ``test_interject`` imports
``main`` after other modules already bound it to the real dir.

This conftest is imported by pytest BEFORE any test module, so it pins the whole
session to a throwaway data dir. The first (and therefore decisive) import of
``main`` is always isolated; a module may still override ``COWORKER_DATA_DIR``,
but it can never fall back to the real dir.
"""

import os
import tempfile
from pathlib import Path

_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="cw_pytest_data_"))
os.environ["COWORKER_DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ.setdefault("COWORKER_AGENT_PROVIDER", "simulated")
os.environ.setdefault("COWORKER_LOG_LEVEL", "WARNING")


def is_isolated_data_dir(path: str | os.PathLike[str]) -> bool:
    """True only for paths that clearly belong to a test run (never the real
    ``~/Library/Application Support/Coworker``)."""
    p = Path(path)
    text = str(p)
    temp = tempfile.gettempdir()
    return text.startswith(temp) or ".test" in p.name or "cw_pytest_data_" in text

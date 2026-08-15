import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """Parse an integer env var, falling back to ``default`` on bad input.

    A malformed value (e.g. ``COWORKER_CHECKPOINT_CAP=abc``) must never crash
    backend startup — fall back to the product default and log instead.
    """
    raw = (os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid integer for %s=%r; using default %d", name, raw, default)
        return default


@dataclass(frozen=True)
class BackendSettings:
    project_root: Path
    data_dir: Path
    workspace_dir: Path
    memory_dir: Path
    agent_provider: str
    openai_model: str
    checkpoint_cap_per_session: int
    checkpoint_max_bytes_per_thread: int
    checkpoint_sweep_interval_seconds: int
    memory_enabled: bool
    memory_char_limit: int
    memory_auto_extract: bool
    memory_nudge_interval: int
    memory_extract_model: str


def load_settings() -> BackendSettings:
    backend_dir = Path(__file__).resolve().parents[1]
    project_root = backend_dir.parent
    default_data_dir = Path.home() / "Library" / "Application Support" / "Coworker"
    data_dir = Path(os.getenv("COWORKER_DATA_DIR", str(default_data_dir))).expanduser().resolve()
    workspace_dir = Path(os.getenv("COWORKER_WORKSPACE", str(project_root))).expanduser().resolve()
    agent_provider = os.getenv("COWORKER_AGENT_PROVIDER", "simulated").strip().lower()
    openai_model = os.getenv("COWORKER_OPENAI_MODEL", "gpt-4.1-mini").strip()

    # Checkpoint lifecycle tuning (see coworker.checkpoints).
    checkpoint_cap_per_session = _env_int("COWORKER_CHECKPOINT_CAP", 500)
    checkpoint_max_bytes_per_thread = _env_int("COWORKER_CHECKPOINT_MAX_MB_PER_THREAD", 32) * 1024 * 1024
    checkpoint_sweep_interval_seconds = _env_int("COWORKER_CHECKPOINT_SWEEP_INTERVAL_SECONDS", 21600)

    memory_enabled = os.getenv("COWORKER_MEMORY_ENABLED", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }
    memory_char_limit = _env_int("COWORKER_MEMORY_CHAR_LIMIT", 2000)
    memory_auto_extract = os.getenv("COWORKER_MEMORY_AUTO_EXTRACT", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }
    memory_nudge_interval = _env_int("COWORKER_MEMORY_NUDGE_INTERVAL", 10)
    memory_extract_model = os.getenv("COWORKER_MEMORY_EXTRACT_MODEL", "").strip()

    data_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    memory_dir = (data_dir / "memory").resolve()
    memory_dir.mkdir(parents=True, exist_ok=True)

    return BackendSettings(
        project_root=project_root,
        data_dir=data_dir,
        workspace_dir=workspace_dir,
        memory_dir=memory_dir,
        agent_provider=agent_provider,
        openai_model=openai_model,
        checkpoint_cap_per_session=checkpoint_cap_per_session,
        checkpoint_max_bytes_per_thread=checkpoint_max_bytes_per_thread,
        checkpoint_sweep_interval_seconds=checkpoint_sweep_interval_seconds,
        memory_enabled=memory_enabled,
        memory_char_limit=memory_char_limit,
        memory_auto_extract=memory_auto_extract,
        memory_nudge_interval=memory_nudge_interval,
        memory_extract_model=memory_extract_model,
    )

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BackendSettings:
    project_root: Path
    data_dir: Path
    workspace_dir: Path
    agent_provider: str
    openai_model: str
    checkpoint_cap_per_session: int
    checkpoint_max_bytes_per_thread: int
    checkpoint_sweep_interval_seconds: int


def load_settings() -> BackendSettings:
    backend_dir = Path(__file__).resolve().parents[1]
    project_root = backend_dir.parent
    default_data_dir = Path.home() / "Library" / "Application Support" / "Coworker"
    data_dir = Path(os.getenv("COWORKER_DATA_DIR", str(default_data_dir))).expanduser().resolve()
    workspace_dir = Path(os.getenv("COWORKER_WORKSPACE", str(project_root))).expanduser().resolve()
    agent_provider = os.getenv("COWORKER_AGENT_PROVIDER", "simulated").strip().lower()
    openai_model = os.getenv("COWORKER_OPENAI_MODEL", "gpt-4.1-mini").strip()

    # Checkpoint lifecycle tuning (see coworker.checkpoints).
    checkpoint_cap_per_session = int(os.getenv("COWORKER_CHECKPOINT_CAP", "500").strip() or "500")
    checkpoint_max_bytes_per_thread = int(
        os.getenv("COWORKER_CHECKPOINT_MAX_MB_PER_THREAD", "32").strip() or "32"
    ) * 1024 * 1024
    checkpoint_sweep_interval_seconds = int(
        os.getenv("COWORKER_CHECKPOINT_SWEEP_INTERVAL_SECONDS", "21600").strip() or "21600"
    )

    data_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    return BackendSettings(
        project_root=project_root,
        data_dir=data_dir,
        workspace_dir=workspace_dir,
        agent_provider=agent_provider,
        openai_model=openai_model,
        checkpoint_cap_per_session=checkpoint_cap_per_session,
        checkpoint_max_bytes_per_thread=checkpoint_max_bytes_per_thread,
        checkpoint_sweep_interval_seconds=checkpoint_sweep_interval_seconds,
    )

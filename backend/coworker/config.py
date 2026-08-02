import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BackendSettings:
    project_root: Path
    workspace_dir: Path
    agent_provider: str
    openai_model: str


def load_settings() -> BackendSettings:
    backend_dir = Path(__file__).resolve().parents[1]
    project_root = backend_dir.parent
    workspace_dir = Path(os.getenv("COWORKER_WORKSPACE", str(project_root))).expanduser().resolve()
    agent_provider = os.getenv("COWORKER_AGENT_PROVIDER", "simulated").strip().lower()
    openai_model = os.getenv("COWORKER_OPENAI_MODEL", "gpt-4.1-mini").strip()

    workspace_dir.mkdir(parents=True, exist_ok=True)

    return BackendSettings(
        project_root=project_root,
        workspace_dir=workspace_dir,
        agent_provider=agent_provider,
        openai_model=openai_model,
    )

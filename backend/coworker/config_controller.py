from typing import Any

from .config import BackendSettings
from .providers import ProviderManager


class AppConfigController:
    def __init__(self, settings: BackendSettings, provider_manager: ProviderManager):
        self.settings = settings
        self.provider_manager = provider_manager

    def runtime_config(self) -> dict[str, Any]:
        provider_config = self.provider_manager.public_config()
        return {
            "workspace": str(self.settings.workspace_dir),
            "data_dir": str(self.settings.data_dir),
            "default_mode": "single",
            "agent_provider": self.settings.agent_provider,
            "available_modes": ["single"],
            "selected_provider_id": provider_config.get("default_provider_id", ""),
            "selected_model": provider_config.get("default_model", ""),
        }

    def update_runtime_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider_id = str(payload.get("selected_provider_id") or "").strip()
        model = str(payload.get("selected_model") or "").strip()
        if provider_id:
            self.provider_manager.set_default_provider(provider_id, model)
        return self.runtime_config()

import os
from dataclasses import dataclass


def _flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "si", "on"}


@dataclass(frozen=True)
class RuntimeSettings:
    environment: str
    shadow_mode: bool

    @property
    def is_development(self) -> bool:
        return self.environment in {"dev", "development", "local", "test"}

    @property
    def publication_mode(self) -> str:
        return "shadow" if self.shadow_mode else "live"


def load_runtime_settings() -> RuntimeSettings:
    environment = (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "development").strip().lower()
    shadow_default = environment in {"dev", "development", "local", "test"}
    shadow_mode = _flag("SHADOW_MODE", default=shadow_default)
    return RuntimeSettings(
        environment=environment,
        shadow_mode=shadow_mode,
    )

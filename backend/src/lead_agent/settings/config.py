from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from lead_agent.domain.configuration import IcpConfig, RoutingConfig, load_icp, load_routing


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_name: str = "lead-qualification-routing-agent"
    app_version: str = "0.1.0"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"
    secret_key: str = "local-development-only"
    webhook_signing_secret: str = ""
    cors_allowed_origins: str = "http://localhost:5173"
    database_url: str = "postgresql+psycopg://lead_agent:lead_agent@localhost:5432/lead_agent"
    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "lead-processing"
    job_max_retries: int = 3
    icp_config_path: str = "config/icp.example.yaml"
    routing_config_path: str = "config/routing.example.yaml"
    llm_provider: str = "none"
    openai_api_key: str = ""
    openai_model: str = ""
    openai_org_id: str = ""
    openai_timeout_seconds: float = 30.0
    openai_max_retries: int = 2
    openai_store_responses: bool = False
    crm_provider: str = "none"
    mailbox_provider: str = "none"
    social_provider: str = "none"
    demo_data_enabled: bool = False

    @property
    def repository_root(self) -> Path:
        return Path(__file__).resolve().parents[4]

    def resolve_path(self, configured_path: str) -> Path:
        path = Path(configured_path)
        if path.is_absolute():
            return path
        candidates = (
            Path.cwd() / path,
            Path.cwd().parent / path,
            self.repository_root / path,
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    def load_policies(self) -> tuple[IcpConfig, RoutingConfig]:
        return (
            load_icp(self.resolve_path(self.icp_config_path)),
            load_routing(self.resolve_path(self.routing_config_path)),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

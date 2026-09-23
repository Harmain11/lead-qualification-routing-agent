from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from lead_agent.domain.configuration import IcpConfig, RoutingConfig, load_icp, load_routing
from lead_agent.domain.crm import CrmConfig, load_crm_config


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "lead-qualification-routing-agent"
    app_version: str = "0.6.5"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"
    observability_enabled: bool = True
    secret_key: str = "local-development-only"
    auth_development_fallback: bool = True
    auth_session_cookie_name: str = "lead_agent_session"
    auth_csrf_cookie_name: str = "lead_agent_csrf"
    auth_session_hours: int = 336
    auth_cookie_secure: bool = False
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    webhook_signing_secret: str = ""
    cors_allowed_origins: str = "http://localhost:5173"
    database_url: str = "postgresql+psycopg://lead_agent:lead_agent@localhost:5432/lead_agent"
    database_ssl_mode: Literal[
        "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
    ] = "prefer"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout_seconds: float = 30.0
    database_pool_recycle_seconds: int = 1800
    database_connect_timeout_seconds: int = 10
    redis_url: str = "redis://localhost:6379/0"
    redis_required: bool = True
    redis_connect_timeout_seconds: float = 2.0
    redis_socket_timeout_seconds: float = 2.0
    redis_retry_on_timeout: bool = True
    rate_limit_backend: Literal["memory", "redis"] = "memory"
    rate_limit_authentication_max: int = 10
    rate_limit_authentication_window_seconds: int = 60
    rate_limit_oauth_max: int = 12
    rate_limit_oauth_window_seconds: int = 300
    rate_limit_credentials_max: int = 5
    rate_limit_credentials_window_seconds: int = 300
    rate_limit_provider_actions_max: int = 20
    rate_limit_provider_actions_window_seconds: int = 60
    rate_limit_agent_max: int = 30
    rate_limit_agent_window_seconds: int = 60
    queue_name: str = "lead-processing"
    job_max_retries: int = 3
    icp_config_path: str = "config/icp.example.yaml"
    routing_config_path: str = "config/routing.example.yaml"
    crm_config_path: str = "config/crm.example.yaml"
    llm_provider: str = "none"
    openai_api_key: str = ""
    openai_model: str = ""
    openai_org_id: str = ""
    openai_timeout_seconds: float = 30.0
    openai_max_retries: int = 2
    openai_store_responses: bool = False
    crm_provider: str = "mock"
    crm_max_attempts: int = 3
    export_sync_limit: int = 5000
    crm_timeout_seconds: float = 20.0
    secret_store_provider: str = "development"
    managed_secret_store_backend: str = ""
    oauth_broker: str = "development"
    integration_public_base_url: str = "http://localhost:8000"
    frontend_public_base_url: str = "http://localhost:5173"
    google_client_id: str = ""
    google_client_secret: str = ""
    microsoft_tenant_id: str = "common"
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    slack_client_id: str = ""
    slack_client_secret: str = ""
    hubspot_client_id: str = ""
    hubspot_client_secret: str = ""
    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    salesforce_client_id: str = ""
    salesforce_client_secret: str = ""
    salesforce_refresh_token: str = ""
    salesforce_login_base_url: str = "https://login.salesforce.com"
    salesforce_api_version: str = "v68.0"
    hubspot_access_token: str = ""
    hubspot_api_base_url: str = "https://api.hubapi.com"
    mailbox_provider: str = "none"
    social_provider: str = "none"
    demo_data_enabled: bool = False
    notification_provider: str = "mock"
    slack_bot_token: str = ""
    slack_channel_id: str = ""

    @model_validator(mode="after")
    def validate_environment_security(self) -> Settings:
        if self.auth_session_hours < 1 or self.auth_session_hours > 24 * 31:
            raise ValueError("AUTH_SESSION_HOURS must be between 1 and 744")
        if self.auth_session_cookie_name == self.auth_csrf_cookie_name:
            raise ValueError("session and CSRF cookie names must be different")
        if self.auth_cookie_samesite == "none" and not self.auth_cookie_secure:
            raise ValueError("SameSite=None cookies require AUTH_COOKIE_SECURE=true")
        if (
            min(
                self.database_pool_size,
                self.database_pool_timeout_seconds,
                self.database_pool_recycle_seconds,
                self.database_connect_timeout_seconds,
                self.redis_connect_timeout_seconds,
                self.redis_socket_timeout_seconds,
            )
            <= 0
            or self.database_max_overflow < 0
        ):
            raise ValueError("database and Redis pool/timeout settings must be positive")
        for value in (
            self.rate_limit_authentication_max,
            self.rate_limit_authentication_window_seconds,
            self.rate_limit_oauth_max,
            self.rate_limit_oauth_window_seconds,
            self.rate_limit_credentials_max,
            self.rate_limit_credentials_window_seconds,
            self.rate_limit_provider_actions_max,
            self.rate_limit_provider_actions_window_seconds,
            self.rate_limit_agent_max,
            self.rate_limit_agent_window_seconds,
        ):
            if value < 1:
                raise ValueError("rate-limit policies must have positive limits and windows")
        if self.job_max_retries < 0:
            raise ValueError("JOB_MAX_RETRIES must be zero or greater")
        if self.export_sync_limit < 1 or self.export_sync_limit > 100_000:
            raise ValueError("EXPORT_SYNC_LIMIT must be between 1 and 100000")
        if self.app_env == "production":
            self._validate_production_security()
        return self

    def _validate_production_security(self) -> None:
        problems: list[str] = []

        def require(condition: bool, message: str) -> None:
            if not condition:
                problems.append(message)

        require(not self.auth_development_fallback, "AUTH_DEVELOPMENT_FALLBACK must be false")
        require(self.auth_cookie_secure, "AUTH_COOKIE_SECURE must be true")
        require(self.log_format == "json", "LOG_FORMAT must be json in production")
        require(
            len(self.secret_key) >= 32 and not _is_placeholder(self.secret_key),
            "SECRET_KEY must be a non-placeholder value of at least 32 characters",
        )
        require(
            len(self.webhook_signing_secret) >= 32
            and not _is_placeholder(self.webhook_signing_secret),
            "WEBHOOK_SIGNING_SECRET must be a non-placeholder value of at least 32 characters",
        )
        require(
            self.secret_store_provider == "managed",
            "SECRET_STORE_PROVIDER must be managed",
        )
        require(
            bool(self.managed_secret_store_backend.strip())
            and not _is_placeholder(self.managed_secret_store_backend),
            "MANAGED_SECRET_STORE_BACKEND is required",
        )
        require(self.oauth_broker == "provider", "OAUTH_BROKER must be provider")
        require(self.llm_provider == "openai", "LLM_PROVIDER must be openai")
        require(
            bool(self.openai_api_key)
            and not _is_placeholder(self.openai_api_key)
            and bool(self.openai_model)
            and not _is_placeholder(self.openai_model),
            "OpenAI provider credentials and model must be configured",
        )
        require(self.crm_provider != "mock", "CRM_PROVIDER cannot be mock")
        require(
            self.notification_provider != "mock",
            "NOTIFICATION_PROVIDER cannot be mock",
        )
        require(
            _is_https_base_url(self.salesforce_login_base_url)
            and _is_public_https_base_url(self.salesforce_login_base_url),
            "SALESFORCE_LOGIN_BASE_URL must be an absolute public HTTPS URL",
        )
        require(
            _is_https_base_url(self.hubspot_api_base_url)
            and _is_public_https_base_url(self.hubspot_api_base_url),
            "HUBSPOT_API_BASE_URL must be an absolute public HTTPS URL",
        )
        require(not self.demo_data_enabled, "DEMO_DATA_ENABLED must be false")
        require(
            self.database_url
            != "postgresql+psycopg://lead_agent:lead_agent@localhost:5432/lead_agent",
            "DATABASE_URL must be explicitly configured for production",
        )
        database = urlsplit(self.database_url)
        require(
            database.scheme in {"postgresql", "postgresql+psycopg"}
            and bool(database.hostname)
            and database.hostname not in {"localhost", "127.0.0.1", "::1"}
            and self.database_ssl_mode in {"require", "verify-ca", "verify-full"},
            (
                "production PostgreSQL requires a remote host and SSL mode "
                "require/verify-ca/verify-full"
            ),
        )
        require(
            self.redis_url != "redis://localhost:6379/0",
            "REDIS_URL must be explicitly configured for production",
        )
        redis = urlsplit(self.redis_url)
        require(
            redis.scheme == "rediss"
            and bool(redis.hostname)
            and redis.hostname not in {"localhost", "127.0.0.1", "::1"},
            "production Redis requires a remote rediss:// endpoint",
        )
        require(
            self.rate_limit_backend == "redis",
            "RATE_LIMIT_BACKEND must be redis in production",
        )
        require(self.redis_required, "REDIS_REQUIRED must be true in production")
        require(
            _is_https_base_url(self.integration_public_base_url),
            "INTEGRATION_PUBLIC_BASE_URL must be an absolute HTTPS URL",
        )
        require(
            _is_public_https_base_url(self.integration_public_base_url),
            "INTEGRATION_PUBLIC_BASE_URL cannot use a localhost or private callback host",
        )
        require(
            _is_https_base_url(self.frontend_public_base_url),
            "FRONTEND_PUBLIC_BASE_URL must be an absolute HTTPS URL",
        )
        require(
            _is_public_https_base_url(self.frontend_public_base_url),
            "FRONTEND_PUBLIC_BASE_URL cannot use a localhost or private host",
        )
        origins = self.cors_origins
        require(bool(origins), "CORS_ALLOWED_ORIGINS must contain a trusted frontend origin")
        require("*" not in origins, "wildcard production CORS is forbidden")
        require(
            all(_is_https_origin(origin) for origin in origins),
            "production CORS origins must be explicit HTTPS origins without paths",
        )
        require(
            all(_is_public_https_base_url(origin) for origin in origins),
            "production CORS origins cannot use localhost or private IP addresses",
        )
        frontend_origin = _origin(self.frontend_public_base_url)
        require(
            frontend_origin in origins,
            "CORS_ALLOWED_ORIGINS must include FRONTEND_PUBLIC_BASE_URL origin",
        )
        if self.crm_provider == "salesforce":
            require(
                all(
                    not _is_placeholder(value)
                    for value in (
                        self.salesforce_client_id,
                        self.salesforce_client_secret,
                        self.salesforce_refresh_token,
                    )
                ),
                "Salesforce fallback credentials must be configured",
            )
        elif self.crm_provider == "hubspot":
            require(
                bool(self.hubspot_access_token)
                and not _is_placeholder(self.hubspot_access_token)
                and bool(self.hubspot_client_id)
                and not _is_placeholder(self.hubspot_client_id)
                and bool(self.hubspot_client_secret)
                and not _is_placeholder(self.hubspot_client_secret),
                "HubSpot fallback and OAuth application credentials must be configured",
            )
        else:
            problems.append("CRM_PROVIDER must be salesforce or hubspot in production")
        if self.notification_provider == "slack":
            require(
                bool(self.slack_bot_token)
                and not _is_placeholder(self.slack_bot_token)
                and bool(self.slack_channel_id)
                and not _is_placeholder(self.slack_channel_id)
                and bool(self.slack_client_id)
                and not _is_placeholder(self.slack_client_id)
                and bool(self.slack_client_secret)
                and not _is_placeholder(self.slack_client_secret),
                "Slack notification and OAuth application credentials must be configured",
            )
        else:
            problems.append("NOTIFICATION_PROVIDER must be slack in production")
        if problems:
            raise ValueError("invalid production configuration: " + "; ".join(problems))

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

    def load_crm_config(self) -> CrmConfig:
        return load_crm_config(self.resolve_path(self.crm_config_path))


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return not normalized or normalized.startswith("replace_") or "development-only" in normalized


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""


def _is_https_base_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and not parsed.query
        and not parsed.fragment
    )


def _is_https_origin(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _is_public_https_base_url(value: str) -> bool:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if parsed.username or parsed.password or not host:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True

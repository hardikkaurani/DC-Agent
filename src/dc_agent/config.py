"""Configuration settings for DC-Agent."""

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AIProviderType(StrEnum):
    GEMINI = "gemini"
    OLLAMA = "ollama"


class DailyPolicyType(StrEnum):
    ALTERNATING = "alternating"
    LEETCODE_ONLY = "leetcode_only"
    CODEFORCES_ONLY = "codeforces_only"


class SandboxBackendType(StrEnum):
    SUBPROCESS = "subprocess"
    DOCKER = "docker"


class Settings(BaseSettings):
    """Central application configuration loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # AI Provider Settings
    ai_provider: AIProviderType = Field(default=AIProviderType.GEMINI, alias="AI_PROVIDER")
    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")

    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="deepseek-coder:latest", alias="OLLAMA_MODEL")

    # Platform Profiles
    codeforces_username: str = Field(default="hkvariant", alias="CODEFORCES_USERNAME")
    leetcode_username: str = Field(default="hardikkaurani_", alias="LEETCODE_USERNAME")

    # Persistence
    database_url: str = Field(default="sqlite:///dc_agent.db", alias="DATABASE_URL")

    # Scheduler Settings
    timezone: str = Field(default="Asia/Kolkata", alias="TIMEZONE")
    daily_run_time: str = Field(default="17:15", alias="DAILY_RUN_TIME")  # 5:15 PM
    daily_policy: DailyPolicyType = Field(default=DailyPolicyType.ALTERNATING, alias="DAILY_POLICY")

    # Sandbox & Resource Limits (Docker is the preferred secure backend)
    sandbox_backend: SandboxBackendType = Field(
        default=SandboxBackendType.DOCKER, alias="SANDBOX_BACKEND"
    )
    allow_insecure_fallback: bool = Field(default=False, alias="ALLOW_INSECURE_FALLBACK")
    docker_image: str = Field(default="python:3.12-slim", alias="DOCKER_IMAGE")
    retry_limit: int = Field(default=5, alias="RETRY_LIMIT")
    execution_timeout: float = Field(default=2.0, alias="EXECUTION_TIMEOUT")
    memory_limit_mb: int = Field(default=256, alias="MEMORY_LIMIT_MB")

    # Problem workspace path
    workspace_dir: Path = Field(default=Path("problem_workspace"), alias="WORKSPACE_DIR")

    def get_masked_gemini_key(self) -> str:
        """Return safely masked API key for logging or display."""
        if not self.gemini_api_key:
            return "<NOT SET>"
        raw = self.gemini_api_key.get_secret_value()
        if len(raw) <= 8:
            return "***"
        return f"{raw[:4]}...{raw[-4:]}"


def get_canonical_cycle_date(
    settings: Settings | None = None, now_dt: datetime | None = None
) -> str:
    """Return the current local cycle date string (YYYY-MM-DD) in canonical Asia/Kolkata timezone."""
    cfg = settings or get_settings()
    tz = ZoneInfo(cfg.timezone)
    if now_dt is None:
        dt = datetime.now(tz)
    elif now_dt.tzinfo is None:
        dt = now_dt.replace(tzinfo=tz)
    else:
        dt = now_dt.astimezone(tz)
    return dt.strftime("%Y-%m-%d")


# Global cached settings instance
_settings: Settings | None = None


def get_settings(reload: bool = False) -> Settings:
    """Get the active application settings singleton."""
    global _settings
    if _settings is None or reload:
        _settings = Settings()
    return _settings

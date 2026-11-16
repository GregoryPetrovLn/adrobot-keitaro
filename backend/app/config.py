"""Application settings, loaded from the environment (and a local .env file)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Keitaro ---
    keitaro_base_url: str = "https://in.posdk.xyz"
    keitaro_api_key: str = ""
    keitaro_timeout: float = 30.0

    # --- Campaign creator defaults ---
    default_traffic_source_id: int | None = None
    default_domain_id: int | None = None
    default_group_id: int | None = None
    campaign_name_prefix: str = ""
    google_redirect_url: str = "https://www.google.com"

    # Campaign policy. The brief does not specify these, so they are settings rather than
    # constants — cost model and uniqueness are exactly the fields a media team has
    # opinions about. The defaults mirror what the Keitaro UI creates.
    campaign_cost_type: str = "CPC"
    campaign_cost_currency: str = "USD"
    campaign_cost_auto: bool = True
    campaign_uniqueness_method: str = "ip_ua"
    campaign_cookies_ttl: int = 24

    # --- App ---
    database_url: str = "sqlite+aiosqlite:///./adrobot.db"
    log_level: str = "INFO"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    @field_validator("keitaro_base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [part.strip() for part in v.split(",") if part.strip()]
        return v

    @field_validator("default_traffic_source_id", "default_domain_id", "default_group_id",
                     mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        # `DEFAULT_DOMAIN_ID=` in .env arrives as "" and must not blow up int parsing.
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def keitaro_api_url(self) -> str:
        return f"{self.keitaro_base_url}/admin_api/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()

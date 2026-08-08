"""Validated environment configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MANGASENSEI_",
        extra="ignore",
    )

    environment: str = "development"
    database_url: str | None = None
    storage_root: Path = Path("var/storage")
    model_cache: Path = Path("var/models")
    jmdict_path: Path = Path("var/data/jmdict.json")
    frontend_dist: Path | None = Path("frontend/dist")
    capability_peppers: tuple[str, ...] | None = None
    retention_hours: int = Field(default=24, frozen=True)
    max_upload_bytes: int = Field(default=12 * 1024 * 1024, gt=0, le=12 * 1024 * 1024)
    max_image_pixels: int = Field(default=25_000_000, gt=0, le=25_000_000)
    max_image_side: int = Field(default=10_000, gt=0, le=10_000)
    api_rate_limit_per_minute: int = Field(default=120, ge=1, le=10_000)
    upload_rate_limit_per_minute: int = Field(default=10, ge=1, le=1_000)
    reprocess_rate_limit_per_minute: int = Field(default=6, ge=1, le=1_000)
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_lease_seconds: int = Field(default=300, ge=30, le=3600)
    retention_poll_seconds: float = Field(default=60.0, gt=0, le=3600)
    ocr_device: str = Field(default="cpu", pattern=r"^[A-Za-z0-9:_-]{1,32}$")
    gemini_model: str = "gemini-3.6-flash"
    gemini_daily_budget_usd: float = Field(default=5.0, gt=0)
    gemini_max_calls_per_page: int = Field(default=3, ge=1, le=3)
    google_api_key: SecretStr | None = Field(default=None, validation_alias="GOOGLE_API_KEY")

    @field_validator("capability_peppers")
    @classmethod
    def validate_capability_peppers(
        cls, peppers: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        if peppers is None:
            return peppers
        if not peppers:
            raise ValueError("at least one capability pepper is required")
        if any(
            len(pepper.encode()) < 32
            or pepper
            in {
                "replace-with-at-least-32-random-characters",
                "replace-with-a-long-random-pepper",
            }
            for pepper in peppers
        ):
            raise ValueError(
                "each capability pepper must be at least 32 random bytes and not a placeholder"
            )
        return peppers

    def require_runtime_config(self) -> tuple[str, tuple[str, ...]]:
        if not self.database_url or not self.capability_peppers:
            raise ValueError(
                "MANGASENSEI_DATABASE_URL and MANGASENSEI_CAPABILITY_PEPPERS are required"
            )
        return self.database_url, self.capability_peppers

    @field_validator("retention_hours")
    @classmethod
    def require_fixed_retention(cls, value: int) -> int:
        if value != 24:
            raise ValueError("MangaSensei v0.1.0 has a fixed 24-hour retention contract")
        return value

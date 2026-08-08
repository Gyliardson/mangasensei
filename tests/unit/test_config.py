from __future__ import annotations

import pytest
from pydantic import ValidationError

from mangasensei.config import Settings


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_google_api_key_from_environment_disables_gemini(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", value)

    settings = Settings(_env_file=None)

    assert settings.google_api_key is None


def test_non_blank_google_api_key_from_environment_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-gemini-key")

    settings = Settings(_env_file=None)

    assert settings.google_api_key is not None
    assert settings.google_api_key.get_secret_value() == "test-gemini-key"


def test_require_runtime_config_accepts_configured_settings() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@host/db",
        capability_peppers=("a-true-random-pepper-000000000000000000000000",),
    )

    database_url, peppers = settings.require_runtime_config()

    assert database_url == "postgresql+psycopg://u:p@host/db"
    assert peppers == ("a-true-random-pepper-000000000000000000000000",)


def test_require_runtime_config_rejects_missing_database_url() -> None:
    settings = Settings(
        _env_file=None,
        database_url=None,
        capability_peppers=("a-true-random-pepper-000000000000000000000000",),
    )

    with pytest.raises(ValueError, match="MANGASENSEI_DATABASE_URL"):
        settings.require_runtime_config()


def test_require_runtime_config_rejects_missing_peppers() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@host/db",
        capability_peppers=None,
    )

    with pytest.raises(ValueError, match="MANGASENSEI_CAPABILITY_PEPPERS"):
        settings.require_runtime_config()


def test_placeholder_pepper_is_rejected() -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://u:p@host/db",
            capability_peppers=("replace-with-a-long-random-pepper",),
        )


def test_documentation_placeholder_pepper_is_rejected() -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://u:p@host/db",
            capability_peppers=("replace-with-at-least-32-random-characters",),
        )


def test_short_pepper_is_rejected() -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://u:p@host/db",
            capability_peppers=("too-short",),
        )


def test_artifact_only_settings_construct_without_database_credentials() -> None:
    settings = Settings(_env_file=None)

    assert settings.model_cache is not None
    assert settings.jmdict_path is not None
    assert settings.database_url is None
    assert settings.capability_peppers is None
    assert settings.google_api_key is None

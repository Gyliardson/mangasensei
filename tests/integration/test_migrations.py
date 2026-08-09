from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def alembic_config(database_url: str) -> Config:
    config = Config("backend/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.mark.integration
def test_migrations_upgrade_downgrade_and_reupgrade(postgres_url: str) -> None:
    config = alembic_config(postgres_url)

    command.upgrade(config, "head")
    engine = create_engine(postgres_url)
    assert {
        "image_blobs",
        "pages",
        "page_capabilities",
        "jobs",
        "job_attempts",
        "ocr_runs",
        "ocr_regions",
        "linguistic_runs",
        "linguistic_tokens",
        "gemini_calls",
        "gemini_analyses",
        "gemini_cost_ledger",
        "rate_limit_buckets",
        "study_results",
    }.issubset(inspect(engine).get_table_names(schema="mangasensei"))

    command.downgrade(config, "base")
    assert "pages" not in inspect(engine).get_table_names(schema="mangasensei")

    command.upgrade(config, "head")
    with engine.connect() as connection:
        heads = connection.execute(text("SELECT count(*) FROM alembic_version")).scalar_one()
    assert heads == 1
    engine.dispose()

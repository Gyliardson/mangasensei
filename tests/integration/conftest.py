from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from testcontainers.community.postgres import PostgresContainer


def pytest_asyncio_loop_factories(config: object, item: object) -> dict[str, object]:
    del config, item
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    configured = os.getenv("MANGASENSEI_TEST_DATABASE_URL")
    if configured:
        yield configured
        return

    with PostgresContainer("postgres:18.4", driver="psycopg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def migrated_postgres_url(postgres_url: str) -> str:
    config = Config("backend/alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    command.upgrade(config, "head")
    return postgres_url


_MANGASENSEI_TABLES = (
    "document_import_capabilities",
    "document_imports",
    "documents",
    "document_capabilities",
    "pages",
    "jobs",
    "job_attempts",
    "dictionary_projection_requests",
    "dictionary_projections",
    "dictionary_projection_sources",
    "dictionary_projection_items",
    "dictionary_projection_meanings",
    "ocr_runs",
    "ocr_regions",
    "ocr_region_vertices",
    "linguistic_runs",
    "linguistic_tokens",
    "linguistic_meanings",
    "lexical_matches",
    "lexical_meanings",
    "gemini_analyses",
    "gemini_region_analyses",
    "gemini_calls",
    "gemini_grammar_points",
    "gemini_vocabulary_links",
    "gemini_lexical_vocabulary_links",
    "gemini_cost_ledger",
    "gemini_budget_buckets",
    "image_blobs",
    "page_capabilities",
    "rate_limit_buckets",
)


@pytest.fixture
def clean_postgres_url(migrated_postgres_url: str) -> str:
    tables = ", ".join(f"mangasensei.{name}" for name in _MANGASENSEI_TABLES)
    engine = create_engine(migrated_postgres_url)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {tables} CASCADE"))
    engine.dispose()
    return migrated_postgres_url

"""Command-line entry point for all MangaSensei runtime roles."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from alembic import command as alembic_command
from alembic.config import Config
from pydantic import ValidationError
from pydantic_settings import SettingsError

from mangasensei.api.app import create_app
from mangasensei.config import Settings
from mangasensei.linguistics.jmdict import DictionaryDataError
from mangasensei.linguistics.jmdict_bootstrap import JmdictIntegrityError
from mangasensei.linguistics.jmdict_packs import (
    DEFAULT_DICTIONARY_LANGUAGE,
    download_jmdict_pack,
    verify_jmdict_pack,
)
from mangasensei.ocr.models.downloader import download_models, verify_models
from mangasensei.ocr.models.manifest import ModelIntegrityError
from mangasensei.runtime import run_retention_process, run_worker_process


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mangasensei")
    subcommands = parser.add_subparsers(dest="command", required=True)

    api = subcommands.add_parser("api", help="start the API and web application")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=_port, default=8000)

    worker = subcommands.add_parser("worker", help="run the analysis worker")
    worker.add_argument("--once", action="store_true")

    retention = subcommands.add_parser("retention", help="remove expired pages")
    retention.add_argument("--once", action="store_true")

    models = subcommands.add_parser("models", help="manage local OCR model files")
    model_commands = models.add_subparsers(dest="models_command", required=True)
    model_commands.add_parser("download", help="download and verify reviewed artifacts")
    model_commands.add_parser("verify", help="verify local artifact size and checksum")

    jmdict = subcommands.add_parser("jmdict", help="manage reviewed local JMdict packs")
    jmdict_commands = jmdict.add_subparsers(dest="jmdict_command", required=True)
    jmdict_download = jmdict_commands.add_parser(
        "download", help="download, normalize and verify a reviewed JMdict pack"
    )
    jmdict_download.add_argument("--language", default=DEFAULT_DICTIONARY_LANGUAGE)
    jmdict_verify = jmdict_commands.add_parser(
        "verify", help="verify a reviewed local JMdict pack"
    )
    jmdict_verify.add_argument("--language", default=DEFAULT_DICTIONARY_LANGUAGE)

    migrate = subcommands.add_parser("migrate", help="upgrade the database schema")
    migrate.add_argument("--config", type=Path, default=Path("backend/alembic.ini"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings()
        if args.command == "api":
            uvicorn.run(
                create_app(settings),
                host=args.host,
                port=args.port,
                log_level="info",
                proxy_headers=False,
                server_header=False,
            )
        elif args.command == "worker":
            logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
            asyncio.run(run_worker_process(settings, once=args.once))
        elif args.command == "retention":
            asyncio.run(run_retention_process(settings, once=args.once))
        elif args.command == "models" and args.models_command == "download":
            asyncio.run(download_models(settings.model_cache))
        elif args.command == "models" and args.models_command == "verify":
            verify_models(settings.model_cache)
        elif args.command == "jmdict" and args.jmdict_command == "download":
            asyncio.run(
                download_jmdict_pack(
                    settings.jmdict_path,
                    language=args.language,
                )
            )
        elif args.command == "jmdict" and args.jmdict_command == "verify":
            verify_jmdict_pack(settings.jmdict_path, language=args.language)
        elif args.command == "migrate":
            _migrate(settings, args.config)
        return 0
    except KeyboardInterrupt:
        return 130
    except (
        DictionaryDataError,
        FileNotFoundError,
        JmdictIntegrityError,
        ModelIntegrityError,
        SettingsError,
        TimeoutError,
        ValidationError,
        ValueError,
    ) as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def _migrate(settings: Settings, config_path: Path) -> None:
    database_url = settings.require_database_url()
    config = Config(config_path)
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    alembic_command.upgrade(config, "head")


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65_535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port

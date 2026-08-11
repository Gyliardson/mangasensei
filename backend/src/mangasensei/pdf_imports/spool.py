"""Filesystem-only handoff for the isolated PDF renderer boundary."""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel


class PdfSpoolError(RuntimeError):
    """The renderer spool violated its fixed path/integrity contract."""


class PdfSpool:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.requests = self.root / "requests"
        self.imports = self.root / "imports"
        self.renderer = self.root / "renderer"
        for directory in (self.root, self.requests, self.imports, self.renderer):
            directory.mkdir(parents=True, exist_ok=True)
            if directory.is_symlink():
                raise PdfSpoolError("PDF spool directory must not be a symlink")

    def import_dir(self, import_id: UUID) -> Path:
        return self.imports / str(import_id)

    def source_path(self, import_id: UUID) -> Path:
        return self.import_dir(import_id) / "source.pdf"

    def attempt_dir(self, import_id: UUID, fencing_token: int) -> Path:
        if fencing_token < 1:
            raise ValueError("fencing token must be positive")
        return self.import_dir(import_id) / f"attempt-{fencing_token}"

    def request_path(self, import_id: UUID, fencing_token: int) -> Path:
        return self.requests / f"{import_id}.{fencing_token}.request.json"

    def manifest_path(self, import_id: UUID, fencing_token: int) -> Path:
        return self.attempt_dir(import_id, fencing_token) / "manifest.json"

    def failure_path(self, import_id: UUID, fencing_token: int) -> Path:
        return self.attempt_dir(import_id, fencing_token) / "failure.json"

    def heartbeat_path(self) -> Path:
        return self.renderer / "heartbeat.json"

    def prepare_import_dir(self, import_id: UUID) -> Path:
        directory = self.import_dir(import_id)
        directory.mkdir(parents=True, exist_ok=True)
        self._require_directory(directory)
        return directory

    def prepare_attempt_dir(self, import_id: UUID, fencing_token: int) -> Path:
        directory = self.attempt_dir(import_id, fencing_token)
        directory.mkdir(parents=True, exist_ok=True)
        self._require_directory(directory)
        return directory

    def write_model_atomic(self, path: Path, model: BaseModel) -> None:
        self._require_within_root(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._require_directory(path.parent)
        payload = model.model_dump_json(exclude_none=True).encode("utf-8") + b"\n"
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)
        os.replace(temporary, path)

    def read_json(self, path: Path, *, max_bytes: int = 2 * 1024 * 1024) -> object:
        self.require_regular_file(path, max_bytes=max_bytes)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PdfSpoolError("invalid PDF spool JSON") from exc

    def require_regular_file(self, path: Path, *, max_bytes: int | None = None) -> os.stat_result:
        self._require_within_root(path)
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise PdfSpoolError("PDF spool file is missing") from exc
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise PdfSpoolError("PDF spool entry must be a regular file")
        if metadata.st_nlink != 1:
            raise PdfSpoolError("PDF spool files must not be hard-linked")
        if max_bytes is not None and metadata.st_size > max_bytes:
            raise PdfSpoolError("PDF spool file exceeds its bounded size")
        return metadata

    def page_path(self, import_id: UUID, fencing_token: int, filename: str) -> Path:
        if not filename.startswith("page-") or not filename.endswith(".png"):
            raise PdfSpoolError("invalid raster filename")
        middle = filename.removeprefix("page-").removesuffix(".png")
        if len(middle) != 6 or not middle.isdecimal():
            raise PdfSpoolError("invalid raster filename")
        return self.attempt_dir(import_id, fencing_token) / filename

    def remove_import(self, import_id: UUID) -> None:
        directory = self.import_dir(import_id)
        self._require_within_root(directory)
        if not directory.exists() and not directory.is_symlink():
            return
        if directory.is_symlink():
            directory.unlink(missing_ok=True)
            return
        shutil.rmtree(directory)

    def remove_attempt(self, import_id: UUID, fencing_token: int) -> None:
        directory = self.attempt_dir(import_id, fencing_token)
        self._require_within_root(directory)
        if not directory.exists() and not directory.is_symlink():
            return
        if directory.is_symlink():
            directory.unlink(missing_ok=True)
            return
        shutil.rmtree(directory)

    def _require_directory(self, path: Path) -> None:
        self._require_within_root(path)
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
            raise PdfSpoolError("PDF spool directory must be a real directory")

    def _require_within_root(self, path: Path) -> None:
        try:
            path.absolute().relative_to(self.root)
        except ValueError as exc:
            raise PdfSpoolError("PDF spool path escaped its root") from exc

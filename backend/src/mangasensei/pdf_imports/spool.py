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
        self.root.mkdir(parents=True, exist_ok=True)
        self._require_directory(self.root)
        for directory in (self.requests, self.imports, self.renderer):
            self._prepare_child_directory(directory)

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
        self._prepare_child_directory(directory)
        return directory

    def prepare_attempt_dir(self, import_id: UUID, fencing_token: int) -> Path:
        directory = self.attempt_dir(import_id, fencing_token)
        self._require_directory(directory.parent)
        self._prepare_child_directory(directory)
        return directory

    def write_model_atomic(self, path: Path, model: BaseModel) -> None:
        self._require_within_root(path)
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
        self._require_directory(path.parent)
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
        self.remove_requests(import_id)
        self._require_directory(self.imports)
        directory = self.import_dir(import_id)
        self._require_within_root(directory)
        if not directory.exists() and not directory.is_symlink():
            return
        if directory.is_symlink():
            directory.unlink(missing_ok=True)
            return
        self._require_directory(directory)
        shutil.rmtree(directory)

    def remove_requests(self, import_id: UUID) -> None:
        self._require_directory(self.requests)
        prefix = f"{import_id}."
        for request in self.requests.glob(f"{import_id}.*.request.json"):
            if not request.name.startswith(prefix):
                continue
            try:
                self.require_regular_file(request, max_bytes=2 * 1024 * 1024)
            except PdfSpoolError:
                if request.is_symlink():
                    request.unlink(missing_ok=True)
                continue
            request.unlink(missing_ok=True)

    def remove_attempt(self, import_id: UUID, fencing_token: int) -> None:
        self._require_directory(self.import_dir(import_id))
        directory = self.attempt_dir(import_id, fencing_token)
        self._require_within_root(directory)
        if not directory.exists() and not directory.is_symlink():
            return
        if directory.is_symlink():
            directory.unlink(missing_ok=True)
            return
        self._require_directory(directory)
        shutil.rmtree(directory)

    def _prepare_child_directory(self, path: Path) -> None:
        self._require_within_root(path)
        self._require_directory(path.parent)
        try:
            path.mkdir(exist_ok=True)
        except FileExistsError:
            pass
        self._require_directory(path)

    def _require_directory(self, path: Path) -> None:
        self._require_within_root(path)
        self._require_safe_parent_chain(path)
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise PdfSpoolError("PDF spool directory is missing") from exc
        if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
            raise PdfSpoolError("PDF spool directory must be a real directory")

    def _require_safe_parent_chain(self, path: Path) -> None:
        relative = self._relative_to_root(path)
        current = self.root
        for part in relative.parts[:-1]:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError as exc:
                raise PdfSpoolError("PDF spool parent directory is missing") from exc
            if not stat.S_ISDIR(metadata.st_mode) or current.is_symlink():
                raise PdfSpoolError("PDF spool parent directory must be a real directory")

    def _require_within_root(self, path: Path) -> None:
        self._relative_to_root(path)

    def _relative_to_root(self, path: Path) -> Path:
        try:
            return path.absolute().relative_to(self.root)
        except ValueError as exc:
            raise PdfSpoolError("PDF spool path escaped its root") from exc

"""Filesystem handoff across the isolated PDF renderer trust boundary."""

from __future__ import annotations

import errno
import json
import os
import stat
from contextlib import suppress
from pathlib import Path, PosixPath
from uuid import UUID

from pydantic import BaseModel

_INPUT_ROOT_MODE = 0o750
_INPUT_DIRECTORY_MODE = 0o750
_STAGING_DIRECTORY_MODE = 0o700
_CHANNEL_ROOT_MODE = 0o710
_CHANNEL_DIRECTORY_MODE = 0o710
_RENDERER_WRITABLE_DIRECTORY_MODE = 0o2730
_SHARED_FILE_MODE = 0o640
_READ_CHUNK_BYTES = 1024 * 1024


class PdfSpoolError(RuntimeError):
    """The PDF spool violated its fixed path/integrity contract."""


class _PinnedRendererOutputPath(PosixPath):
    """Path facade whose privileged read returns bytes pinned by PdfSpool validation."""

    __slots__ = ("_spool", "_pinned_content")
    _spool: PdfSpool
    _pinned_content: bytes | None

    def bind(self, spool: PdfSpool) -> _PinnedRendererOutputPath:
        self._spool = spool
        self._pinned_content = None
        return self

    def exists(self) -> bool:
        spool = getattr(self, "_spool", None)
        if spool is None:
            raise PdfSpoolError("unbound renderer output path")
        return spool.output_file_exists(Path(self))

    def read_bytes(self) -> bytes:
        content = getattr(self, "_pinned_content", None)
        if content is not None:
            return content
        spool = getattr(self, "_spool", None)
        if spool is not None and not spool.split_output:
            return Path(self).read_bytes()
        raise PdfSpoolError("renderer output must be validated before consumption")


class PdfSpool:
    """Split trusted renderer input from the renderer-owned output channel."""

    def __init__(
        self,
        input_root: Path,
        output_root: Path | None = None,
        *,
        initialize: bool = True,
    ) -> None:
        self.root = input_root.absolute()
        self.staging = self.root / "staging"
        self.requests = self.root / "requests"
        self.imports = self.root / "imports"
        configured_output = output_root
        if configured_output is None:
            env_output = os.environ.get("MANGASENSEI_PDF_RENDERER_OUTPUT_ROOT")
            configured_output = Path(env_output) if env_output else self.root
        self.output_root = configured_output.absolute()
        self.output_imports = self.output_root / "imports"
        self.renderer = self.output_root / "renderer"
        self._split_output = self.output_root != self.root
        self._trusted_uid = os.geteuid()
        if initialize and self.root.exists() and self.root.lstat().st_uid != os.geteuid():
            initialize = False
        if initialize:
            self.initialize_layout()
        else:
            self._trusted_uid = self._existing_layout_owner()
            self._validate_existing_layout()

    @property
    def trusted_uid(self) -> int:
        return self._trusted_uid

    @property
    def split_output(self) -> bool:
        return self._split_output

    def initialize_layout(self) -> None:
        self._prepare_trusted_directory(self.root, _INPUT_ROOT_MODE, create_parents=True)
        self._prepare_trusted_directory(self.staging, _STAGING_DIRECTORY_MODE)
        self._prepare_trusted_directory(self.requests, _INPUT_DIRECTORY_MODE)
        self._prepare_trusted_directory(self.imports, _INPUT_DIRECTORY_MODE)
        if self._split_output:
            self._prepare_trusted_directory(
                self.output_root, _CHANNEL_ROOT_MODE, create_parents=True
            )
            self._prepare_trusted_directory(self.output_imports, _CHANNEL_DIRECTORY_MODE)
        self._prepare_trusted_directory(self.renderer, _RENDERER_WRITABLE_DIRECTORY_MODE)

    def import_dir(self, import_id: UUID) -> Path:
        return self.imports / str(import_id)

    def output_import_dir(self, import_id: UUID) -> Path:
        return self.output_root / "imports" / str(import_id)

    def source_path(self, import_id: UUID) -> Path:
        return self.import_dir(import_id) / "source.pdf"

    def attempt_dir(self, import_id: UUID, fencing_token: int) -> Path:
        if fencing_token < 1:
            raise ValueError("fencing token must be positive")
        return self.output_import_dir(import_id) / f"attempt-{fencing_token}"

    def request_path(self, import_id: UUID, fencing_token: int) -> Path:
        if fencing_token < 1:
            raise ValueError("fencing token must be positive")
        return self.requests / f"{import_id}.{fencing_token}.request.json"

    def manifest_path(self, import_id: UUID, fencing_token: int) -> Path:
        return self._pinned_output_path(
            self.attempt_dir(import_id, fencing_token) / "manifest.json"
        )

    def failure_path(self, import_id: UUID, fencing_token: int) -> Path:
        return self._pinned_output_path(
            self.attempt_dir(import_id, fencing_token) / "failure.json"
        )

    def heartbeat_path(self) -> Path:
        return self._pinned_output_path(self.renderer / "heartbeat.json")

    def prepare_import_dir(self, import_id: UUID) -> Path:
        directory = self.import_dir(import_id)
        self._prepare_trusted_directory(directory, _INPUT_DIRECTORY_MODE)
        return directory

    def prepare_attempt_dir(self, import_id: UUID, fencing_token: int) -> Path:
        output_import = self.output_import_dir(import_id)
        directory = self.attempt_dir(import_id, fencing_token)
        if os.geteuid() != self._trusted_uid:
            self._require_trusted_directory(output_import, allow_renderer_write=False)
            self._require_trusted_directory(directory, allow_renderer_write=True)
            return directory
        self._prepare_trusted_directory(output_import, _CHANNEL_DIRECTORY_MODE)
        self._prepare_trusted_directory(directory, _RENDERER_WRITABLE_DIRECTORY_MODE)
        return directory

    def write_bytes_exclusive(self, path: Path, content: bytes) -> None:
        parent_fd = self._open_parent_directory(path, for_output=self._is_output_path(path))
        descriptor: int | None = None
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | self._nofollow_flag()
            descriptor = os.open(path.name, flags, _SHARED_FILE_MODE, dir_fd=parent_fd)
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent_fd)

    def write_model_atomic(self, path: Path, model: BaseModel) -> None:
        payload = model.model_dump_json(exclude_none=True).encode("utf-8") + b"\n"
        parent_fd = self._open_parent_directory(path, for_output=self._is_output_path(path))
        temporary_name = f".{path.name}.{os.getpid()}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | self._nofollow_flag()
        descriptor: int | None = None
        try:
            descriptor = os.open(temporary_name, flags, _SHARED_FILE_MODE, dir_fd=parent_fd)
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            with suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=parent_fd)
            os.close(parent_fd)

    def read_json(self, path: Path, *, max_bytes: int = 2 * 1024 * 1024) -> object:
        content = self.read_bytes(path, max_bytes=max_bytes)
        try:
            return json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PdfSpoolError("invalid PDF spool JSON") from exc

    def read_bytes(self, path: Path, *, max_bytes: int) -> bytes:
        content, _ = self._read_bytes_with_metadata(path, max_bytes=max_bytes)
        return content

    def _read_bytes_with_metadata(
        self, path: Path, *, max_bytes: int
    ) -> tuple[bytes, os.stat_result]:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        output = self._is_output_path(path)
        parent_fd = self._open_parent_directory(path, for_output=output)
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY | os.O_NONBLOCK | self._nofollow_flag()
            descriptor = os.open(path.name, flags, dir_fd=parent_fd)
            metadata = self._validate_regular_descriptor(descriptor, max_bytes=max_bytes)
            content = bytearray()
            while True:
                remaining = max_bytes + 1 - len(content)
                chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise PdfSpoolError("PDF spool file exceeds its bounded size")
            final_metadata = self._validate_regular_descriptor(descriptor, max_bytes=max_bytes)
            if (metadata.st_dev, metadata.st_ino) != (final_metadata.st_dev, final_metadata.st_ino):
                raise PdfSpoolError("PDF spool inode changed during consumption")
            return bytes(content), final_metadata
        except (OSError, PdfSpoolError) as exc:
            if isinstance(exc, PdfSpoolError):
                raise
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise PdfSpoolError("PDF spool entry must be a regular file") from exc
            raise PdfSpoolError("PDF spool file could not be opened safely") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent_fd)

    def require_regular_file(self, path: Path, *, max_bytes: int | None = None) -> os.stat_result:
        if isinstance(path, _PinnedRendererOutputPath) and max_bytes is not None:
            content, metadata = self._read_bytes_with_metadata(Path(path), max_bytes=max_bytes)
            path._pinned_content = content
            return metadata
        parent_fd = self._open_parent_directory(path, for_output=self._is_output_path(path))
        descriptor: int | None = None
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_NONBLOCK | self._nofollow_flag(),
                dir_fd=parent_fd,
            )
            metadata = self._validate_regular_descriptor(descriptor, max_bytes=max_bytes)
            if os.geteuid() == self._trusted_uid and self._is_source_path(path):
                os.fchmod(descriptor, _SHARED_FILE_MODE)
            return metadata
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise PdfSpoolError("PDF spool entry must be a regular file") from exc
            raise PdfSpoolError("PDF spool file could not be opened safely") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent_fd)

    def output_file_exists(self, path: Path) -> bool:
        if not self._is_output_path(path):
            raise PdfSpoolError("output existence checks require the renderer output channel")
        parent_fd = self._open_parent_directory(path, for_output=True)
        try:
            try:
                metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return False
            return stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)
        finally:
            os.close(parent_fd)

    def page_path(self, import_id: UUID, fencing_token: int, filename: str) -> Path:
        if not filename.startswith("page-") or not filename.endswith(".png"):
            raise PdfSpoolError("invalid raster filename")
        middle = filename.removeprefix("page-").removesuffix(".png")
        if len(middle) != 6 or not middle.isdecimal():
            raise PdfSpoolError("invalid raster filename")
        return self._pinned_output_path(
            self.attempt_dir(import_id, fencing_token) / filename
        )

    def remove_import(self, import_id: UUID) -> None:
        self.remove_requests(import_id)
        self._remove_tree(self.imports, str(import_id), for_output=False)
        if self._split_output:
            self._remove_tree(self.output_imports, str(import_id), for_output=True)

    def remove_requests(self, import_id: UUID) -> None:
        directory_fd = self._open_input_directory(self.requests)
        prefix = f"{import_id}."
        suffix = ".request.json"
        try:
            for name in os.listdir(directory_fd):
                if name.startswith(prefix) and name.endswith(suffix):
                    with suppress(FileNotFoundError):
                        os.unlink(name, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)

    def remove_request(self, import_id: UUID, fencing_token: int) -> None:
        directory_fd = self._open_input_directory(self.requests)
        try:
            with suppress(FileNotFoundError):
                os.unlink(self.request_path(import_id, fencing_token).name, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)

    def remove_attempt(self, import_id: UUID, fencing_token: int) -> None:
        output_import = self.output_import_dir(import_id)
        self._remove_tree(output_import, f"attempt-{fencing_token}", for_output=True)

    def _remove_tree(self, parent: Path, name: str, *, for_output: bool) -> None:
        parent_fd = self._open_parent_directory(
            parent / name,
            for_output=for_output,
            renderer_writable_parent=False if for_output else None,
        )
        try:
            self._remove_entry_at(parent_fd, name)
        finally:
            os.close(parent_fd)

    def _remove_entry_at(self, parent_fd: int, name: str) -> None:
        directory_fd: int | None = None
        try:
            directory_fd = os.open(
                name,
                os.O_RDONLY | self._directory_flag() | self._nofollow_flag(),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            return
        except OSError as exc:
            if exc.errno not in (errno.ELOOP, errno.ENOTDIR):
                raise
            with suppress(FileNotFoundError):
                os.unlink(name, dir_fd=parent_fd)
            return

        try:
            for child in os.listdir(directory_fd):
                self._remove_entry_at(directory_fd, child)
        finally:
            os.close(directory_fd)
        with suppress(FileNotFoundError):
            os.rmdir(name, dir_fd=parent_fd)

    def _pinned_output_path(self, path: Path) -> _PinnedRendererOutputPath:
        return _PinnedRendererOutputPath(path).bind(self)

    def _existing_layout_owner(self) -> int:
        owners: set[int] = set()
        for root in {self.root, self.output_root}:
            try:
                metadata = root.lstat()
            except FileNotFoundError as exc:
                raise PdfSpoolError("PDF spool root is missing") from exc
            if not stat.S_ISDIR(metadata.st_mode) or root.is_symlink():
                raise PdfSpoolError("PDF spool root must be a real directory")
            owners.add(metadata.st_uid)
        if len(owners) != 1:
            raise PdfSpoolError("PDF spool roots must share the trusted coordinator owner")
        return owners.pop()

    def _validate_existing_layout(self) -> None:
        self._require_trusted_directory(self.root, allow_renderer_write=False)
        self._require_trusted_directory(self.staging, allow_renderer_write=False)
        self._require_trusted_directory(self.requests, allow_renderer_write=False)
        self._require_trusted_directory(self.imports, allow_renderer_write=False)
        if self._split_output:
            self._require_trusted_directory(self.output_root, allow_renderer_write=False)
            self._require_trusted_directory(self.output_imports, allow_renderer_write=False)
        self._require_trusted_directory(self.renderer, allow_renderer_write=True)

    def _prepare_trusted_directory(
        self,
        path: Path,
        mode: int,
        *,
        create_parents: bool = False,
    ) -> None:
        root = self.output_root if self._is_output_path(path) else self.root
        if path != root:
            self._require_trusted_directory(path.parent, allow_renderer_write=False)
        path.mkdir(parents=create_parents, mode=mode, exist_ok=True)
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
            raise PdfSpoolError("PDF spool directory must be a real directory")
        if metadata.st_uid != self._trusted_uid:
            raise PdfSpoolError("PDF spool trusted directory has the wrong owner")
        os.chmod(path, mode)

    def _require_trusted_directory(
        self, path: Path, *, allow_renderer_write: bool
    ) -> os.stat_result:
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise PdfSpoolError("PDF spool directory is missing") from exc
        if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
            raise PdfSpoolError("PDF spool directory must be a real directory")
        if metadata.st_uid != self._trusted_uid:
            raise PdfSpoolError("PDF spool trusted directory has the wrong owner")
        if not allow_renderer_write and metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise PdfSpoolError("PDF spool trusted directory is renderer-writable")
        return metadata

    def _open_input_directory(self, path: Path) -> int:
        relative = self._relative_to(self.root, path)
        return self._open_directory_chain(
            self.root,
            relative.parts,
            trusted=True,
            allow_renderer_writable_leaf=False,
            path_only=False,
        )

    def _open_parent_directory(
        self,
        path: Path,
        *,
        for_output: bool,
        renderer_writable_parent: bool | None = None,
    ) -> int:
        if for_output:
            relative = self._relative_to(self.output_root, path)
            if renderer_writable_parent is None:
                renderer_writable_parent = self._is_renderer_writable_parent(relative.parent)
            return self._open_directory_chain(
                self.output_root,
                relative.parts[:-1],
                trusted=True,
                allow_renderer_writable_leaf=renderer_writable_parent,
                path_only=True,
            )
        relative = self._relative_to(self.root, path)
        return self._open_directory_chain(
            self.root,
            relative.parts[:-1],
            trusted=True,
            allow_renderer_writable_leaf=False,
            path_only=False,
        )

    def _open_directory_chain(
        self,
        root: Path,
        parts: tuple[str, ...],
        *,
        trusted: bool,
        allow_renderer_writable_leaf: bool,
        path_only: bool,
    ) -> int:
        flags = (
            self._path_only_flag() if path_only else os.O_RDONLY
        ) | self._directory_flag() | self._nofollow_flag()
        try:
            descriptor = os.open(root, flags)
        except OSError as exc:
            raise PdfSpoolError("PDF spool root could not be opened safely") from exc
        try:
            if trusted:
                self._validate_trusted_descriptor(descriptor, allow_renderer_write=False)
            for index, part in enumerate(parts):
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
                if trusted:
                    self._validate_trusted_descriptor(
                        descriptor,
                        allow_renderer_write=(
                            allow_renderer_writable_leaf and index == len(parts) - 1
                        ),
                    )
            return descriptor
        except (OSError, PdfSpoolError) as exc:
            os.close(descriptor)
            if isinstance(exc, PdfSpoolError):
                raise
            raise PdfSpoolError("PDF spool parent path could not be opened safely") from exc

    def _validate_trusted_descriptor(self, descriptor: int, *, allow_renderer_write: bool) -> None:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise PdfSpoolError("PDF spool parent must be a directory")
        if metadata.st_uid != self._trusted_uid:
            raise PdfSpoolError("PDF spool trusted parent has the wrong owner")
        if not allow_renderer_write and metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise PdfSpoolError("PDF spool trusted parent is renderer-writable")

    @staticmethod
    def _validate_regular_descriptor(
        descriptor: int, *, max_bytes: int | None
    ) -> os.stat_result:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PdfSpoolError("PDF spool entry must be a regular file")
        if metadata.st_nlink > 1:
            raise PdfSpoolError("PDF spool files must not be hard-linked")
        if max_bytes is not None and metadata.st_size > max_bytes:
            raise PdfSpoolError("PDF spool file exceeds its bounded size")
        return metadata

    @staticmethod
    def _is_renderer_writable_parent(relative_parent: Path) -> bool:
        parts = relative_parent.parts
        if parts == ("renderer",):
            return True
        return len(parts) == 3 and parts[0] == "imports" and parts[2].startswith("attempt-")

    def _is_source_path(self, path: Path) -> bool:
        try:
            relative = self._relative_to(self.root, path)
        except PdfSpoolError:
            return False
        parts = relative.parts
        return len(parts) == 3 and parts[0] == "imports" and parts[2] == "source.pdf"

    def _is_output_path(self, path: Path) -> bool:
        try:
            self._relative_to(self.output_root, path)
        except PdfSpoolError:
            return False
        return True

    def _require_input_path(self, path: Path) -> None:
        self._relative_to(self.root, path)

    @staticmethod
    def _relative_to(root: Path, path: Path) -> Path:
        try:
            relative = path.absolute().relative_to(root)
        except ValueError as exc:
            raise PdfSpoolError("PDF spool path escaped its root") from exc
        if any(part in {".", ".."} for part in relative.parts):
            raise PdfSpoolError("PDF spool path escaped its root")
        return relative

    @staticmethod
    def _nofollow_flag() -> int:
        if not hasattr(os, "O_NOFOLLOW"):
            raise PdfSpoolError("secure no-follow filesystem operations are unavailable")
        return os.O_NOFOLLOW

    @staticmethod
    def _path_only_flag() -> int:
        if not hasattr(os, "O_PATH"):
            raise PdfSpoolError("secure path-only directory operations are unavailable")
        return os.O_PATH

    @staticmethod
    def _directory_flag() -> int:
        if not hasattr(os, "O_DIRECTORY"):
            raise PdfSpoolError("secure directory filesystem operations are unavailable")
        return os.O_DIRECTORY

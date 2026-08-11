from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pypdfium2 as pdfium

import mangasensei.pdf_imports.renderer as renderer_module
from mangasensei.config import Settings
from mangasensei.pdf_imports.contracts import PdfRasterManifest, PdfRenderFailure, PdfRenderRequest
from mangasensei.pdf_imports.renderer import PdfRenderer
from mangasensei.pdf_imports.spool import PdfSpool


def _pdf_bytes(path: Path) -> bytes:
    document = pdfium.PdfDocument.new()
    try:
        page = document.new_page(72, 72)
        page.close()
        document.save(path)
    finally:
        document.close()
    return path.read_bytes()


def _request(spool: PdfSpool, settings: Settings, content: bytes) -> PdfRenderRequest:
    import_id = uuid4()
    spool.prepare_import_dir(import_id)
    spool.source_path(import_id).write_bytes(content)
    return PdfRenderRequest(
        import_id=import_id,
        fencing_token=1,
        source_sha256=hashlib.sha256(content).hexdigest(),
        max_pages=settings.max_pdf_pages,
        max_side=settings.max_image_side,
        max_page_pixels=settings.max_image_pixels,
        max_aggregate_pixels=settings.max_document_pixels,
        max_page_raster_bytes=settings.max_upload_bytes,
        max_aggregate_raster_bytes=settings.max_pdf_raster_bytes,
        max_spool_bytes=settings.max_pdf_spool_bytes,
    )


def test_run_once_renders_in_disposable_child_process(tmp_path: Path) -> None:
    settings = Settings(environment="test", pdf_spool_root=tmp_path / "spool")
    spool = PdfSpool(settings.pdf_spool_root)
    request = _request(spool, settings, _pdf_bytes(tmp_path / "one-page.pdf"))
    request_path = spool.request_path(request.import_id, request.fencing_token)
    spool.write_model_atomic(request_path, request)

    assert PdfRenderer(settings).run_once() is True

    manifest = PdfRasterManifest.model_validate(
        spool.read_json(spool.manifest_path(request.import_id, request.fencing_token))
    )
    assert manifest.page_count == 1
    assert not request_path.exists()


class _FakeProcess:
    def __init__(self, *, alive: bool, exitcode: int | None) -> None:
        self._alive = alive
        self.exitcode = exitcode
        self.terminated = False
        self.killed = False

    def start(self) -> None:
        return None

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        del timeout

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False
        self.exitcode = -15

    def kill(self) -> None:
        self.killed = True
        self._alive = False
        self.exitcode = -9

    def close(self) -> None:
        return None


class _FakeContext:
    def __init__(self, process: _FakeProcess) -> None:
        self._process = process

    def Process(self, **_: Any) -> _FakeProcess:  # noqa: N802 - mirrors multiprocessing API
        return self._process


def test_supervisor_kills_child_at_wall_clock_deadline(tmp_path: Path, monkeypatch: Any) -> None:
    settings = Settings(environment="test", pdf_spool_root=tmp_path / "spool")
    spool = PdfSpool(settings.pdf_spool_root)
    request = _request(spool, settings, b"%PDF-1.7\n")
    request_path = spool.request_path(request.import_id, request.fencing_token)
    spool.write_model_atomic(request_path, request)
    process = _FakeProcess(alive=True, exitcode=None)
    monotonic = iter((0.0, 181.0))
    monkeypatch.setattr(renderer_module.multiprocessing, "get_context", lambda _: _FakeContext(process))
    monkeypatch.setattr(renderer_module.time, "monotonic", lambda: next(monotonic))

    PdfRenderer(settings)._process(request_path, request)

    failure = PdfRenderFailure.model_validate(
        spool.read_json(spool.failure_path(request.import_id, request.fencing_token))
    )
    assert failure.error_code == "pdf_renderer_timeout"
    assert process.terminated is True
    assert process.killed is False
    assert not request_path.exists()


def test_supervisor_maps_abnormal_child_exit_to_crash(tmp_path: Path, monkeypatch: Any) -> None:
    settings = Settings(environment="test", pdf_spool_root=tmp_path / "spool")
    spool = PdfSpool(settings.pdf_spool_root)
    request = _request(spool, settings, b"%PDF-1.7\n")
    request_path = spool.request_path(request.import_id, request.fencing_token)
    spool.write_model_atomic(request_path, request)
    process = _FakeProcess(alive=False, exitcode=-9)
    monkeypatch.setattr(renderer_module.multiprocessing, "get_context", lambda _: _FakeContext(process))

    PdfRenderer(settings)._process(request_path, request)

    failure = PdfRenderFailure.model_validate(
        spool.read_json(spool.failure_path(request.import_id, request.fencing_token))
    )
    assert failure.error_code == "pdf_renderer_crash"
    assert not request_path.exists()

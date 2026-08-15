"""Sequential fail-closed PDFium renderer used only by the isolated renderer role."""

from __future__ import annotations

import errno
import hashlib
import math
import multiprocessing
import os
import re
import socket
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pypdfium2_raw
from PIL import __version__ as pillow_version
from pydantic import ValidationError
from pypdfium2._helpers.misc import PdfiumError
from pypdfium2.version import PDFIUM_INFO, PYPDFIUM_INFO

from mangasensei.config import Settings
from mangasensei.pdf_imports.contracts import (
    PDF_OUTPUT_BACKGROUND_RGBA,
    PDF_PNG_COMPRESS_LEVEL,
    PDF_PNG_OPTIMIZE,
    PDF_RASTER_CONTRACT_VERSION,
    PDF_RENDER_SCALE,
    PDFIUM_EXPECTED_BUILD,
    PYPDFIUM2_EXPECTED_VERSION,
    PdfImportErrorCode,
    PdfRasterManifest,
    PdfRasterPage,
    PdfRendererHeartbeat,
    PdfRendererProvenance,
    PdfRenderFailure,
    PdfRenderRequest,
)
from mangasensei.pdf_imports.spool import PdfSpool, PdfSpoolError

_REQUEST_RE = re.compile(
    r"^(?P<import>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\."
    r"(?P<fence>[1-9][0-9]*)\.request\.json$"
)
_PROCESS_HEARTBEAT_SECONDS = 1.0
_PROCESS_TERMINATE_GRACE_SECONDS = 2.0


class PdfRenderRejected(RuntimeError):
    def __init__(self, code: PdfImportErrorCode) -> None:
        super().__init__(code)
        self.code = code


def renderer_provenance() -> PdfRendererProvenance:
    """Fail if production resolved a system/source PDFium instead of the pinned binary wheel."""
    if PYPDFIUM_INFO.tag != PYPDFIUM2_EXPECTED_VERSION:
        raise RuntimeError("unexpected pypdfium2 helper version")
    if PDFIUM_INFO.build != PDFIUM_EXPECTED_BUILD:
        raise RuntimeError("unexpected bundled PDFium build")
    flags = tuple(sorted(str(flag) for flag in PDFIUM_INFO.flags))
    if "V8" in flags or "XFA" in flags:
        raise RuntimeError("renderer requires the standard non-V8/non-XFA PDFium build")

    raw_root = Path(pypdfium2_raw.__file__).resolve().parent
    native = raw_root / "libpdfium.so"
    if not native.is_file() or native.is_symlink():
        raise RuntimeError("bundled PDFium shared library is missing")
    try:
        native.resolve().relative_to(raw_root)
    except ValueError as exc:
        raise RuntimeError("PDFium resolved outside the binary wheel") from exc

    return PdfRendererProvenance(
        pypdfium2=PYPDFIUM_INFO.tag,
        pdfium=PDFIUM_INFO.tag,
        pdfium_build=PDFIUM_INFO.build,
        pdfium_flags=flags,
        pillow=pillow_version,
        native_library="pypdfium2_raw/libpdfium.so",
    )


def _render_request_child(
    settings_payload: dict[str, Any], request_payload: dict[str, Any]
) -> None:
    """Render one request in a disposable process so native hangs/crashes are containable."""
    settings = Settings.model_validate(settings_payload)
    request = PdfRenderRequest.model_validate(request_payload)
    renderer = PdfRenderer(settings)
    manifest_path = renderer._spool.manifest_path(request.import_id, request.fencing_token)
    failure_path = renderer._spool.failure_path(request.import_id, request.fencing_token)
    try:
        manifest = renderer._render(request)
        renderer._spool.write_model_atomic(manifest_path, manifest)
    except PdfRenderRejected as exc:
        renderer._write_failure_if_absent(request, exc.code)
    except OSError as exc:
        code: PdfImportErrorCode = (
            "pdf_temp_storage_exhausted" if exc.errno == errno.ENOSPC else "pdf_render_failed"
        )
        renderer._write_failure_if_absent(request, code)
    except Exception:
        renderer._write_failure_if_absent(request, "pdf_render_failed")
    finally:
        if not renderer._spool.output_file_exists(
            manifest_path
        ) and not renderer._spool.output_file_exists(failure_path):
            raise RuntimeError("renderer child exited without a terminal record")


class PdfRenderer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        output_value = os.environ.get("MANGASENSEI_PDF_RENDERER_OUTPUT_ROOT")
        if settings.environment == "production" and not output_value:
            raise RuntimeError("production PDF renderer requires a split output channel")
        output_root = Path(output_value) if output_value else None
        self._spool = PdfSpool(settings.pdf_spool_root, output_root)
        if output_root is not None:
            if not self._spool.split_output:
                raise RuntimeError("PDF renderer input and output roots must be distinct")
            if os.geteuid() == self._spool.trusted_uid:
                raise RuntimeError("PDF renderer must not share the coordinator Unix identity")
        self._provenance = renderer_provenance()
        self._instance_id = f"{socket.gethostname()}-{os.getpid()}"[:128]

    def run_forever(self, *, once: bool = False) -> None:
        while True:
            self._write_heartbeat()
            processed = self.run_once()
            if once:
                return
            if not processed:
                time.sleep(self._settings.pdf_import_poll_seconds)

    def run_once(self) -> bool:
        for path in sorted(self._spool.requests.glob("*.request.json"), key=lambda item: item.name):
            match = _REQUEST_RE.fullmatch(path.name)
            if match is None:
                continue
            try:
                request = PdfRenderRequest.model_validate(self._spool.read_json(path))
                if str(request.import_id) != match.group("import"):
                    raise PdfSpoolError("request import identity mismatch")
                if request.fencing_token != int(match.group("fence")):
                    raise PdfSpoolError("request fencing identity mismatch")
                if self._spool.split_output:
                    manifest_path = self._spool.manifest_path(
                        request.import_id, request.fencing_token
                    )
                    failure_path = self._spool.failure_path(
                        request.import_id, request.fencing_token
                    )
                    if self._spool.output_file_exists(
                        manifest_path
                    ) or self._spool.output_file_exists(failure_path):
                        continue
                self._process(path, request)
            except (PdfSpoolError, ValidationError, ValueError):
                if not self._spool.split_output:
                    path.unlink(missing_ok=True)
                    return True
                continue
            return True
        return False

    def _process(self, request_path: Path, request: PdfRenderRequest) -> None:
        self._spool.prepare_attempt_dir(request.import_id, request.fencing_token)
        manifest_path = self._spool.manifest_path(request.import_id, request.fencing_token)
        failure_path = self._spool.failure_path(request.import_id, request.fencing_token)
        if self._spool.output_file_exists(manifest_path) or self._spool.output_file_exists(
            failure_path
        ):
            if not self._spool.split_output:
                request_path.unlink(missing_ok=True)
            return

        context = multiprocessing.get_context("spawn")
        process = context.Process(
            target=_render_request_child,
            args=(self._settings.model_dump(), request.model_dump()),
            name=f"mangasensei-pdf-{request.import_id}",
            daemon=False,
        )
        try:
            process.start()
        except (OSError, RuntimeError):
            self._write_failure_if_absent(request, "pdf_renderer_crash")
        else:
            deadline = time.monotonic() + self._settings.pdf_renderer_timeout_seconds
            while process.is_alive() and time.monotonic() < deadline:
                remaining = max(0.0, deadline - time.monotonic())
                process.join(timeout=min(_PROCESS_HEARTBEAT_SECONDS, remaining))
                self._write_heartbeat()

            if process.is_alive():
                process.terminate()
                process.join(timeout=_PROCESS_TERMINATE_GRACE_SECONDS)
                if process.is_alive():
                    process.kill()
                    process.join()
                self._write_failure_if_absent(request, "pdf_renderer_timeout")
            elif (
                process.exitcode != 0
                and not self._spool.output_file_exists(manifest_path)
                and not self._spool.output_file_exists(failure_path)
            ):
                self._write_failure_if_absent(request, "pdf_renderer_crash")
            process.close()
        finally:
            if not self._spool.split_output:
                request_path.unlink(missing_ok=True)
            self._write_heartbeat()

    def _write_failure_if_absent(
        self, request: PdfRenderRequest, code: PdfImportErrorCode
    ) -> None:
        manifest_path = self._spool.manifest_path(request.import_id, request.fencing_token)
        failure_path = self._spool.failure_path(request.import_id, request.fencing_token)
        if self._spool.output_file_exists(manifest_path) or self._spool.output_file_exists(
            failure_path
        ):
            return
        self._spool.write_model_atomic(
            failure_path,
            PdfRenderFailure(
                import_id=request.import_id,
                fencing_token=request.fencing_token,
                error_code=code,
            ),
        )

    def _render(self, request: PdfRenderRequest) -> PdfRasterManifest:
        source = self._spool.source_path(request.import_id)
        source_meta = self._spool.require_regular_file(
            source, max_bytes=self._settings.max_pdf_bytes
        )
        if source_meta.st_size <= 0:
            raise PdfRenderRejected("pdf_invalid")
        if self._sha256_file(source) != request.source_sha256:
            raise PdfRenderRejected("pdf_invalid")

        try:
            document = pdfium.PdfDocument(source, password=None)
        except PdfiumError as exc:
            if getattr(exc, "err_code", None) == pdfium_c.FPDF_ERR_PASSWORD:
                raise PdfRenderRejected("pdf_encrypted_unsupported") from exc
            raise PdfRenderRejected("pdf_invalid") from exc

        pages: list[PdfRasterPage] = []
        aggregate_pixels = 0
        aggregate_raster_bytes = 0
        try:
            page_count = len(document)
            if page_count < 1:
                raise PdfRenderRejected("pdf_invalid")
            if page_count > request.max_pages:
                raise PdfRenderRejected("pdf_page_limit")

            for ordinal in range(page_count):
                page = document[ordinal]
                try:
                    bbox = self._validated_bbox(page.get_bbox())
                    page.set_cropbox(*bbox)
                    embedded_rotation = page.get_rotation()
                    if embedded_rotation not in (0, 90, 180, 270):
                        raise PdfRenderRejected("pdf_geometry_limit")
                    width_points, height_points = page.get_size()
                    if not all(
                        math.isfinite(value) and value > 0
                        for value in (width_points, height_points)
                    ):
                        raise PdfRenderRejected("pdf_geometry_limit")
                    width = math.ceil(width_points * PDF_RENDER_SCALE)
                    height = math.ceil(height_points * PDF_RENDER_SCALE)
                    if width > request.max_side or height > request.max_side:
                        raise PdfRenderRejected("pdf_geometry_limit")
                    pixels = width * height
                    if pixels > request.max_page_pixels:
                        raise PdfRenderRejected("pdf_pixel_limit")
                    aggregate_pixels += pixels
                    if aggregate_pixels > request.max_aggregate_pixels:
                        raise PdfRenderRejected("pdf_pixel_limit")

                    bitmap = page.render(
                        scale=PDF_RENDER_SCALE,
                        rotation=0,
                        crop=(0, 0, 0, 0),
                        may_draw_forms=False,
                        fill_color=PDF_OUTPUT_BACKGROUND_RGBA,
                        grayscale=False,
                        optimize_mode=None,
                        draw_annots=False,
                        no_smoothtext=False,
                        no_smoothimage=False,
                        no_smoothpath=False,
                        force_halftone=False,
                        limit_image_cache=True,
                        rev_byteorder=True,
                        prefer_bgrx=False,
                        maybe_alpha=False,
                        extra_flags=0,
                    )
                    try:
                        if bitmap.width != width or bitmap.height != height:
                            raise PdfRenderRejected("pdf_geometry_limit")
                        pil_image = bitmap.to_pil().convert("RGB").copy()
                    finally:
                        bitmap.close()

                    output = BytesIO()
                    pil_image.save(
                        output,
                        format="PNG",
                        compress_level=PDF_PNG_COMPRESS_LEVEL,
                        optimize=PDF_PNG_OPTIMIZE,
                    )
                    content = output.getvalue()
                    if len(content) > request.max_page_raster_bytes:
                        raise PdfRenderRejected("pdf_raster_bytes_limit")
                    aggregate_raster_bytes += len(content)
                    if aggregate_raster_bytes > request.max_aggregate_raster_bytes:
                        raise PdfRenderRejected("pdf_raster_bytes_limit")
                    if source_meta.st_size + aggregate_raster_bytes > request.max_spool_bytes:
                        raise PdfRenderRejected("pdf_temp_storage_exhausted")

                    filename = f"page-{ordinal + 1:06d}.png"
                    output_path = self._spool.page_path(
                        request.import_id, request.fencing_token, filename
                    )
                    self._spool.write_bytes_exclusive(output_path, content)
                    pages.append(
                        PdfRasterPage(
                            ordinal=ordinal,
                            filename=filename,
                            sha256=hashlib.sha256(content).hexdigest(),
                            byte_size=len(content),
                            width=width,
                            height=height,
                            embedded_rotation=embedded_rotation,
                            page_bbox=bbox,
                        )
                    )
                finally:
                    page.close()
        except PdfRenderRejected:
            raise
        except PdfiumError as exc:
            raise PdfRenderRejected("pdf_render_failed") from exc
        finally:
            document.close()

        return PdfRasterManifest(
            import_id=request.import_id,
            fencing_token=request.fencing_token,
            source_sha256=request.source_sha256,
            raster_contract=PDF_RASTER_CONTRACT_VERSION,
            page_count=len(pages),
            aggregate_pixels=aggregate_pixels,
            aggregate_raster_bytes=aggregate_raster_bytes,
            pages=tuple(pages),
            renderer=self._provenance,
        )

    @staticmethod
    def _validated_bbox(
        raw: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        left, bottom, right, top = (float(value) for value in raw)
        if not all(math.isfinite(value) for value in (left, bottom, right, top)):
            raise PdfRenderRejected("pdf_geometry_limit")
        if right <= left or top <= bottom:
            raise PdfRenderRejected("pdf_geometry_limit")
        return left, bottom, right, top

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _write_heartbeat(self) -> None:
        self._spool.write_model_atomic(
            self._spool.heartbeat_path(),
            PdfRendererHeartbeat(
                instance_id=self._instance_id,
                monotonic_ns=time.monotonic_ns(),
                renderer=self._provenance,
            ),
        )

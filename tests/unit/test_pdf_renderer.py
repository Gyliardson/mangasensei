from __future__ import annotations

import hashlib
import math
from pathlib import Path
from uuid import uuid4

import pypdfium2 as pdfium
import pytest

from mangasensei.config import Settings
from mangasensei.pdf_imports.contracts import PDF_RENDER_SCALE, PdfRenderRequest
from mangasensei.pdf_imports.renderer import PdfRenderer, PdfRenderRejected, renderer_provenance
from mangasensei.pdf_imports.spool import PdfSpool


def _write_pdf(
    path: Path,
    pages: list[tuple[float, float, int, tuple[float, float, float, float] | None]],
) -> bytes:
    document = pdfium.PdfDocument.new()
    try:
        for width, height, rotation, cropbox in pages:
            page = document.new_page(width, height)
            try:
                if cropbox is not None:
                    page.set_cropbox(*cropbox)
                if rotation:
                    page.set_rotation(rotation)
            finally:
                page.close()
        document.save(path)
    finally:
        document.close()
    return path.read_bytes()


def _write_userunit_pdf(path: Path, *, user_unit: int = 2) -> bytes:
    # Small self-contained PDF. The explicit /UserUnit exercises PDFium's canvas-unit
    # behavior without introducing another PDF parser/generator dependency.
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 36] "
            + f"/UserUnit {user_unit} ".encode()
            + b"/Resources << >> >>"
        ),
    ]
    body = bytearray(b"%PDF-1.7\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{index} 0 obj\n".encode())
        body.extend(obj)
        body.extend(b"\nendobj\n")
    xref = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    body.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode())
    body.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(bytes(body))
    return bytes(body)


def _render(tmp_path: Path, content: bytes, *, max_pages: int = 200):
    settings = Settings(environment="test", pdf_spool_root=tmp_path / "spool")
    spool = PdfSpool(settings.pdf_spool_root)
    import_id = uuid4()
    spool.prepare_import_dir(import_id)
    spool.source_path(import_id).write_bytes(content)
    request = PdfRenderRequest(
        import_id=import_id,
        fencing_token=1,
        source_sha256=hashlib.sha256(content).hexdigest(),
        max_pages=max_pages,
        max_side=settings.max_image_side,
        max_page_pixels=settings.max_image_pixels,
        max_aggregate_pixels=settings.max_document_pixels,
        max_page_raster_bytes=settings.max_upload_bytes,
        max_aggregate_raster_bytes=settings.max_pdf_raster_bytes,
        max_spool_bytes=settings.max_pdf_spool_bytes,
    )
    spool.prepare_attempt_dir(import_id, request.fencing_token)
    manifest = PdfRenderer(settings)._render(request)
    return spool, request, manifest


def test_renderer_provenance_is_the_pinned_binary_wheel() -> None:
    provenance = renderer_provenance()

    assert provenance.pypdfium2 == "5.12.1"
    assert provenance.pdfium_build == 7947
    assert provenance.native_library == "pypdfium2_raw/libpdfium.so"
    assert "V8" not in provenance.pdfium_flags
    assert "XFA" not in provenance.pdfium_flags


def test_blank_page_raster_is_deterministic(tmp_path: Path) -> None:
    pdf_path = tmp_path / "blank.pdf"
    content = _write_pdf(pdf_path, [(72, 72, 0, None)])

    first_spool, first_request, first = _render(tmp_path / "first", content)
    second_spool, second_request, second = _render(tmp_path / "second", content)

    assert first.page_count == second.page_count == 1
    assert first.aggregate_pixels == second.aggregate_pixels
    assert first.pages[0].sha256 == second.pages[0].sha256
    first_bytes = first_spool.page_path(
        first_request.import_id, 1, first.pages[0].filename
    ).read_bytes()
    second_bytes = second_spool.page_path(
        second_request.import_id, 1, second.pages[0].filename
    ).read_bytes()
    assert first_bytes == second_bytes


def test_cropbox_policy_uses_intersection_bbox(tmp_path: Path) -> None:
    pdf_path = tmp_path / "crop.pdf"
    content = _write_pdf(pdf_path, [(100, 100, 0, (10, 20, 60, 80))])

    _, _, manifest = _render(tmp_path / "run", content)
    page = manifest.pages[0]

    assert page.page_bbox == pytest.approx((10.0, 20.0, 60.0, 80.0))
    assert page.width == math.ceil(50 * PDF_RENDER_SCALE)
    assert page.height == math.ceil(60 * PDF_RENDER_SCALE)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_embedded_page_rotations_are_preserved(tmp_path: Path, rotation: int) -> None:
    pdf_path = tmp_path / f"rotation-{rotation}.pdf"
    content = _write_pdf(pdf_path, [(72, 36, rotation, None)])

    _, _, manifest = _render(tmp_path / f"run-{rotation}", content)

    assert manifest.pages[0].embedded_rotation == rotation
    expected = (72, 36) if rotation in (0, 180) else (36, 72)
    assert (manifest.pages[0].width, manifest.pages[0].height) == tuple(
        math.ceil(value * PDF_RENDER_SCALE) for value in expected
    )


def test_duplicate_pages_keep_duplicate_membership_and_identical_hashes(tmp_path: Path) -> None:
    pdf_path = tmp_path / "duplicates.pdf"
    content = _write_pdf(pdf_path, [(72, 72, 0, None), (72, 72, 0, None)])

    _, _, manifest = _render(tmp_path / "run", content)

    assert [page.ordinal for page in manifest.pages] == [0, 1]
    assert [page.filename for page in manifest.pages] == ["page-000001.png", "page-000002.png"]
    assert manifest.pages[0].sha256 == manifest.pages[1].sha256


def test_unusual_userunit_is_bounded_and_deterministic_in_pdfium_canvas_units(
    tmp_path: Path,
) -> None:
    content = _write_userunit_pdf(tmp_path / "user-unit.pdf", user_unit=2)

    _, _, first = _render(tmp_path / "first", content)
    _, _, second = _render(tmp_path / "second", content)

    assert first.pages[0].width <= 10_000
    assert first.pages[0].height <= 10_000
    assert first.pages[0].sha256 == second.pages[0].sha256
    assert first.pages[0].page_bbox == second.pages[0].page_bbox


def test_page_limit_is_rejected_before_rasterization(tmp_path: Path) -> None:
    content = _write_pdf(
        tmp_path / "two-pages.pdf",
        [(72, 72, 0, None), (72, 72, 0, None)],
    )

    with pytest.raises(PdfRenderRejected, match="pdf_page_limit") as exc:
        _render(tmp_path / "run", content, max_pages=1)

    assert exc.value.code == "pdf_page_limit"


def test_huge_geometry_is_rejected_before_rasterization(tmp_path: Path) -> None:
    content = _write_pdf(tmp_path / "huge.pdf", [(20_000, 20_000, 0, None)])

    with pytest.raises(PdfRenderRejected, match="pdf_geometry_limit") as exc:
        _render(tmp_path / "run", content)

    assert exc.value.code == "pdf_geometry_limit"


def test_malformed_and_truncated_pdf_fail_closed(tmp_path: Path) -> None:
    malformed = b"%PDF-1.7\nnot a valid PDF\n"

    with pytest.raises(PdfRenderRejected, match="pdf_invalid"):
        _render(tmp_path / "malformed", malformed)

    valid_path = tmp_path / "valid.pdf"
    valid = _write_pdf(valid_path, [(72, 72, 0, None)])
    truncated = valid[: max(16, len(valid) // 3)]
    with pytest.raises(PdfRenderRejected, match="pdf_invalid"):
        _render(tmp_path / "truncated", truncated)

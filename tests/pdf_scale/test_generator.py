from __future__ import annotations

import hashlib

import pypdfium2 as pdfium
import pytest

from mangasensei.pdf_imports.renderer import renderer_provenance
from tests.pdf_scale.generator import (
    EXPECTED_SOURCE_BYTES,
    EXPECTED_SOURCE_SHA256,
    MEDIA_BOX,
    PAGE_COUNT,
    WORKLOAD_VERSION,
    generate_pdf,
    source_manifest,
)


def test_pdf_scale_stdlib_v1_regenerates_exact_frozen_source() -> None:
    first = generate_pdf()
    second = generate_pdf()

    assert first == second
    assert len(first) == EXPECTED_SOURCE_BYTES == 46_282
    assert hashlib.sha256(first).hexdigest() == EXPECTED_SOURCE_SHA256
    assert EXPECTED_SOURCE_SHA256 == "cb181b41e45a46e138b7188d87d54620e4c1738dd654f3e6cb7eadc854ef2cf5"
    manifest = source_manifest(first)
    assert manifest["serializer"] == WORKLOAD_VERSION == "pdf-scale-stdlib-v1"
    assert manifest["pageCount"] == PAGE_COUNT == 200


def test_pdf_scale_stdlib_v1_has_no_implicit_serializer_features() -> None:
    content = generate_pdf()

    assert content.startswith(b"%PDF-1.4\n")
    assert content.endswith(b"%%EOF\n")
    assert b"\r" not in content
    assert content.count(b"/Type /Page ") == PAGE_COUNT
    assert content.count(b"/Resources << >>") == PAGE_COUNT
    assert content.count(b"\nstream\n") == PAGE_COUNT
    assert content.count(b"\nendstream\n") == PAGE_COUNT
    for forbidden in (
        b"/Font",
        b"/XObject",
        b"/Annots",
        b"/AcroForm",
        b"/JavaScript",
        b"/Metadata",
        b"/ID",
    ):
        assert forbidden not in content


@pytest.mark.parametrize("ordinal", [0, 19, 20, 99, 199])
def test_pdf_scale_stdlib_v1_rectangle_formula_is_present(ordinal: int) -> None:
    content = generate_pdf()
    x = 1 + (ordinal % 20)
    y = 1 + 4 * (ordinal // 20)
    expected = f"q\n0 g\n{x} {y} 1 1 re f\nQ\n".encode("ascii")

    assert content.count(expected) == 1


def test_pdf_scale_stdlib_v1_opens_in_reviewed_pdfium_with_exact_geometry(tmp_path) -> None:
    provenance = renderer_provenance()
    assert provenance.pypdfium2 == "5.12.1"
    assert provenance.pdfium_build == 7947

    path = tmp_path / "pdf-pagecount-max-200.pdf"
    path.write_bytes(generate_pdf())
    document = pdfium.PdfDocument(path)
    try:
        assert len(document) == PAGE_COUNT
        for ordinal in range(PAGE_COUNT):
            page = document[ordinal]
            try:
                assert page.get_size() == pytest.approx((MEDIA_BOX[2], MEDIA_BOX[3]), abs=1e-4)
                assert page.get_bbox() == pytest.approx(MEDIA_BOX, abs=1e-4)
                assert page.get_rotation() == 0
            finally:
                page.close()
    finally:
        document.close()

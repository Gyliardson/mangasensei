from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pypdfium2 as pdfium
import pytest
from pydantic import ValidationError

from mangasensei.config import Settings
from mangasensei.pdf_imports import native_provenance as native_provenance_module
from mangasensei.pdf_imports import renderer as renderer_module
from mangasensei.pdf_imports.contracts import PdfRenderRequest
from mangasensei.pdf_imports.native_provenance import (
    PDFIUM_NATIVE_SHA256_BY_PLATFORM,
    PYPDFIUM2_WHEEL_SHA256_BY_PLATFORM,
)
from mangasensei.pdf_imports.renderer import (
    PdfRenderer,
    PdfRenderRejected,
    renderer_provenance,
)
from mangasensei.pdf_imports.spool import PdfSpool, PdfSpoolError

_ENCRYPTED_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "password-protected-one-page.pdf"
)
_ENCRYPTED_FIXTURE_SHA256 = "c93872e9616c127dca30c59f4bd9aa5f80a1dc69ec79aa3376472f4c3a6a34ae"


def _build_pdf(objects: list[bytes], trailer_extra: bytes = b"") -> bytes:
    body = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
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
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root 1 0 R "
        + trailer_extra
        + b">>\nstartxref\n"
        + str(xref).encode()
        + b"\n%%EOF\n"
    )
    return bytes(body)


def _javascript_pdf() -> bytes:
    return _build_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Resources << >> >>",
            b"<< /S /JavaScript /JS (app.launchURL('https://example.invalid/', true)) >>",
        ]
    )


def _request_for(settings: Settings, spool: PdfSpool, content: bytes) -> PdfRenderRequest:
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


def test_password_protected_pdf_is_rejected_without_password_prompt(tmp_path: Path) -> None:
    content = _ENCRYPTED_FIXTURE.read_bytes()
    assert hashlib.sha256(content).hexdigest() == _ENCRYPTED_FIXTURE_SHA256
    settings = Settings(environment="test", pdf_spool_root=tmp_path / "spool")
    spool = PdfSpool(settings.pdf_spool_root)
    request = _request_for(settings, spool, content)

    with pytest.raises(PdfRenderRejected, match="pdf_encrypted_unsupported") as exc:
        PdfRenderer(settings)._render(request)

    assert exc.value.code == "pdf_encrypted_unsupported"


def test_javascript_open_action_is_not_executed_or_fetched(tmp_path: Path) -> None:
    settings = Settings(environment="test", pdf_spool_root=tmp_path / "spool")
    spool = PdfSpool(settings.pdf_spool_root)
    content = _javascript_pdf()
    request = _request_for(settings, spool, content)
    spool.prepare_attempt_dir(request.import_id, request.fencing_token)

    manifest = PdfRenderer(settings)._render(request)

    assert manifest.page_count == 1
    assert manifest.pages[0].byte_size > 0


def test_per_page_and_aggregate_resource_limits_fail_closed(tmp_path: Path) -> None:
    pdf_path = tmp_path / "limits.pdf"
    document = pdfium.PdfDocument.new()
    try:
        page = document.new_page(72, 72)
        page.close()
        document.save(pdf_path)
    finally:
        document.close()
    content = pdf_path.read_bytes()
    settings = Settings(environment="test", pdf_spool_root=tmp_path / "spool")
    spool = PdfSpool(settings.pdf_spool_root)
    base = _request_for(settings, spool, content)

    pixel_limited = base.model_copy(update={"max_page_pixels": 10})
    with pytest.raises(PdfRenderRejected, match="pdf_pixel_limit"):
        PdfRenderer(settings)._render(pixel_limited)

    bytes_limited = base.model_copy(update={"max_page_raster_bytes": 1})
    with pytest.raises(PdfRenderRejected, match="pdf_raster_bytes_limit"):
        PdfRenderer(settings)._render(bytes_limited)


def test_spool_rejects_traversal_symlink_and_hardlink_rasters(tmp_path: Path) -> None:
    spool = PdfSpool(tmp_path / "spool")
    import_id = uuid4()
    spool.prepare_import_dir(import_id)
    attempt = spool.prepare_attempt_dir(import_id, 1)

    with pytest.raises(PdfSpoolError, match="invalid raster filename"):
        spool.page_path(import_id, 1, "../../escape.png")

    target = attempt / "target.png"
    target.write_bytes(b"png")
    symlink = attempt / "page-000001.png"
    symlink.symlink_to(target.name)
    with pytest.raises(PdfSpoolError, match="regular file"):
        spool.require_regular_file(symlink)

    symlink.unlink()
    hardlink = attempt / "page-000001.png"
    os.link(target, hardlink)
    with pytest.raises(PdfSpoolError, match="hard-linked"):
        spool.require_regular_file(hardlink)


def _stub_renderer_provenance_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    flags: tuple[str, ...] = (),
    native_content: bytes | None = b"reviewed-native",
) -> Path:
    raw_root = tmp_path / "site-packages" / "pypdfium2_raw"
    raw_root.mkdir(parents=True)
    module_file = raw_root / "__init__.py"
    module_file.write_text("# test package\n")
    monkeypatch.setattr(renderer_module.pypdfium2_raw, "__file__", str(module_file))
    monkeypatch.setattr(renderer_module, "PYPDFIUM_INFO", SimpleNamespace(tag="5.12.1"))
    monkeypatch.setattr(
        renderer_module,
        "PDFIUM_INFO",
        SimpleNamespace(tag="152.0.7947.0", build=7947, flags=flags),
    )
    monkeypatch.setattr(
        native_provenance_module, "_runtime_platform_key", lambda: ("Linux", "x86_64")
    )
    native = raw_root / "libpdfium.so"
    if native_content is not None:
        native.write_bytes(native_content)
    return native


def test_renderer_provenance_accepts_supported_reviewed_native(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = _stub_renderer_provenance_runtime(tmp_path, monkeypatch)
    expected = hashlib.sha256(native.read_bytes()).hexdigest()
    monkeypatch.setattr(
        native_provenance_module,
        "PDFIUM_NATIVE_SHA256_BY_PLATFORM",
        {("Linux", "x86_64"): expected},
    )

    provenance = renderer_provenance()

    assert provenance.pypdfium2 == "5.12.1"
    assert provenance.pdfium_build == 7947
    assert provenance.pdfium_flags == ()
    assert provenance.native_library == "pypdfium2_raw/libpdfium.so"


def test_renderer_provenance_rejects_wrong_native_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_renderer_provenance_runtime(tmp_path, monkeypatch, native_content=b"tampered-native")
    monkeypatch.setattr(
        native_provenance_module,
        "PDFIUM_NATIVE_SHA256_BY_PLATFORM",
        {("Linux", "x86_64"): "0" * 64},
    )

    with pytest.raises(ValidationError, match="integrity verification"):
        renderer_provenance()


@pytest.mark.parametrize(
    ("system", "machine"),
    [("Darwin", "arm64"), ("Windows", "AMD64"), ("Linux", "riscv64")],
)
def test_renderer_provenance_rejects_unsupported_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    machine: str,
) -> None:
    _stub_renderer_provenance_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(
        native_provenance_module,
        "_runtime_platform_key",
        lambda: (system, machine.lower()),
    )

    with pytest.raises(ValidationError, match="unsupported hardened PDF renderer platform"):
        renderer_provenance()


def test_renderer_provenance_rejects_missing_native_library(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_renderer_provenance_runtime(tmp_path, monkeypatch, native_content=None)

    with pytest.raises(RuntimeError, match="shared library is missing"):
        renderer_provenance()


def test_renderer_provenance_rejects_symlink_outside_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = _stub_renderer_provenance_runtime(tmp_path, monkeypatch, native_content=None)
    outside = tmp_path / "system-libpdfium.so"
    outside.write_bytes(b"system-native")
    native.symlink_to(outside)

    with pytest.raises(RuntimeError, match="shared library is missing"):
        renderer_provenance()


@pytest.mark.parametrize("flag", ["V8", "XFA"])
def test_renderer_provenance_rejects_active_content_builds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    _stub_renderer_provenance_runtime(tmp_path, monkeypatch, flags=(flag,))

    with pytest.raises(RuntimeError, match="non-V8/non-XFA"):
        renderer_provenance()


def test_renderer_provenance_rejects_wrong_wrapper_or_pdfium_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_renderer_provenance_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(renderer_module, "PYPDFIUM_INFO", SimpleNamespace(tag="5.12.0"))
    with pytest.raises(RuntimeError, match="helper version"):
        renderer_provenance()

    monkeypatch.setattr(renderer_module, "PYPDFIUM_INFO", SimpleNamespace(tag="5.12.1"))
    monkeypatch.setattr(
        renderer_module,
        "PDFIUM_INFO",
        SimpleNamespace(tag="152.0.7946.0", build=7946, flags=()),
    )
    with pytest.raises(RuntimeError, match="PDFium build"):
        renderer_provenance()


def test_reviewed_linux_wheel_and_native_hash_contract_is_frozen() -> None:
    assert PYPDFIUM2_WHEEL_SHA256_BY_PLATFORM == {
        ("Linux", "x86_64"): "e10cbf41b21233ec5e20adfc170cf60edd77abead86a97dc708fff55a8a886c7",
        ("Linux", "aarch64"): "6eabf028ad8e7bc7811c9acf3a72718c180569b624b844d2c6cc974609784275",
    }
    assert PDFIUM_NATIVE_SHA256_BY_PLATFORM == {
        ("Linux", "x86_64"): "61c9f745c6296a1050599a99a1ed985036411b591a11bd2a41bafe530ecb4f33",
        ("Linux", "aarch64"): "f5c8d54a498e2112fbcf53e866c4a5635e9839db3a36d88c4772e5384dabeac6",
    }

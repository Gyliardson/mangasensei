from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path
from uuid import uuid4

import pypdfium2 as pdfium
import pytest

from mangasensei.config import Settings
from mangasensei.pdf_imports.contracts import PdfRenderRequest
from mangasensei.pdf_imports.renderer import PdfRenderRejected, PdfRenderer
from mangasensei.pdf_imports.spool import PdfSpool, PdfSpoolError

_PASSWORD_PADDING = bytes.fromhex(
    "28bf4e5e4e758a4164004e56fffa01082e2e00b6d0683e802f0ca9fe6453697a"
)


def _rc4(key: bytes, data: bytes) -> bytes:
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) % 256
        state[i], state[j] = state[j], state[i]
    output = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + state[i]) % 256
        state[i], state[j] = state[j], state[i]
        output.append(byte ^ state[(state[i] + state[j]) % 256])
    return bytes(output)


def _pad_password(password: bytes) -> bytes:
    return (password + _PASSWORD_PADDING)[:32]


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


def _encrypted_pdf() -> bytes:
    user_password = b"reader"
    owner_password = b"owner"
    permissions = -4
    file_id = bytes.fromhex("00112233445566778899aabbccddeeff")
    owner_key = hashlib.md5(_pad_password(owner_password)).digest()[:5]
    owner_entry = _rc4(owner_key, _pad_password(user_password))
    file_key = hashlib.md5(
        _pad_password(user_password)
        + owner_entry
        + struct.pack("<i", permissions)
        + file_id
    ).digest()[:5]
    user_entry = _rc4(file_key, _PASSWORD_PADDING)
    encrypt = (
        b"<< /Filter /Standard /V 1 /R 2 /Length 40 /O <"
        + owner_entry.hex().encode()
        + b"> /U <"
        + user_entry.hex().encode()
        + b"> /P "
        + str(permissions).encode()
        + b" >>"
    )
    return _build_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Resources << >> >>",
            encrypt,
        ],
        b"/Encrypt 4 0 R /ID [<"
        + file_id.hex().encode()
        + b"><"
        + file_id.hex().encode()
        + b">] ",
    )


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
    settings = Settings(environment="test", pdf_spool_root=tmp_path / "spool")
    spool = PdfSpool(settings.pdf_spool_root)
    request = _request_for(settings, spool, _encrypted_pdf())

    with pytest.raises(PdfRenderRejected, match="pdf_encrypted_unsupported") as exc:
        PdfRenderer(settings)._render(request)

    assert exc.value.code == "pdf_encrypted_unsupported"


def test_javascript_open_action_is_not_executed_or_fetched(tmp_path: Path) -> None:
    settings = Settings(environment="test", pdf_spool_root=tmp_path / "spool")
    spool = PdfSpool(settings.pdf_spool_root)
    content = _javascript_pdf()
    request = _request_for(settings, spool, content)

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

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from mangasensei.pdf_imports.spool import PdfSpool, PdfSpoolError


def test_symlinked_import_parent_cannot_escape_spool(tmp_path: Path) -> None:
    spool = PdfSpool(tmp_path / "spool")
    import_id = uuid4()
    outside = tmp_path / "outside"
    outside.mkdir()
    spool.import_dir(import_id).symlink_to(outside, target_is_directory=True)

    with pytest.raises(PdfSpoolError, match="directory"):
        spool.prepare_attempt_dir(import_id, 1)

    assert not (outside / "attempt-1").exists()


def test_symlinked_source_is_rejected_without_following_target(tmp_path: Path) -> None:
    spool = PdfSpool(tmp_path / "spool")
    import_id = uuid4()
    spool.prepare_import_dir(import_id)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.7\n")
    spool.source_path(import_id).symlink_to(outside)

    with pytest.raises(PdfSpoolError, match="regular file"):
        spool.require_regular_file(spool.source_path(import_id))


def test_hard_linked_source_is_rejected(tmp_path: Path) -> None:
    spool = PdfSpool(tmp_path / "spool")
    import_id = uuid4()
    spool.prepare_import_dir(import_id)
    source = spool.source_path(import_id)
    source.write_bytes(b"%PDF-1.7\n")
    os.link(source, tmp_path / "second-link.pdf")

    with pytest.raises(PdfSpoolError, match="hard-linked"):
        spool.require_regular_file(source)


def test_remove_import_also_removes_pending_requests(tmp_path: Path) -> None:
    spool = PdfSpool(tmp_path / "spool")
    import_id = uuid4()
    spool.prepare_import_dir(import_id)
    request = spool.request_path(import_id, 1)
    request.write_text("{}\n", encoding="utf-8")

    spool.remove_import(import_id)

    assert not request.exists()
    assert not spool.import_dir(import_id).exists()


def test_raster_filename_rejects_path_traversal(tmp_path: Path) -> None:
    spool = PdfSpool(tmp_path / "spool")

    with pytest.raises(PdfSpoolError, match="invalid raster filename"):
        spool.page_path(uuid4(), 1, "../page-000001.png")

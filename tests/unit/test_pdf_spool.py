from __future__ import annotations

import os
import stat
from pathlib import Path
from uuid import uuid4

import pytest

from mangasensei.pdf_imports.spool import PdfSpool, PdfSpoolError


def _spool(tmp_path: Path) -> PdfSpool:
    return PdfSpool(tmp_path / "input", tmp_path / "output")


def test_symlinked_import_parent_cannot_escape_spool(tmp_path: Path) -> None:
    spool = _spool(tmp_path)
    import_id = uuid4()
    outside = tmp_path / "outside"
    outside.mkdir()
    spool.import_dir(import_id).symlink_to(outside, target_is_directory=True)

    with pytest.raises(PdfSpoolError, match="directory"):
        spool.prepare_import_dir(import_id)

    assert not (outside / "source.pdf").exists()


def test_symlinked_source_is_rejected_without_following_target(tmp_path: Path) -> None:
    spool = _spool(tmp_path)
    import_id = uuid4()
    spool.prepare_import_dir(import_id)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.7\n")
    spool.source_path(import_id).symlink_to(outside)

    with pytest.raises(PdfSpoolError, match="regular file"):
        spool.require_regular_file(spool.source_path(import_id))


def test_hard_linked_source_is_rejected(tmp_path: Path) -> None:
    spool = _spool(tmp_path)
    import_id = uuid4()
    spool.prepare_import_dir(import_id)
    source = spool.source_path(import_id)
    source.write_bytes(b"%PDF-1.7\n")
    os.link(source, tmp_path / "second-link.pdf")

    with pytest.raises(PdfSpoolError, match="hard-linked"):
        spool.require_regular_file(source)


def test_source_validation_makes_trusted_source_renderer_group_readable(tmp_path: Path) -> None:
    spool = _spool(tmp_path)
    import_id = uuid4()
    spool.prepare_import_dir(import_id)
    source = spool.source_path(import_id)
    source.write_bytes(b"%PDF-1.7\n")
    source.chmod(0o600)

    spool.require_regular_file(source, max_bytes=1024)

    assert stat.S_IMODE(source.stat().st_mode) == 0o640


def test_remove_import_also_removes_pending_requests_and_output(tmp_path: Path) -> None:
    spool = _spool(tmp_path)
    import_id = uuid4()
    spool.prepare_import_dir(import_id)
    attempt = spool.prepare_attempt_dir(import_id, 1)
    request = spool.request_path(import_id, 1)
    request.write_text("{}\n", encoding="utf-8")
    (attempt / "page-000001.png").write_bytes(b"png")

    spool.remove_import(import_id)

    assert not request.exists()
    assert not spool.import_dir(import_id).exists()
    assert not spool.output_import_dir(import_id).exists()


def test_raster_filename_rejects_path_traversal(tmp_path: Path) -> None:
    spool = _spool(tmp_path)

    with pytest.raises(PdfSpoolError, match="invalid raster filename"):
        spool.page_path(uuid4(), 1, "../page-000001.png")


def test_lexical_dotdot_path_cannot_escape_output_root(tmp_path: Path) -> None:
    spool = _spool(tmp_path)
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_bytes(b"outside")
    malicious = spool.output_root / "imports" / ".." / ".." / "sentinel.txt"

    with pytest.raises(PdfSpoolError, match="escaped"):
        spool.read_bytes(malicious, max_bytes=1024)


def test_renderer_output_symlink_cannot_reach_outside_sentinel(tmp_path: Path) -> None:
    spool = _spool(tmp_path)
    import_id = uuid4()
    spool.prepare_attempt_dir(import_id, 1)
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_bytes(b"outside-secret")
    page = spool.page_path(import_id, 1, "page-000001.png")
    page.symlink_to(sentinel)

    with pytest.raises(PdfSpoolError, match="regular file"):
        spool.read_bytes(page, max_bytes=1024)

    assert sentinel.read_bytes() == b"outside-secret"


def test_renderer_output_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    spool = _spool(tmp_path)
    import_id = uuid4()
    spool.prepare_attempt_dir(import_id, 1)
    page = spool.page_path(import_id, 1, "page-000001.png")
    os.mkfifo(page)

    with pytest.raises(PdfSpoolError, match="regular file"):
        spool.read_bytes(page, max_bytes=1024)


def test_renderer_output_read_is_bounded(tmp_path: Path) -> None:
    spool = _spool(tmp_path)
    import_id = uuid4()
    spool.prepare_attempt_dir(import_id, 1)
    page = spool.page_path(import_id, 1, "page-000001.png")
    page.write_bytes(b"12345")

    with pytest.raises(PdfSpoolError, match="bounded size"):
        spool.read_bytes(page, max_bytes=4)


def test_replacement_after_open_consumes_original_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = _spool(tmp_path)
    import_id = uuid4()
    spool.prepare_attempt_dir(import_id, 1)
    page = spool.page_path(import_id, 1, "page-000001.png")
    page.write_bytes(b"validated-inode")
    replacement = tmp_path / "replacement.png"
    replacement.write_bytes(b"different-inode")
    real_read = os.read
    replaced = False

    def racing_read(descriptor: int, count: int) -> bytes:
        nonlocal replaced
        if not replaced:
            replaced = True
            os.replace(replacement, page)
        return real_read(descriptor, count)

    monkeypatch.setattr(os, "read", racing_read)

    content = spool.read_bytes(page, max_bytes=1024)

    assert content == b"validated-inode"
    assert Path(page).read_bytes() == b"different-inode"


def test_validate_then_path_read_consumes_same_validated_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = _spool(tmp_path)
    import_id = uuid4()
    spool.prepare_attempt_dir(import_id, 1)
    page = spool.page_path(import_id, 1, "page-000001.png")
    Path(page).write_bytes(b"validated-inode")
    replacement = tmp_path / "replacement.png"
    replacement.write_bytes(b"different-inode")
    real_read = os.read
    replaced = False

    def racing_read(descriptor: int, count: int) -> bytes:
        nonlocal replaced
        if not replaced:
            replaced = True
            os.replace(replacement, page)
        return real_read(descriptor, count)

    monkeypatch.setattr(os, "read", racing_read)

    metadata = spool.require_regular_file(page, max_bytes=1024)
    content = page.read_bytes()

    assert metadata.st_size == len(b"validated-inode")
    assert content == b"validated-inode"
    assert Path(page).read_bytes() == b"different-inode"


def test_parent_replacement_cannot_redirect_openat_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = _spool(tmp_path)
    import_id = uuid4()
    attempt = spool.prepare_attempt_dir(import_id, 1)
    page = spool.page_path(import_id, 1, "page-000001.png")
    page.write_bytes(b"renderer-output")
    sentinel_dir = tmp_path / "sentinel-dir"
    sentinel_dir.mkdir()
    (sentinel_dir / page.name).write_bytes(b"outside-sentinel")
    moved_attempt = attempt.with_name("attempt-1-moved")
    real_open = os.open
    replaced = False

    def racing_open(
        path: os.PathLike[str] | str, flags: int, *args: object, **kwargs: object
    ) -> int:
        nonlocal replaced
        if path == page.name and not replaced:
            replaced = True
            attempt.rename(moved_attempt)
            attempt.symlink_to(sentinel_dir, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", racing_open)

    content = spool.read_bytes(page, max_bytes=1024)

    assert content == b"renderer-output"
    assert (sentinel_dir / page.name).read_bytes() == b"outside-sentinel"


def test_cleanup_does_not_follow_renderer_symlink(tmp_path: Path) -> None:
    spool = _spool(tmp_path)
    import_id = uuid4()
    attempt = spool.prepare_attempt_dir(import_id, 1)
    sentinel = tmp_path / "sentinel-dir"
    sentinel.mkdir()
    (sentinel / "keep.txt").write_text("keep", encoding="utf-8")
    (attempt / "nested").symlink_to(sentinel, target_is_directory=True)

    spool.remove_import(import_id)

    assert (sentinel / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_output_parent_topology_is_coordinator_owned_and_not_renderer_writable(
    tmp_path: Path,
) -> None:
    spool = _spool(tmp_path)
    import_id = uuid4()
    attempt = spool.prepare_attempt_dir(import_id, 1)

    assert stat.S_IMODE(spool.output_root.stat().st_mode) == 0o710
    assert stat.S_IMODE(spool.output_imports.stat().st_mode) == 0o710
    assert stat.S_IMODE(spool.output_import_dir(import_id).stat().st_mode) == 0o710
    assert stat.S_IMODE(attempt.stat().st_mode) == 0o2730

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image

from mangasensei.storage.images import ImageValidationError, ImageValidator
from mangasensei.storage.local import LocalFilesystemStorage


def png_bytes(*, width: int = 40, height: int = 30) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (width, height), color=(245, 240, 230)).save(stream, format="PNG")
    return stream.getvalue()


def test_validator_fully_decodes_supported_static_image_without_changing_bytes() -> None:
    original = png_bytes()
    validator = ImageValidator(max_bytes=1_000_000, max_pixels=10_000, max_side=1000)

    validated = validator.validate(original, declared_media_type="image/png")

    assert validated.content == original
    assert validated.sha256 == hashlib.sha256(original).hexdigest()
    assert validated.width == 40
    assert validated.height == 30
    assert validated.media_type == "image/png"


@pytest.mark.parametrize(
    ("content", "declared_type", "message"),
    [
        (b"not an image", "image/png", "magic bytes"),
        (png_bytes(), "image/jpeg", "does not match"),
        (png_bytes(width=101, height=10), "image/png", "maximum side"),
    ],
)
def test_validator_rejects_invalid_or_mismatched_input(
    content: bytes, declared_type: str, message: str
) -> None:
    validator = ImageValidator(max_bytes=1_000_000, max_pixels=10_000, max_side=100)

    with pytest.raises(ImageValidationError, match=message):
        validator.validate(content, declared_media_type=declared_type)


@pytest.mark.asyncio
async def test_local_storage_is_atomic_immutable_and_content_deduplicated(tmp_path: Path) -> None:
    original = png_bytes()
    validated = ImageValidator(max_bytes=1_000_000, max_pixels=10_000, max_side=1000).validate(
        original, declared_media_type="image/png"
    )
    storage = LocalFilesystemStorage(tmp_path)

    first = await storage.store(validated)
    second = await storage.store(validated)

    assert first == second
    assert await storage.read(first) == original
    assert len(tuple((tmp_path / "objects").rglob(validated.sha256))) == 1
    assert not tuple((tmp_path / "tmp").glob("*"))


@pytest.mark.asyncio
async def test_staged_storage_keeps_recovery_marker_until_confirmed(tmp_path: Path) -> None:
    original = png_bytes()
    validated = ImageValidator(max_bytes=1_000_000, max_pixels=10_000, max_side=1000).validate(
        original, declared_media_type="image/png"
    )
    storage = LocalFilesystemStorage(tmp_path)

    pending = await storage.stage(validated)

    assert await storage.pending_writes() == (pending,)
    assert await storage.read(pending.storage_key) == original

    await storage.confirm(pending)

    assert await storage.pending_writes() == ()
    assert await storage.read(pending.storage_key) == original


@pytest.mark.asyncio
async def test_storage_rejects_untrusted_keys(tmp_path: Path) -> None:
    storage = LocalFilesystemStorage(tmp_path)

    with pytest.raises(ValueError, match="invalid storage key"):
        await storage.read("../../secret")

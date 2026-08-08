"""Strict validation for untrusted image uploads."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError


class ImageValidationError(ValueError):
    """An uploaded file does not satisfy the image contract."""


@dataclass(frozen=True, slots=True)
class ValidatedImage:
    content: bytes
    sha256: str
    width: int
    height: int
    media_type: str
    format: str


_FORMAT_MEDIA_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


def _has_supported_magic(content: bytes) -> bool:
    return (
        content.startswith(b"\x89PNG\r\n\x1a\n")
        or content.startswith(b"\xff\xd8\xff")
        or (len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP")
    )


class ImageValidator:
    def __init__(self, *, max_bytes: int, max_pixels: int, max_side: int) -> None:
        if min(max_bytes, max_pixels, max_side) <= 0:
            raise ValueError("image limits must be positive")
        self._max_bytes = max_bytes
        self._max_pixels = max_pixels
        self._max_side = max_side

    def validate(self, content: bytes, *, declared_media_type: str) -> ValidatedImage:
        if not content or len(content) > self._max_bytes:
            raise ImageValidationError("image exceeds maximum byte size")
        if not _has_supported_magic(content):
            raise ImageValidationError("unsupported image magic bytes")

        try:
            with Image.open(io.BytesIO(content)) as image:
                image_format = image.format
                if image_format not in _FORMAT_MEDIA_TYPES:
                    raise ImageValidationError("unsupported image format")
                expected_media_type = _FORMAT_MEDIA_TYPES[image_format]
                if declared_media_type.lower().strip() != expected_media_type:
                    raise ImageValidationError("declared media type does not match decoded image")
                width, height = image.size
                if max(width, height) > self._max_side:
                    raise ImageValidationError("image exceeds maximum side length")
                if width * height > self._max_pixels:
                    raise ImageValidationError("image exceeds maximum pixel count")
                if getattr(image, "n_frames", 1) != 1:
                    raise ImageValidationError("animated or multi-frame images are not supported")
                image.load()
        except ImageValidationError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ImageValidationError("image triggers decompression bomb protection") from exc
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ImageValidationError("image could not be decoded safely") from exc

        return ValidatedImage(
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            width=width,
            height=height,
            media_type=expected_media_type,
            format=image_format,
        )

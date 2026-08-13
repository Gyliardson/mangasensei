"""Reviewed native PDFium artifact contract for hardened local rendering."""

from __future__ import annotations

import hashlib
import platform
from pathlib import Path

import pypdfium2_raw

PDFIUM_NATIVE_SHA256_BY_PLATFORM: dict[tuple[str, str], str] = {
    ("Linux", "x86_64"): "61c9f745c6296a1050599a99a1ed985036411b591a11bd2a41bafe530ecb4f33",
    ("Linux", "aarch64"): "f5c8d54a498e2112fbcf53e866c4a5635e9839db3a36d88c4772e5384dabeac6",
}
PYPDFIUM2_WHEEL_SHA256_BY_PLATFORM: dict[tuple[str, str], str] = {
    ("Linux", "x86_64"): "e10cbf41b21233ec5e20adfc170cf60edd77abead86a97dc708fff55a8a886c7",
    ("Linux", "aarch64"): "6eabf028ad8e7bc7811c9acf3a72718c180569b624b844d2c6cc974609784275",
}


def _runtime_platform_key() -> tuple[str, str]:
    return platform.system(), platform.machine().lower()


def _raw_package_root() -> Path:
    return Path(pypdfium2_raw.__file__).resolve().parent


def _native_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_bundled_pdfium_native() -> str:
    """Return the fixed manifest path only for a reviewed native artifact."""
    platform_key = _runtime_platform_key()
    expected_sha256 = PDFIUM_NATIVE_SHA256_BY_PLATFORM.get(platform_key)
    if expected_sha256 is None:
        raise ValueError(
            f"unsupported hardened PDF renderer platform: {platform_key[0]}/{platform_key[1]}"
        )

    raw_root = _raw_package_root()
    native = raw_root / "libpdfium.so"
    if not native.is_file() or native.is_symlink():
        raise ValueError("bundled PDFium shared library is missing")
    try:
        resolved_native = native.resolve(strict=True)
        resolved_native.relative_to(raw_root)
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError("PDFium resolved outside the binary wheel") from exc
    if _native_sha256(resolved_native) != expected_sha256:
        raise ValueError("bundled PDFium native library failed integrity verification")
    return "pypdfium2_raw/libpdfium.so"

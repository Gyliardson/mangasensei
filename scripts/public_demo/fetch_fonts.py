from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FONT_MANIFEST = REPO_ROOT / "assets" / "public-demo" / "provenance" / "fonts.json"
DEFAULT_CACHE = REPO_ROOT / "var" / "public-demo" / "fonts"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path, *, expected_sha256: str, expected_bytes: int) -> None:
    if not path.is_file():
        raise SystemExit(f"missing font: {path}")
    if path.stat().st_size != expected_bytes:
        raise SystemExit(f"font size mismatch: {path}")
    actual = sha256(path)
    if actual != expected_sha256:
        raise SystemExit(f"font SHA-256 mismatch: {path}: {actual}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Acquire checksum-pinned public-demo fonts.")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="Copy matching filenames from a local directory instead of downloading them.",
    )
    args = parser.parse_args()
    manifest = json.loads(FONT_MANIFEST.read_text(encoding="utf-8"))
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    for font in manifest["fonts"]:
        target = args.cache_dir / font["cacheFile"]
        if target.exists():
            verify(target, expected_sha256=font["sha256"], expected_bytes=font["bytes"])
            print(f"verified {target}")
            continue
        if args.source_dir is not None:
            source = args.source_dir / font["cacheFile"]
            verify(source, expected_sha256=font["sha256"], expected_bytes=font["bytes"])
            shutil.copyfile(source, target)
        else:
            url = (
                "https://raw.githubusercontent.com/notofonts/noto-cjk/"
                f"{font['tag']}/{font['path']}"
            )
            print(f"downloading {font['id']} from pinned upstream tag {font['tag']}")
            with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as out:
                shutil.copyfileobj(response, out)
        verify(target, expected_sha256=font["sha256"], expected_bytes=font["bytes"])
        print(f"verified {target}")


if __name__ == "__main__":
    main()

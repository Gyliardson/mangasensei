from __future__ import annotations

import hashlib
import json
from pathlib import Path


def canonical_json_bytes(value: object) -> bytes:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (payload + "\n").encode("utf-8")


def write_canonical_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def file_record(root: Path, relative: str) -> dict[str, object]:
    path = root / relative
    return {
        "file": relative,
        "bytes": path.stat().st_size,
        "sha256": sha256_path(path),
    }

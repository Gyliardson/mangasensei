"""Recompute normalized JMdict metadata from the reviewed manifest-pinned source."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from mangasensei.linguistics.jmdict import JsonJmdictDictionary
from mangasensei.linguistics.jmdict_bootstrap import (
    CONVERTER_VERSION,
    JmdictManifest,
    build_normalized_jmdict,
    default_manifest_path,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=default_manifest_path())
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify derived metadata without modifying the manifest",
    )
    return parser


async def update_manifest(path: Path, *, check: bool) -> dict[str, object]:
    manifest = JmdictManifest.load(path)
    timeout = httpx.Timeout(300.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        normalized = await build_normalized_jmdict(manifest, client)

    payload = json.loads(normalized.decode("utf-8"))
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError("normalized JMdict output must contain entries")
    _validate_runtime_dictionary(
        normalized,
        expected_entry_count=len(entries),
        expected_version=manifest.source.source_version,
    )
    derived = {
        "filename": manifest.normalized.filename,
        "sha256": hashlib.sha256(normalized).hexdigest(),
        "size_bytes": len(normalized),
        "entry_count": len(entries),
        "converter_version": CONVERTER_VERSION,
    }
    current = manifest.normalized.model_dump()
    if check:
        if current != derived:
            raise ValueError(
                "JMdict normalized manifest metadata is stale; run "
                "uv run python scripts/update_jmdict_manifest.py"
            )
        return derived

    updated = manifest.model_copy(
        update={
            "normalized": manifest.normalized.model_copy(update=derived),
        }
    )
    content = json.dumps(
        updated.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    temporary = path.with_name(f"{path.name}.part-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return derived


def _validate_runtime_dictionary(
    normalized: bytes,
    *,
    expected_entry_count: int,
    expected_version: str,
) -> None:
    """Require the complete generated artifact to satisfy the runtime consumer contract."""
    with TemporaryDirectory(prefix="mangasensei-jmdict-") as temporary_directory:
        dictionary_path = Path(temporary_directory) / "jmdict.json"
        dictionary_path.write_bytes(normalized)
        dictionary = JsonJmdictDictionary(dictionary_path)
    if dictionary.entry_count != expected_entry_count:
        raise ValueError("generated JMdict runtime entry count differs from normalized output")
    if dictionary.version != expected_version:
        raise ValueError("generated JMdict runtime version differs from pinned source")
    if dictionary.digest != hashlib.sha256(normalized).digest():
        raise ValueError("generated JMdict runtime digest differs from normalized output")


def main() -> int:
    args = build_parser().parse_args()
    derived = asyncio.run(update_manifest(args.manifest, check=args.check))
    print(json.dumps(derived, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

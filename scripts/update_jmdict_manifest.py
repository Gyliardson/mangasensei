"""Recompute normalized metadata for the reviewed JMdict language packs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from mangasensei.linguistics.jmdict import JsonJmdictDictionary
from mangasensei.linguistics.jmdict_bootstrap import (
    CONVERTER_VERSION,
    JmdictManifest,
    build_normalized_jmdict,
)
from mangasensei.linguistics.jmdict_packs import (
    default_pack_registry_path,
    load_jmdict_packs,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=default_pack_registry_path())
    parser.add_argument(
        "--language",
        action="append",
        dest="languages",
        help="limit refresh/check to one reviewed product language; repeatable",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify derived metadata without modifying pack manifests",
    )
    return parser


async def update_manifests(
    registry_path: Path,
    *,
    languages: Sequence[str] | None,
    check: bool,
) -> dict[str, dict[str, object]]:
    packs = load_jmdict_packs(registry_path)
    selected = tuple(dict.fromkeys(languages)) if languages else tuple(sorted(packs))
    unknown = sorted(set(selected) - set(packs))
    if unknown:
        raise ValueError(f"unsupported dictionary language(s): {', '.join(unknown)}")

    timeout = httpx.Timeout(300.0, connect=30.0)
    derived_by_language: dict[str, dict[str, object]] = {}
    stale: dict[str, dict[str, object]] = {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        for language in selected:
            pack = packs[language]
            normalized = await build_normalized_jmdict(pack.manifest, client)
            payload = json.loads(normalized.decode("utf-8"))
            entries = payload.get("entries")
            if not isinstance(entries, list):
                raise ValueError("normalized JMdict output must contain entries")
            _validate_runtime_dictionary(
                normalized,
                expected_entry_count=len(entries),
                expected_version=pack.manifest.source.source_version,
            )
            derived: dict[str, object] = {
                "filename": pack.manifest.normalized.filename,
                "sha256": hashlib.sha256(normalized).hexdigest(),
                "size_bytes": len(normalized),
                "entry_count": len(entries),
                "converter_version": CONVERTER_VERSION,
            }
            derived_by_language[language] = derived
            current = pack.manifest.normalized.model_dump()
            if current == derived:
                continue
            if check:
                stale[language] = {"current": current, "derived": derived}
                continue
            updated = pack.manifest.model_copy(
                update={
                    "normalized": pack.manifest.normalized.model_copy(update=derived),
                }
            )
            _write_manifest(pack.manifest_path, updated)

    if stale:
        details = json.dumps(stale, ensure_ascii=False, sort_keys=True)
        raise ValueError(
            "JMdict normalized pack metadata is stale; run "
            f"uv run python scripts/update_jmdict_manifest.py; details={details}"
        )
    return derived_by_language


def _write_manifest(path: Path, manifest: JmdictManifest) -> None:
    content = json.dumps(
        manifest.model_dump(mode="json"),
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
    derived = asyncio.run(
        update_manifests(
            args.registry,
            languages=args.languages,
            check=args.check,
        )
    )
    print(json.dumps(derived, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

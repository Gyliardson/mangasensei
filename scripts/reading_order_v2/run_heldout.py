from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ReadingOrderArm

from .canonical import sha256_path, write_canonical_json, write_canonical_jsonl
from .contracts import PAGE_IDS, load_manifest
from .validate_corpus import validate_corpus

_HASH_SEEDS = (101, 202, 303)


def _run(command: list[str], *, hash_seed: int | None = None) -> None:
    environment = dict(os.environ)
    if hash_seed is not None:
        environment["PYTHONHASHSEED"] = str(hash_seed)
    subprocess.run(command, check=True, env=environment)


def _load_json(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected JSON object")
    return raw


def _aggregate_repeat(
    repeat_dir: Path, *, output_dir: Path, arm: ReadingOrderArm
) -> None:
    diagnostics = []
    orderings: dict[str, list[str]] = {}
    production_fidelity_values: list[bool] = []
    for page_id in PAGE_IDS:
        diagnostic = _load_json(repeat_dir / f"{page_id}.diagnostic.json")
        ordering = _load_json(repeat_dir / f"{page_id}.ordering.json")
        diagnostics.append(diagnostic)
        values = ordering.get("regionIds")
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(f"{page_id}: malformed arm ordering")
        orderings[page_id] = values
        fidelity = ordering.get("productionFidelityVerified")
        if fidelity is not None:
            if not isinstance(fidelity, bool):
                raise ValueError(f"{page_id}: malformed A0 fidelity signal")
            production_fidelity_values.append(fidelity)
    write_canonical_jsonl(output_dir / "diagnostics.jsonl", diagnostics)
    write_canonical_json(
        output_dir / "ordering.json",
        {
            "schemaVersion": "reading-order-v2-ordering-v1",
            "armId": arm.value,
            "productionFidelityVerified": (
                all(production_fidelity_values) if production_fidelity_values else None
            ),
            "pages": orderings,
        },
    )


def _run_ordering_phase(
    *,
    corpus_root: Path,
    output_root: Path,
    repository_sha: str,
) -> dict[ReadingOrderArm, list[Path]]:
    """Finish every arm/repeat before any scorer process receives annotation access."""
    repeat_dirs: dict[ReadingOrderArm, list[Path]] = {}
    for arm in ReadingOrderArm:
        arm_repeats: list[Path] = []
        for repeat_index, hash_seed in enumerate(_HASH_SEEDS, start=1):
            repeat_dir = output_root / "raw" / arm.value / f"repeat-{repeat_index}"
            repeat_dir.mkdir(parents=True, exist_ok=True)
            for page_id in PAGE_IDS:
                _run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.reading_order_v2.run_arm",
                        "--corpus-root",
                        str(corpus_root),
                        "--page-id",
                        page_id,
                        "--arm",
                        arm.value,
                        "--repository-sha",
                        repository_sha,
                        "--output-dir",
                        str(repeat_dir),
                    ],
                    hash_seed=hash_seed,
                )
            _aggregate_repeat(repeat_dir, output_dir=repeat_dir, arm=arm)
            arm_repeats.append(repeat_dir)
        repeat_dirs[arm] = arm_repeats
    return repeat_dirs


def _same_hashes(repeat_dirs: list[Path]) -> tuple[dict[str, object], bool]:
    records: dict[str, object] = {}
    triples: list[tuple[str, str]] = []
    for index, directory in enumerate(repeat_dirs, start=1):
        record = {
            "diagnosticsSha256": sha256_path(directory / "diagnostics.jsonl"),
            "orderingSha256": sha256_path(directory / "ordering.json"),
        }
        records[f"repeat-{index}"] = record
        triples.append((str(record["diagnosticsSha256"]), str(record["orderingSha256"])))
    return records, len(set(triples)) == 1


def _score_phase(
    *,
    corpus_root: Path,
    output_root: Path,
    repeat_dirs: dict[ReadingOrderArm, list[Path]],
) -> None:
    """Only this later phase passes corpus-root to a scorer that may open annotations."""
    for arm in ReadingOrderArm:
        arm_output = output_root / "arms" / arm.value
        arm_output.mkdir(parents=True, exist_ok=True)
        repeat_hashes, deterministic = _same_hashes(repeat_dirs[arm])
        if not deterministic:
            raise RuntimeError(f"{arm.value}: non-deterministic ordering diagnostics/reports")
        score_hashes: list[str] = []
        for index, repeat_dir in enumerate(repeat_dirs[arm], start=1):
            scores_path = repeat_dir / "scores.json"
            _run(
                [
                    sys.executable,
                    "-m",
                    "scripts.reading_order_v2.scoring",
                    "--corpus-root",
                    str(corpus_root),
                    "--ordering",
                    str(repeat_dir / "ordering.json"),
                    "--output",
                    str(scores_path),
                ]
            )
            score_hash = sha256_path(scores_path)
            score_hashes.append(score_hash)
            record = repeat_hashes[f"repeat-{index}"]
            assert isinstance(record, dict)
            record["scoresSha256"] = score_hash
        if len(set(score_hashes)) != 1:
            raise RuntimeError(f"{arm.value}: score report changed across identical arm outputs")
        first = repeat_dirs[arm][0]
        for name in ("diagnostics.jsonl", "ordering.json", "scores.json"):
            (arm_output / name).write_bytes((first / name).read_bytes())
        write_canonical_json(
            arm_output / "repeat-hashes.json",
            {
                "schemaVersion": "reading-order-v2-repeat-hashes-v1",
                "armId": arm.value,
                "deterministic": True,
                "repeats": repeat_hashes,
            },
        )


def run_heldout(*, corpus_root: Path, output_root: Path, repository_sha: str) -> None:
    if len(repository_sha) != 40 or any(
        character not in "0123456789abcdef" for character in repository_sha
    ):
        raise ValueError("repository SHA must be lowercase 40-hex")
    manifest = load_manifest(corpus_root / "manifest.json")
    if tuple(page.page_id for page in manifest.pages) != PAGE_IDS:
        raise ValueError("frozen held-out manifest must contain exactly H01..H16")

    # Phase 1 intentionally validates only manifest identity. Arm subprocesses receive no
    # annotation path and fixture inputs contain only structural geometry.
    repeat_dirs = _run_ordering_phase(
        corpus_root=corpus_root,
        output_root=output_root,
        repository_sha=repository_sha,
    )

    # Ground truth is first opened after all arm/repeat subprocesses have terminated.
    validate_corpus(corpus_root)
    _score_phase(
        corpus_root=corpus_root,
        output_root=output_root,
        repeat_dirs=repeat_dirs,
    )
    _run(
        [
            sys.executable,
            "-m",
            "scripts.reading_order_v2.verdict",
            "--corpus-root",
            str(corpus_root),
            "--output-root",
            str(output_root),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Future Reading Order v2 held-out orchestrator; do not use before corpus freeze"
    )
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--repository-sha", required=True)
    args = parser.parse_args(argv)
    run_heldout(
        corpus_root=args.corpus_root,
        output_root=args.output_root,
        repository_sha=args.repository_sha,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

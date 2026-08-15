from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from shutil import which

from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ArmId

from .canonical import canonical_json_bytes, sha256_bytes, write_canonical_json
from .contracts import PAGE_IDS, load_ground_truth
from .scoring import score_corpus, score_page
from .validate_corpus import CORPUS_ROOT, validate_corpus

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = REPO_ROOT / "var" / "research" / "reading-order-v2" / "raw"
SUMMARY_ROOT = REPO_ROOT / "var" / "research" / "reading-order-v2" / "summary"
HASH_SEEDS = (101, 202, 303)


def _git_head() -> str:
    git = which("git")
    if git is None:
        raise RuntimeError("git executable is required to identify the execution SHA")
    result = subprocess.run(  # noqa: S603
        [git, "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _run_fresh_process(page_id: str, arm: ArmId, repository_sha: str, repeat: int) -> None:
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = str(HASH_SEEDS[repeat - 1])
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "scripts.reading_order_v2.run_arm",
            "--page-id",
            page_id,
            "--arm",
            arm.value,
            "--repository-sha",
            repository_sha,
            "--repeat",
            str(repeat),
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def _aggregate_repeat(
    arm: ArmId, repeat: int
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    diagnostics: list[dict[str, object]] = []
    ordering: list[dict[str, object]] = []
    root = RAW_ROOT / arm.value / f"repeat-{repeat}"
    for page_id in PAGE_IDS:
        diagnostic = json.loads((root / f"{page_id}.diagnostic.json").read_text(encoding="utf-8"))
        order = json.loads((root / f"{page_id}.ordering.json").read_text(encoding="utf-8"))
        if not isinstance(diagnostic, dict) or not isinstance(order, dict):
            raise ValueError("arm output must contain JSON objects")
        diagnostics.append(diagnostic)
        ordering.append(order)
    return diagnostics, ordering


def main() -> None:
    # Qualification remains impossible until PR2 supplies and freezes all H01-H16 assets.
    validate_corpus(CORPUS_ROOT)
    repository_sha = _git_head()
    arm_scores = {}
    for arm in ArmId:
        repeat_hashes: list[dict[str, str]] = []
        first_diagnostics: list[dict[str, object]] | None = None
        first_ordering: list[dict[str, object]] | None = None
        for repeat in (1, 2, 3):
            for page_id in PAGE_IDS:
                _run_fresh_process(page_id, arm, repository_sha, repeat)
            diagnostics, ordering = _aggregate_repeat(arm, repeat)
            hashes = {
                "diagnosticsSha256": sha256_bytes(canonical_json_bytes(diagnostics)),
                "orderingSha256": sha256_bytes(canonical_json_bytes(ordering)),
            }
            repeat_hashes.append(hashes)
            if repeat == 1:
                first_diagnostics, first_ordering = diagnostics, ordering
        if len({item["diagnosticsSha256"] for item in repeat_hashes}) != 1 or len(
            {item["orderingSha256"] for item in repeat_hashes}
        ) != 1:
            raise RuntimeError(f"{arm.value}: nondeterministic output across fresh-process repeats")
        assert first_diagnostics is not None
        assert first_ordering is not None
        page_scores = []
        for order in first_ordering:
            page_id = order["pageId"]
            final_order = order["finalOrder"]
            if (
                not isinstance(page_id, str)
                or not isinstance(final_order, list)
                or not all(isinstance(item, str) for item in final_order)
            ):
                raise ValueError("malformed ordering output")
            gt = load_ground_truth(CORPUS_ROOT / "annotations" / f"{page_id}.json")
            page_scores.append(score_page(gt, tuple(final_order)))
        corpus_score = score_corpus(tuple(page_scores))
        arm_scores[arm.value] = corpus_score
        write_canonical_json(SUMMARY_ROOT / arm.value / "repeat-hashes.json", repeat_hashes)
        write_canonical_json(SUMMARY_ROOT / arm.value / "scores.json", corpus_score)
    # Formal verdict/evidence assembly intentionally occurs in a separate reviewed execution step.
    write_canonical_json(SUMMARY_ROOT / "arm-scores.json", arm_scores)


if __name__ == "__main__":
    main()

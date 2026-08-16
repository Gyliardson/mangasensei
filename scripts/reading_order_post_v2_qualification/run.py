from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from shutil import which
from typing import Any

from .canonical import canonical_json_bytes, sha256_bytes, sha256_path, write_canonical_json
from .contracts import (
    ArmId,
    load_ground_truth,
    validate_corpus,
    validate_qualification_identity,
)
from .exercise import build_exercise_report
from .scoring import CorpusScore, candidate_only_wrong_pairs, score_corpus, score_page
from .spec import validate_spec
from .verdict import evaluate_verdict

REPO_ROOT = Path(__file__).resolve().parents[2]
HASH_SEEDS = (101, 202, 303)
RUNNER_MODULE = "scripts.reading_order_post_v2_qualification.run"


def _git(*args: str) -> str:
    git = which("git")
    if git is None:
        raise RuntimeError("git is required for qualification execution")
    result = subprocess.run(  # noqa: S603
        [git, *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _load_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _run_fresh_process(
    *,
    corpus_root: Path,
    page_id: str,
    arm: ArmId,
    execution_sha: str,
    repeat: int,
    output_root: Path,
) -> None:
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = str(HASH_SEEDS[repeat - 1])
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "scripts.reading_order_post_v2_qualification.run_arm",
            "--corpus-root",
            str(corpus_root),
            "--page-id",
            page_id,
            "--arm",
            arm.value,
            "--execution-sha",
            execution_sha,
            "--repeat",
            str(repeat),
            "--output-root",
            str(output_root),
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def _aggregate_repeat(
    *,
    output_root: Path,
    arm: ArmId,
    repeat: int,
    page_ids: tuple[str, ...],
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    diagnostics: dict[str, dict[str, object]] = {}
    ordering: dict[str, dict[str, object]] = {}
    root = output_root / "raw" / arm.value / f"repeat-{repeat}"
    for page_id in page_ids:
        diagnostics[page_id] = _load_object(root / f"{page_id}.diagnostic.json")
        ordering[page_id] = _load_object(root / f"{page_id}.ordering.json")
    return diagnostics, ordering


def _score_repeat(
    *,
    corpus_root: Path,
    page_ids: tuple[str, ...],
    ordering: dict[str, dict[str, object]],
) -> CorpusScore:
    page_scores = []
    for page_id in page_ids:
        record = ordering[page_id]
        final_order = record.get("finalOrder")
        if not isinstance(final_order, list) or not all(
            isinstance(item, str) for item in final_order
        ):
            raise ValueError(f"{page_id}: malformed ordering output")
        gt = load_ground_truth(corpus_root / "annotations" / f"{page_id}.json")
        page_scores.append(score_page(gt, tuple(final_order)))
    return score_corpus(tuple(page_scores))


def _repeat_record(
    *,
    diagnostics: dict[str, dict[str, object]],
    ordering: dict[str, dict[str, object]],
    score: CorpusScore,
) -> dict[str, str]:
    diagnostic_sha = sha256_bytes(canonical_json_bytes(diagnostics))
    ordering_sha = sha256_bytes(canonical_json_bytes(ordering))
    score_sha = sha256_bytes(canonical_json_bytes(score))
    result_sha = sha256_bytes(
        canonical_json_bytes(
            {
                "diagnosticsSha256": diagnostic_sha,
                "orderingSha256": ordering_sha,
                "scoreSha256": score_sha,
            }
        )
    )
    return {
        "diagnosticsSha256": diagnostic_sha,
        "orderingSha256": ordering_sha,
        "scoreSha256": score_sha,
        "resultSha256": result_sha,
    }


def _require_repeat_determinism(arm: ArmId, records: list[dict[str, str]]) -> None:
    if len(records) != 3 or len({record["resultSha256"] for record in records}) != 1:
        raise ValueError(f"{arm.value}: fresh-process qualification repeats are nondeterministic")


def _comparison(scores: dict[ArmId, CorpusScore]) -> dict[str, object]:
    control = scores[ArmId.CONTROL]
    arms: dict[str, object] = {}
    for arm in ArmId:
        score = scores[arm]
        arms[arm.value] = {
            "comparablePairs": score.aggregate.comparable_pairs,
            "correctPairs": score.aggregate.correct_pairs,
            "wrongPairs": score.aggregate.wrong_pairs_count,
            "pairwiseAccuracy": score.aggregate.pairwise_accuracy,
            "normalizedError": score.aggregate.normalized_error,
            "exactSequencePages": score.exact_sequence_pages,
            "candidateOnlyWrongPairsVersusControl": candidate_only_wrong_pairs(
                control.aggregate, score.aggregate
            ),
        }
    return {"controlArm": ArmId.CONTROL.value, "arms": arms}


def execute(
    *,
    corpus_root: Path,
    spec_path: Path,
    expected_spec_sha256: str,
    expected_manifest_sha256: str,
    expected_design_sha256: str,
    qualification_identity: str,
    execution_sha: str,
    expected_tree_sha: str,
    output_root: Path,
) -> None:
    if _git("rev-parse", "HEAD") != execution_sha:
        raise ValueError("execution SHA does not match HEAD")
    if _git("rev-parse", "HEAD^{tree}") != expected_tree_sha:
        raise ValueError("execution tree SHA mismatch")
    if _git("status", "--porcelain"):
        raise RuntimeError("qualification requires a clean repository")
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError("qualification output root must start empty")

    spec = validate_spec(spec_path, expected_sha256=expected_spec_sha256)
    manifest_path = corpus_root / "manifest.json"
    design_path = corpus_root / "corpus-design.json"
    if sha256_path(manifest_path) != expected_manifest_sha256:
        raise ValueError("held-out manifest SHA-256 mismatch")
    if sha256_path(design_path) != expected_design_sha256:
        raise ValueError("held-out design SHA-256 mismatch")
    design, manifest, annotations = validate_corpus(corpus_root)
    if manifest.design_sha256 != expected_design_sha256:
        raise ValueError("manifest does not bind expected corpus design SHA-256")

    validate_qualification_identity(
        qualification_identity,
        experiment_id=str(spec["experimentId"]),
        spec_sha256=expected_spec_sha256,
        manifest_sha256=expected_manifest_sha256,
        design_sha256=expected_design_sha256,
        execution_sha=execution_sha,
        execution_tree_sha=expected_tree_sha,
    )

    scores: dict[ArmId, CorpusScore] = {}
    selected_diagnostics: dict[ArmId, dict[str, dict[str, object]]] = {}
    selected_ordering: dict[ArmId, dict[str, dict[str, object]]] = {}
    repeat_hashes: dict[ArmId, list[dict[str, str]]] = {}

    for arm in ArmId:
        records: list[dict[str, str]] = []
        first_diagnostics: dict[str, dict[str, object]] | None = None
        first_ordering: dict[str, dict[str, object]] | None = None
        first_score: CorpusScore | None = None
        for repeat in (1, 2, 3):
            for page_id in design.page_ids:
                _run_fresh_process(
                    corpus_root=corpus_root,
                    page_id=page_id,
                    arm=arm,
                    execution_sha=execution_sha,
                    repeat=repeat,
                    output_root=output_root,
                )
            diagnostics, ordering = _aggregate_repeat(
                output_root=output_root,
                arm=arm,
                repeat=repeat,
                page_ids=design.page_ids,
            )
            score = _score_repeat(
                corpus_root=corpus_root,
                page_ids=design.page_ids,
                ordering=ordering,
            )
            records.append(_repeat_record(diagnostics=diagnostics, ordering=ordering, score=score))
            if repeat == 1:
                first_diagnostics = diagnostics
                first_ordering = ordering
                first_score = score

        _require_repeat_determinism(arm, records)
        assert first_diagnostics is not None
        assert first_ordering is not None
        assert first_score is not None
        selected_diagnostics[arm] = first_diagnostics
        selected_ordering[arm] = first_ordering
        scores[arm] = first_score
        repeat_hashes[arm] = records
        write_canonical_json(output_root / "summary" / arm.value / "scores.json", first_score)
        write_canonical_json(
            output_root / "summary" / arm.value / "repeat-hashes.json",
            records,
        )

    exercise = build_exercise_report(annotations=annotations, diagnostics=selected_diagnostics)
    verdict = evaluate_verdict(harness_valid=True, scores=scores, exercise=exercise)

    write_canonical_json(
        output_root / "summary" / "arm-scores.json",
        {arm.value: scores[arm] for arm in ArmId},
    )
    write_canonical_json(output_root / "summary" / "comparison.json", _comparison(scores))
    write_canonical_json(output_root / "summary" / "exercise.json", exercise)
    write_canonical_json(output_root / "summary" / "verdict.json", verdict)
    write_canonical_json(
        output_root / "summary" / "run-metadata.json",
        {
            "experimentId": spec["experimentId"],
            "qualificationIdentity": qualification_identity,
            "executionSha": execution_sha,
            "executionTreeSha": expected_tree_sha,
            "specSha256": expected_spec_sha256,
            "corpusId": design.corpus_id,
            "corpusVersion": design.version,
            "manifestSha256": expected_manifest_sha256,
            "designSha256": expected_design_sha256,
            "runnerModule": RUNNER_MODULE,
            "repeatHashSeeds": list(HASH_SEEDS),
            "armOrder": [arm.value for arm in ArmId],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute the frozen post-v2 qualification once")
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--experiment-spec", type=Path, required=True)
    parser.add_argument("--expected-spec-sha256", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--expected-design-sha256", required=True)
    parser.add_argument("--qualification-identity", required=True)
    parser.add_argument("--execution-sha", required=True)
    parser.add_argument("--expected-tree-sha", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    execute(
        corpus_root=args.corpus_root,
        spec_path=args.experiment_spec,
        expected_spec_sha256=args.expected_spec_sha256,
        expected_manifest_sha256=args.expected_manifest_sha256,
        expected_design_sha256=args.expected_design_sha256,
        qualification_identity=args.qualification_identity,
        execution_sha=args.execution_sha,
        expected_tree_sha=args.expected_tree_sha,
        output_root=args.output_root,
    )


if __name__ == "__main__":
    main()

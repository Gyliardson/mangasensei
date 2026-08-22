from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
from scripts.reading_order_v3_authoring.contracts import CorpusDesign, load_design

from . import exercise_v3, run_arm_v3
from . import v3_clean_room_compat as compat
from .canonical import canonical_json_bytes, sha256_bytes, write_canonical_json
from .contracts import ArmId, PageGroundTruth
from .exercise import ExerciseReport
from .exercise_v3 import V3DiagnosticValidationError, V3TrustedPageInput
from .preflight_v3 import validate_preflight_v3
from .scoring import CorpusScore, candidate_only_wrong_pairs, score_corpus, score_page
from .verdict import ComponentStatus, GateReason, Verdict, VerdictResult
from .verdict_v3 import evaluate_verdict_v3

HASH_SEEDS = (101, 202, 303)
RUNNER_MODULE = "scripts.reading_order_post_v2_qualification.run_v3"
ARM_RUNNER_MODULE = "scripts.reading_order_post_v2_qualification.run_arm_v3"
ORDERING_SCHEMA_VERSION = "reading-order-post-v2-ordering-v1"
ORDERING_FIELDS = frozenset(
    {"schemaVersion", "experimentArm", "executionSha", "pageId", "finalOrder"}
)
REPO_ROOT = Path(__file__).resolve().parents[2]

EvidenceError = dict[str, object]
Diagnostics = dict[ArmId, dict[str, dict[str, object]]]
Orderings = dict[ArmId, dict[str, dict[str, object]]]


def _error(
    code: str,
    location: str,
    *,
    error_type: str | None = None,
    detail: str | None = None,
) -> EvidenceError:
    record: EvidenceError = {"code": code, "location": location}
    if error_type is not None:
        record["errorType"] = error_type
    if detail is not None:
        record["detail"] = detail
    return record


def _child_environment(seed: int) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("PYTHON")
    }
    env.update(
        {
            "PYTHONPATH": os.pathsep.join((str(REPO_ROOT), str(REPO_ROOT / "backend" / "src"))),
            "PYTHONNOUSERSITE": "1",
            "PYTHONHASHSEED": str(seed),
        }
    )
    return env


def _run_fresh_process(
    *,
    corpus_root: Path,
    page_id: str,
    arm: ArmId,
    execution_sha: str,
    repeat: int,
    output_root: Path,
) -> EvidenceError | None:
    command = [
        sys.executable,
        "-m",
        ARM_RUNNER_MODULE,
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
    ]
    location = f"{arm.value}/repeat-{repeat}/{page_id}"
    try:
        subprocess.run(  # noqa: S603
            command,
            cwd=REPO_ROOT,
            env=_child_environment(HASH_SEEDS[repeat - 1]),
            check=True,
        )
    except Exception as exc:  # noqa: BLE001 - child execution evidence boundary
        return _error(
            "subprocess-failure",
            location,
            error_type=type(exc).__name__,
            detail="arm child process did not complete successfully",
        )
    return None


def _load_output(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _collect_repeat(
    *,
    output_root: Path,
    arm: ArmId,
    repeat: int,
    page_ids: tuple[str, ...],
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]], list[EvidenceError]]:
    diagnostics: dict[str, dict[str, object]] = {}
    orderings: dict[str, dict[str, object]] = {}
    errors: list[EvidenceError] = []
    root = output_root / "raw" / arm.value / f"repeat-{repeat}"
    for page_id in page_ids:
        location = f"{arm.value}/repeat-{repeat}/{page_id}"
        for kind, destination in (("diagnostic", diagnostics), ("ordering", orderings)):
            path = root / f"{page_id}.{kind}.json"
            if not path.is_file():
                errors.append(_error(f"missing-{kind}", location))
                continue
            try:
                destination[page_id] = _load_output(path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                errors.append(
                    _error(
                        f"malformed-{kind}",
                        location,
                        error_type=type(exc).__name__,
                    )
                )
    return diagnostics, orderings, errors


def _trusted_page_inputs(
    corpus_root: Path, design: CorpusDesign
) -> dict[str, V3TrustedPageInput]:
    trusted: dict[str, V3TrustedPageInput] = {}
    for record in design.pages:
        assets = run_arm_v3._resolve_page_assets(corpus_root, record.page_id)
        manifest_identity = run_arm_v3._verify_sealed_page(
            assets, record.page_id, manifest_identity=None
        )
        page = compat.load_arm_input(assets.input_path)
        run_arm_v3._verify_sealed_page(
            assets, record.page_id, manifest_identity=manifest_identity
        )
        if page.page_id != record.page_id:
            raise ValueError(f"{record.page_id}: input pageId mismatch")
        decoded = run_arm_v3._decode_rgb_image(
            assets.image_path,
            width=page.width,
            height=page.height,
            page_id=record.page_id,
        )
        run_arm_v3._verify_sealed_page(
            assets, record.page_id, manifest_identity=manifest_identity
        )
        pixels = np.array(decoded, dtype=np.uint8, copy=True, order="C")
        pixels.setflags(write=False)
        trusted[record.page_id] = V3TrustedPageInput(page=page, pixels=pixels)
    return trusted


def _ordering_problems(
    *,
    diagnostics_by_repeat: dict[int, Diagnostics],
    orderings_by_repeat: dict[int, Orderings],
    trusted_page_inputs: dict[str, V3TrustedPageInput],
    execution_sha: str,
) -> list[str]:
    problems: list[str] = []
    for repeat in (1, 2, 3):
        for arm in ArmId:
            for page_id, trusted in trusted_page_inputs.items():
                where = f"{arm.value}/repeat-{repeat}/{page_id}"
                ordering = orderings_by_repeat[repeat][arm][page_id]
                diagnostic = diagnostics_by_repeat[repeat][arm][page_id]
                if set(ordering) != ORDERING_FIELDS:
                    problems.append(f"{where}: ordering field set mismatch")
                    continue
                if ordering["schemaVersion"] != ORDERING_SCHEMA_VERSION:
                    problems.append(f"{where}: ordering schemaVersion mismatch")
                if ordering["experimentArm"] != arm.value:
                    problems.append(f"{where}: ordering experimentArm mismatch")
                if ordering["pageId"] != page_id:
                    problems.append(f"{where}: ordering pageId mismatch")
                if ordering["executionSha"] != execution_sha:
                    problems.append(f"{where}: ordering executionSha mismatch")
                final_order = ordering["finalOrder"]
                expected = {region.region_id for region in trusted.page.regions}
                if (
                    not isinstance(final_order, list)
                    or not all(isinstance(item, str) for item in final_order)
                    or len(final_order) != len(expected)
                    or len(set(final_order)) != len(final_order)
                    or set(final_order) != expected
                ):
                    problems.append(f"{where}: ordering finalOrder is not the trusted inventory")
                if diagnostic.get("executionSha") != execution_sha:
                    problems.append(f"{where}: diagnostic executionSha mismatch")
                if diagnostic.get("finalOrder") != final_order:
                    problems.append(f"{where}: diagnostic and ordering finalOrder disagree")
    return problems


def _raw_repeat_metadata(
    *,
    diagnostics_by_repeat: dict[int, Diagnostics],
    orderings_by_repeat: dict[int, Orderings],
    page_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    expected_pages = set(page_ids)
    for repeat, seed in enumerate(HASH_SEEDS, start=1):
        arms: dict[str, object] = {}
        for arm in ArmId:
            diagnostics = diagnostics_by_repeat[repeat][arm]
            orderings = orderings_by_repeat[repeat][arm]
            complete = set(diagnostics) == expected_pages and set(orderings) == expected_pages
            arms[arm.value] = {
                "status": "COLLECTED" if complete else "INCOMPLETE",
                "diagnosticsSha256": (
                    sha256_bytes(canonical_json_bytes(diagnostics)) if diagnostics else None
                ),
                "orderingSha256": (
                    sha256_bytes(canonical_json_bytes(orderings)) if orderings else None
                ),
            }
        records.append({"repeat": repeat, "pythonHashSeed": seed, "arms": arms})
    return records


def _authenticate_all_diagnostics(
    *,
    diagnostics_by_repeat: dict[int, Diagnostics],
    annotations_by_id: dict[str, PageGroundTruth],
    trusted_page_inputs: dict[str, V3TrustedPageInput],
    execution_sha: str,
) -> None:
    problems: list[str] = []
    for repeat in (1, 2, 3):
        for arm in ArmId:
            for page_id, page in annotations_by_id.items():
                produced_sha = exercise_v3._diagnostic(
                    diagnostics_by_repeat[repeat][arm][page_id],
                    arm=arm,
                    page=page,
                    trusted=trusted_page_inputs[page_id],
                    problems=problems,
                )
                if produced_sha is not None and produced_sha != execution_sha:
                    problems.append(
                        f"{arm.value}/repeat-{repeat}/{page_id}: diagnostic executionSha mismatch"
                    )
    if problems:
        raise V3DiagnosticValidationError(problems)


def _score_repeat(
    *,
    annotations_by_id: dict[str, PageGroundTruth],
    page_ids: tuple[str, ...],
    ordering: dict[str, dict[str, object]],
) -> CorpusScore:
    page_scores = []
    for page_id in page_ids:
        final_order = ordering[page_id]["finalOrder"]
        assert isinstance(final_order, list)
        page_scores.append(score_page(annotations_by_id[page_id], tuple(final_order)))
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


def _deterministic_hashes(
    repeat_hashes: dict[ArmId, list[dict[str, str]]],
) -> list[dict[str, object]]:
    for arm, records in repeat_hashes.items():
        if len(records) != 3 or len({record["resultSha256"] for record in records}) != 1:
            raise ValueError(f"{arm.value}: scoring-inclusive repeats are nondeterministic")
    global_records: list[dict[str, object]] = []
    for repeat, seed in enumerate(HASH_SEEDS, start=1):
        arms = {
            arm.value: repeat_hashes[arm][repeat - 1]["resultSha256"] for arm in ArmId
        }
        global_records.append(
            {
                "repeat": repeat,
                "pythonHashSeed": seed,
                "armResultSha256": arms,
                "qualificationResultSha256": sha256_bytes(canonical_json_bytes(arms)),
            }
        )
    if len({record["qualificationResultSha256"] for record in global_records}) != 1:
        raise ValueError("whole qualification scoring-inclusive repeat hashes differ")
    return global_records


def _comparison(scores: dict[ArmId, CorpusScore]) -> dict[str, object]:
    control = scores[ArmId.CONTROL]
    return {
        "controlArm": ArmId.CONTROL.value,
        "arms": {
            arm.value: {
                "comparablePairs": scores[arm].aggregate.comparable_pairs,
                "correctPairs": scores[arm].aggregate.correct_pairs,
                "wrongPairs": scores[arm].aggregate.wrong_pairs_count,
                "pairwiseAccuracy": scores[arm].aggregate.pairwise_accuracy,
                "normalizedError": scores[arm].aggregate.normalized_error,
                "exactSequencePages": scores[arm].exact_sequence_pages,
                "candidateOnlyWrongPairsVersusControl": candidate_only_wrong_pairs(
                    control.aggregate, scores[arm].aggregate
                ),
            }
            for arm in ArmId
        },
    }


def _invalid_verdict(errors: list[EvidenceError]) -> VerdictResult:
    detail = canonical_json_bytes(errors).decode("utf-8").strip()
    return VerdictResult(
        Verdict.INVALID_EXPERIMENT,
        "harness-invalid",
        ComponentStatus.NOT_EVALUATED,
        ComponentStatus.NOT_EVALUATED,
        ComponentStatus.NOT_EVALUATED,
        ComponentStatus.NOT_EVALUATED,
        ComponentStatus.NOT_EVALUATED,
        (GateReason("diagnostic-authentication", "all", "fail", detail),),
    )


def _write_invalid(
    *, output_root: Path, metadata: dict[str, Any], errors: list[EvidenceError]
) -> None:
    invalid_metadata = {**metadata, "evidenceStatus": "INVALID", "evidenceErrors": errors}
    write_canonical_json(output_root / "summary" / "run-metadata.json", invalid_metadata)
    write_canonical_json(output_root / "summary" / "verdict.json", _invalid_verdict(errors))


def _metadata(
    *,
    context: object,
    design: CorpusDesign,
    qualification_identity: str,
    execution_sha: str,
    expected_tree_sha: str,
    expected_spec_sha256: str,
    expected_methodology_sha256: str,
    expected_manifest_sha256: str,
    expected_design_sha256: str,
    page_ids: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "experimentId": context.spec["experimentId"],  # type: ignore[attr-defined]
        "qualificationIdentity": qualification_identity,
        "executionSha": execution_sha,
        "executionTreeSha": expected_tree_sha,
        "specSha256": expected_spec_sha256,
        "methodologySha256": expected_methodology_sha256,
        "corpusId": design.corpus_id,
        "corpusVersion": design.version,
        "manifestSha256": expected_manifest_sha256,
        "designSha256": expected_design_sha256,
        "runnerModule": RUNNER_MODULE,
        "armRunnerModule": ARM_RUNNER_MODULE,
        "repeatHashSeeds": list(HASH_SEEDS),
        "armOrder": [arm.value for arm in ArmId],
        "pageOrder": list(page_ids),
    }


def _evidence_error_from_exception(exc: Exception) -> EvidenceError:
    if isinstance(exc, V3DiagnosticValidationError):
        return _error(
            "diagnostic-authentication-failure",
            "qualification",
            error_type=type(exc).__name__,
            detail="; ".join(exc.problems),
        )
    if isinstance(exc, ValueError) and "nondeterministic" in str(exc):
        return _error(
            "nondeterministic-evidence",
            "qualification",
            error_type=type(exc).__name__,
            detail=str(exc),
        )
    return _error(
        "post-candidate-evidence-failure",
        "qualification",
        error_type=type(exc).__name__,
        detail="post-candidate evidence processing did not complete",
    )


def execute(
    *,
    corpus_root: Path,
    spec_path: Path,
    experiment_id: str,
    expected_spec_sha256: str,
    expected_methodology_sha256: str,
    expected_manifest_sha256: str,
    expected_design_sha256: str,
    qualification_identity: str,
    execution_sha: str,
    expected_tree_sha: str,
    output_root: Path,
) -> None:
    context = validate_preflight_v3(
        corpus_root=corpus_root,
        spec_path=spec_path,
        experiment_id=experiment_id,
        expected_spec_sha256=expected_spec_sha256,
        expected_methodology_sha256=expected_methodology_sha256,
        expected_manifest_sha256=expected_manifest_sha256,
        expected_design_sha256=expected_design_sha256,
        qualification_identity=qualification_identity,
        execution_sha=execution_sha,
        expected_tree_sha=expected_tree_sha,
    )
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError("qualification output root must start empty")

    design = load_design(corpus_root / "corpus-design.json")
    annotations, c3_rejection_page_ids = compat.load_clean_room_annotations(corpus_root)
    page_ids = tuple(record.page_id for record in design.pages)
    annotations_by_id = {page.page_id: page for page in annotations}
    if tuple(annotations_by_id) != page_ids:
        raise ValueError("clean-room design and annotation page order/inventory disagree")
    trusted_page_inputs = _trusted_page_inputs(corpus_root, design)
    metadata = _metadata(
        context=context,
        design=design,
        qualification_identity=qualification_identity,
        execution_sha=execution_sha,
        expected_tree_sha=expected_tree_sha,
        expected_spec_sha256=expected_spec_sha256,
        expected_methodology_sha256=expected_methodology_sha256,
        expected_manifest_sha256=expected_manifest_sha256,
        expected_design_sha256=expected_design_sha256,
        page_ids=page_ids,
    )

    errors: list[EvidenceError] = []
    for arm in ArmId:
        for repeat in (1, 2, 3):
            for page_id in page_ids:
                error = _run_fresh_process(
                    corpus_root=corpus_root,
                    page_id=page_id,
                    arm=arm,
                    execution_sha=execution_sha,
                    repeat=repeat,
                    output_root=output_root,
                )
                if error is not None:
                    errors.append(error)

    diagnostics_by_repeat: dict[int, Diagnostics] = {}
    orderings_by_repeat: dict[int, Orderings] = {}
    for repeat in (1, 2, 3):
        diagnostics_by_repeat[repeat] = {}
        orderings_by_repeat[repeat] = {}
        for arm in ArmId:
            diagnostics, orderings, collection_errors = _collect_repeat(
                output_root=output_root,
                arm=arm,
                repeat=repeat,
                page_ids=page_ids,
            )
            diagnostics_by_repeat[repeat][arm] = diagnostics
            orderings_by_repeat[repeat][arm] = orderings
            errors.extend(collection_errors)
    metadata["repeatEvidence"] = _raw_repeat_metadata(
        diagnostics_by_repeat=diagnostics_by_repeat,
        orderings_by_repeat=orderings_by_repeat,
        page_ids=page_ids,
    )
    if errors:
        _write_invalid(output_root=output_root, metadata=metadata, errors=errors)
        return

    ordering_problems = _ordering_problems(
        diagnostics_by_repeat=diagnostics_by_repeat,
        orderings_by_repeat=orderings_by_repeat,
        trusted_page_inputs=trusted_page_inputs,
        execution_sha=execution_sha,
    )
    if ordering_problems:
        errors = [
            _error("ordering-envelope-failure", "qualification", detail=problem)
            for problem in ordering_problems
        ]
        _write_invalid(output_root=output_root, metadata=metadata, errors=errors)
        return

    try:
        selected_diagnostics = diagnostics_by_repeat[1]
        compat.validate_diagnostics_v3(
            annotations=annotations,
            diagnostics=selected_diagnostics,
            trusted_page_inputs=trusted_page_inputs,
            c3_rejection_page_ids=c3_rejection_page_ids,
        )
        _authenticate_all_diagnostics(
            diagnostics_by_repeat=diagnostics_by_repeat,
            annotations_by_id=annotations_by_id,
            trusted_page_inputs=trusted_page_inputs,
            execution_sha=execution_sha,
        )
        exercise: ExerciseReport = compat.build_exercise_report_v3(
            annotations=annotations,
            diagnostics=selected_diagnostics,
            trusted_page_inputs=trusted_page_inputs,
            c3_rejection_page_ids=c3_rejection_page_ids,
        )

        scores_by_repeat: dict[int, dict[ArmId, CorpusScore]] = {}
        repeat_hashes: dict[ArmId, list[dict[str, str]]] = {arm: [] for arm in ArmId}
        for repeat in (1, 2, 3):
            scores_by_repeat[repeat] = {}
            for arm in ArmId:
                score = _score_repeat(
                    annotations_by_id=annotations_by_id,
                    page_ids=page_ids,
                    ordering=orderings_by_repeat[repeat][arm],
                )
                scores_by_repeat[repeat][arm] = score
                repeat_hashes[arm].append(
                    _repeat_record(
                        diagnostics=diagnostics_by_repeat[repeat][arm],
                        ordering=orderings_by_repeat[repeat][arm],
                        score=score,
                    )
                )
        global_repeats = _deterministic_hashes(repeat_hashes)
    except Exception as exc:  # noqa: BLE001 - post-candidate evidence boundary
        _write_invalid(
            output_root=output_root,
            metadata=metadata,
            errors=[_evidence_error_from_exception(exc)],
        )
        return

    scores = scores_by_repeat[1]
    verdict = evaluate_verdict_v3(harness_valid=True, scores=scores, exercise=exercise)
    for arm in ArmId:
        write_canonical_json(output_root / "summary" / arm.value / "scores.json", scores[arm])
        write_canonical_json(
            output_root / "summary" / arm.value / "repeat-hashes.json",
            repeat_hashes[arm],
        )
    write_canonical_json(
        output_root / "summary" / "arm-scores.json",
        {arm.value: scores[arm] for arm in ArmId},
    )
    write_canonical_json(output_root / "summary" / "repeat-hashes.json", global_repeats)
    write_canonical_json(output_root / "summary" / "comparison.json", _comparison(scores))
    write_canonical_json(output_root / "summary" / "exercise.json", exercise)
    write_canonical_json(output_root / "summary" / "verdict.json", verdict)
    write_canonical_json(
        output_root / "summary" / "run-metadata.json",
        {
            **metadata,
            "evidenceStatus": "VALID",
            "evidenceErrors": [],
            "qualificationResultSha256": global_repeats[0]["qualificationResultSha256"],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute the frozen clean-room v3 qualification")
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--experiment-spec", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--expected-spec-sha256", required=True)
    parser.add_argument("--expected-methodology-sha256", required=True)
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
        experiment_id=args.experiment_id,
        expected_spec_sha256=args.expected_spec_sha256,
        expected_methodology_sha256=args.expected_methodology_sha256,
        expected_manifest_sha256=args.expected_manifest_sha256,
        expected_design_sha256=args.expected_design_sha256,
        qualification_identity=args.qualification_identity,
        execution_sha=args.execution_sha,
        expected_tree_sha=args.expected_tree_sha,
        output_root=args.output_root,
    )


if __name__ == "__main__":
    main()

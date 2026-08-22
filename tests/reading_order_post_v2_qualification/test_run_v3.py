from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from scripts.reading_order_post_v2_qualification import run_v3
from scripts.reading_order_post_v2_qualification.canonical import canonical_json_bytes
from scripts.reading_order_post_v2_qualification.contracts import (
    ArmId,
    ArmPageInput,
    PageGroundTruth,
    RegionFixture,
)
from scripts.reading_order_post_v2_qualification.exercise import ExerciseCount, ExerciseReport
from scripts.reading_order_post_v2_qualification.exercise_v3 import (
    EXERCISE_MINIMA_V3,
    V3DiagnosticValidationError,
    V3TrustedPageInput,
)
from scripts.reading_order_post_v2_qualification.verdict import (
    ComponentStatus,
    Verdict,
    VerdictResult,
)
from tests.reading_order_v3_authoring._fixtures import _write

EXECUTION_SHA = "a" * 40
PAGE_ID = "page.synthetic"


@dataclass(frozen=True)
class _DesignPage:
    page_id: str = PAGE_ID
    input: str = "inputs/page.json"
    image: str = "images/page.png"


@dataclass(frozen=True)
class _Design:
    corpus_id: str = "synthetic-v3"
    version: str = "0.0.0-test"
    pages: tuple[_DesignPage, ...] = (_DesignPage(),)


def _page() -> ArmPageInput:
    line = (((0, 0), (1, 0), (1, 1), (0, 1)),)
    return ArmPageInput(
        PAGE_ID,
        2,
        2,
        (
            RegionFixture("r0", 0, line, 0.0),
            RegionFixture("r1", 1, line, 0.0),
        ),
    )


def _annotation() -> PageGroundTruth:
    return PageGroundTruth(PAGE_ID, ("r0", "r1"), (), (), ())


def _exercise() -> ExerciseReport:
    return ExerciseReport(
        counts={
            name: ExerciseCount(minimum, (), ())
            for name, minimum in EXERCISE_MINIMA_V3.items()
        },
        minima=dict(EXERCISE_MINIMA_V3),
    )


def _verdict(value: Verdict = Verdict.C1_INCONCLUSIVE) -> VerdictResult:
    return VerdictResult(
        value,
        "VALID",
        ComponentStatus.INCONCLUSIVE,
        ComponentStatus.INCONCLUSIVE,
        ComponentStatus.INCONCLUSIVE,
        ComponentStatus.INCONCLUSIVE,
        ComponentStatus.NOT_EVALUATED,
        (),
    )


def _arg(command: list[str], name: str) -> str:
    return command[command.index(name) + 1]


def _install_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: list[str],
    tamper: str | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "processes": [],
        "trusted": None,
        "verdict_calls": 0,
        "producer_auth": [],
        "score_calls": 0,
    }

    def preflight(**_kwargs: object) -> SimpleNamespace:
        events.append("preflight")
        return SimpleNamespace(spec={"experimentId": "experiment-v3"})

    def process(command: list[str], **kwargs: object) -> SimpleNamespace:
        events.append("candidate")
        state["processes"].append((list(command), kwargs))
        arm = _arg(command, "--arm")
        repeat = int(_arg(command, "--repeat"))
        page_id = _arg(command, "--page-id")
        execution_sha = _arg(command, "--execution-sha")
        output = Path(_arg(command, "--output-root")) / "raw" / arm / f"repeat-{repeat}"
        output.mkdir(parents=True, exist_ok=True)
        if tamper == "subprocess" and arm == ArmId.CONTROL.value and repeat == 1:
            raise subprocess.CalledProcessError(7, command)
        order = ["r0", "r1"]
        diagnostic_order = list(order)
        if tamper == "order" and arm == ArmId.CONTROL.value:
            diagnostic_order.reverse()
        if tamper == "repeat" and arm == ArmId.CONTROL.value and repeat == 3:
            order.reverse()
            diagnostic_order = list(order)
        diagnostic = {
            "experimentArm": arm,
            "executionSha": "b" * 40 if tamper == "sha" else execution_sha,
            "pageId": page_id,
            "finalOrder": diagnostic_order,
        }
        ordering = {
            "schemaVersion": "reading-order-post-v2-ordering-v1",
            "experimentArm": arm,
            "executionSha": execution_sha,
            "pageId": page_id,
            "finalOrder": order,
        }
        if tamper == "ordering-fields":
            ordering["unexpected"] = True
        for suffix, value in (("diagnostic", diagnostic), ("ordering", ordering)):
            if tamper == "missing" and suffix == "ordering":
                continue
            if tamper == "malformed" and suffix == "diagnostic":
                (output / f"{page_id}.{suffix}.json").write_text("{", encoding="utf-8")
                continue
            (output / f"{page_id}.{suffix}.json").write_text(
                json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        return SimpleNamespace(returncode=0)

    def validate(**kwargs: object) -> None:
        events.append("compat-auth")
        state["trusted"] = kwargs["trusted_page_inputs"]

    def producer_auth(
        _diagnostic: object,
        *,
        arm: ArmId,
        page: PageGroundTruth,
        trusted: V3TrustedPageInput,
        problems: list[str],
    ) -> str:
        del trusted, problems
        events.append("producer-auth")
        state["producer_auth"].append((arm, page.page_id))
        return EXECUTION_SHA

    def build(**_kwargs: object) -> ExerciseReport:
        events.append("build")
        return _exercise()

    def evaluate(**_kwargs: object) -> VerdictResult:
        state["verdict_calls"] += 1
        return _verdict()

    monkeypatch.setattr(run_v3, "validate_preflight_v3", preflight)
    @contextmanager
    def staged(corpus_root: Path, **_kwargs: object) -> Iterator[Path]:
        events.append("stage")
        yield corpus_root

    @contextmanager
    def candidate_staged(corpus_root: Path) -> Iterator[Path]:
        events.append("candidate-stage")
        yield corpus_root / "candidate-view"

    monkeypatch.setattr(run_v3, "_stage_sealed_corpus", staged)
    monkeypatch.setattr(run_v3, "_stage_candidate_corpus", candidate_staged)
    monkeypatch.setattr(run_v3.subprocess, "run", process)
    monkeypatch.setattr(run_v3, "load_design", lambda _path: _Design())
    monkeypatch.setattr(
        run_v3.compat,
        "load_clean_room_annotations",
        lambda _root: ((_annotation(),), frozenset({PAGE_ID})),
    )
    monkeypatch.setattr(
        run_v3.run_arm_v3,
        "_resolve_page_assets",
        lambda root, _page_id: SimpleNamespace(
            input_path=root / "inputs/page.json",
            image_path=root / "images/page.png",
        ),
    )
    monkeypatch.setattr(
        run_v3.run_arm_v3,
        "_verify_sealed_page",
        lambda *_args, **_kwargs: b"sealed-manifest",
    )
    monkeypatch.setattr(run_v3.compat, "load_arm_input", lambda _path: _page())
    monkeypatch.setattr(
        run_v3.run_arm_v3,
        "_decode_rgb_image",
        lambda *_args, **_kwargs: np.zeros((2, 2, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(run_v3.compat, "validate_diagnostics_v3", validate)
    monkeypatch.setattr(run_v3.exercise_v3, "_diagnostic", producer_auth)
    monkeypatch.setattr(run_v3.compat, "build_exercise_report_v3", build)
    monkeypatch.setattr(run_v3, "evaluate_verdict_v3", evaluate)
    real_score_page = run_v3.score_page

    def tracked_score(*args: Any, **kwargs: Any) -> Any:
        events.append("score")
        state["score_calls"] += 1
        return real_score_page(*args, **kwargs)

    monkeypatch.setattr(run_v3, "score_page", tracked_score)
    return state


def _execute(tmp_path: Path) -> Path:
    output = tmp_path / "output"
    run_v3.execute(
        corpus_root=tmp_path / "corpus",
        spec_path=tmp_path / "spec.json",
        experiment_id="experiment-v3",
        expected_spec_sha256="1" * 64,
        expected_methodology_sha256="2" * 64,
        expected_manifest_sha256="3" * 64,
        expected_design_sha256="4" * 64,
        qualification_identity="identity",
        execution_sha=EXECUTION_SHA,
        expected_tree_sha="5" * 40,
        output_root=output,
    )
    return output


def test_preflight_is_first_and_runs_three_fresh_seeded_processes_per_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    state = _install_harness(monkeypatch, events=events)

    output = _execute(tmp_path)

    assert events[:3] == ["preflight", "stage", "candidate-stage"]
    assert len(state["processes"]) == len(ArmId) * 3
    assert {
        (int(_arg(command, "--repeat")), kwargs["env"]["PYTHONHASHSEED"])
        for command, kwargs in state["processes"]
    } == {(1, "101"), (2, "202"), (3, "303")}
    assert all(
        command[1:4]
        == [
            "-m",
            "scripts.reading_order_post_v2_qualification.run_arm_v3",
            "--corpus-root",
        ]
        for command, _kwargs in state["processes"]
    )
    assert {
        Path(_arg(command, "--corpus-root")) for command, _kwargs in state["processes"]
    } == {tmp_path / "corpus" / "candidate-view"}
    repeat_hashes = json.loads(
        (output / "summary/repeat-hashes.json").read_text(encoding="utf-8")
    )
    assert len({record["qualificationResultSha256"] for record in repeat_hashes}) == 1
    assert state["verdict_calls"] == 1


def test_child_environment_removes_python_injection_and_pins_import_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONHOME", "attacker-home")
    monkeypatch.setenv("PYTHONSTARTUP", "attacker-startup.py")
    monkeypatch.setenv("PYTHONWARNINGS", "error")
    monkeypatch.setenv("PythonInspect", "1")
    state = _install_harness(monkeypatch, events=[])

    _execute(tmp_path)

    expected_path = os.pathsep.join(
        (str(run_v3.REPO_ROOT), str(run_v3.REPO_ROOT / "backend" / "src"))
    )
    for _command, kwargs in state["processes"]:
        python_env = {
            key: value
            for key, value in kwargs["env"].items()
            if key.upper().startswith("PYTHON")
        }
        assert python_env in (
            {
                "PYTHONPATH": expected_path,
                "PYTHONNOUSERSITE": "1",
                "PYTHONHASHSEED": "101",
            },
            {
                "PYTHONPATH": expected_path,
                "PYTHONNOUSERSITE": "1",
                "PYTHONHASHSEED": "202",
            },
            {
                "PYTHONPATH": expected_path,
                "PYTHONNOUSERSITE": "1",
                "PYTHONHASHSEED": "303",
            },
        )


def test_trusted_inputs_are_internal_copies_and_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _install_harness(monkeypatch, events=[])

    _execute(tmp_path)

    trusted = state["trusted"][PAGE_ID]
    assert isinstance(trusted, V3TrustedPageInput)
    assert trusted.page == _page()
    assert trusted.pixels.flags.owndata
    assert not trusted.pixels.flags.writeable
    with pytest.raises(ValueError, match="read-only"):
        trusted.pixels[0, 0, 0] = 1


@pytest.mark.parametrize("tamper", ["sha", "order"])
def test_execution_sha_and_final_order_fail_at_trust_boundary(
    tamper: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _install_harness(monkeypatch, events=[], tamper=tamper)

    output = _execute(tmp_path)

    verdict = json.loads((output / "summary/verdict.json").read_text(encoding="utf-8"))
    assert verdict["verdict"] == "INVALID_EXPERIMENT"
    assert verdict["harness_status"] == "harness-invalid"
    assert all(verdict[name] == "NOT_EVALUATED" for name in (
        "c1_status", "c2_status", "c3_status", "b1_status", "final_status"
    ))
    assert not (output / "summary/exercise.json").exists()
    assert not (output / "summary/arm-scores.json").exists()
    assert not (output / "summary/comparison.json").exists()
    assert not list((output / "summary").glob("*/scores.json"))
    assert "exercise" not in json.dumps(verdict).lower()
    assert state["verdict_calls"] == 0
    assert state["score_calls"] == 0
    assert (output / "summary/run-metadata.json").is_file()
    assert list((output / "raw").rglob("*.json"))


def test_producer_authentication_failure_has_canonical_invalid_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _install_harness(monkeypatch, events=[])

    def reject(**_kwargs: object) -> None:
        raise V3DiagnosticValidationError(("producer evidence mismatch",))

    monkeypatch.setattr(run_v3.compat, "validate_diagnostics_v3", reject)
    output = _execute(tmp_path)

    raw = (output / "summary/verdict.json").read_bytes()
    verdict = json.loads(raw)
    assert raw.endswith(b"\n")
    assert verdict["verdict"] == "INVALID_EXPERIMENT"
    assert verdict["reasons"][0]["gate"] == "diagnostic-authentication"
    assert not (output / "summary/exercise.json").exists()
    assert state["verdict_calls"] == 0
    assert state["score_calls"] == 0


def test_authenticated_path_builds_only_through_compat_then_calls_v3_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _install_harness(monkeypatch, events=[])
    calls: list[str] = []
    monkeypatch.setattr(
        run_v3.compat,
        "validate_diagnostics_v3",
        lambda **_kwargs: calls.append("auth"),
    )
    monkeypatch.setattr(
        run_v3.compat,
        "build_exercise_report_v3",
        lambda **_kwargs: calls.append("build") or _exercise(),
    )

    output = _execute(tmp_path)

    assert calls == ["auth", "build"]
    assert state["verdict_calls"] == 1
    assert json.loads((output / "summary/verdict.json").read_text())["verdict"] == "C1_INCONCLUSIVE"
    assert (output / "summary/exercise.json").is_file()


def test_authentication_and_exercise_build_finish_before_any_scoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    _install_harness(monkeypatch, events=events)

    _execute(tmp_path)

    assert events.index("compat-auth") < events.index("build") < events.index("score")
    assert events.index("producer-auth") < events.index("build")


def test_frozen_producer_authenticator_covers_every_scored_repeat_arm_and_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _install_harness(monkeypatch, events=[])

    _execute(tmp_path)

    assert len(state["producer_auth"]) == len(ArmId) * 3
    assert set(state["producer_auth"]) == {(arm, PAGE_ID) for arm in ArmId}


def test_repeat_mismatch_is_classified_after_authentication_without_score_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _install_harness(monkeypatch, events=[], tamper="repeat")

    output = _execute(tmp_path)

    verdict = json.loads((output / "summary/verdict.json").read_text(encoding="utf-8"))
    assert verdict["verdict"] == "INVALID_EXPERIMENT"
    assert state["trusted"] is not None
    assert state["verdict_calls"] == 0
    assert not (output / "summary/arm-scores.json").exists()
    assert not (output / "summary/comparison.json").exists()


@pytest.mark.parametrize(
    "failure",
    ["malformed", "missing", "subprocess", "ordering-fields"],
)
def test_post_candidate_evidence_failures_are_canonical_invalid_without_scores(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _install_harness(monkeypatch, events=[], tamper=failure)

    output = _execute(tmp_path)

    verdict = json.loads((output / "summary/verdict.json").read_text(encoding="utf-8"))
    metadata = json.loads((output / "summary/run-metadata.json").read_text(encoding="utf-8"))
    assert verdict["verdict"] == "INVALID_EXPERIMENT"
    assert verdict["harness_status"] == "harness-invalid"
    assert metadata["evidenceStatus"] == "INVALID"
    assert metadata["evidenceErrors"]
    assert len(metadata["repeatEvidence"]) == 3
    assert state["verdict_calls"] == 0
    assert state["score_calls"] == 0
    assert not (output / "summary/arm-scores.json").exists()
    assert not (output / "summary/comparison.json").exists()
    assert not (output / "summary/exercise.json").exists()
    assert not list((output / "summary").glob("*/scores.json"))


def test_nonfinite_child_json_is_canonical_invalid_without_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _install_harness(monkeypatch, events=[])
    real_load = run_v3._load_output

    def nonfinite(path: Path) -> dict[str, object]:
        if path.name.endswith(".diagnostic.json"):
            path.write_text('{"value":NaN}', encoding="utf-8")
        return real_load(path)

    monkeypatch.setattr(run_v3, "_load_output", nonfinite)
    output = _execute(tmp_path)

    verdict = json.loads((output / "summary/verdict.json").read_text(encoding="utf-8"))
    assert verdict["verdict"] == "INVALID_EXPERIMENT"
    assert state["score_calls"] == 0
    assert not (output / "summary/exercise.json").exists()


def test_nonempty_output_is_rejected_after_preflight_without_candidate_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    _install_harness(monkeypatch, events=events)
    output = tmp_path / "output"
    output.mkdir()
    (output / "existing.txt").write_text("occupied", encoding="utf-8")

    with pytest.raises(RuntimeError, match="must not already exist"):
        _execute(tmp_path)

    assert events == ["preflight"]


def test_output_root_rejects_symlinked_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    _install_harness(monkeypatch, events=events)
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")

    with pytest.raises(RuntimeError, match="symlink"):
        run_v3.execute(
            corpus_root=tmp_path / "corpus",
            spec_path=tmp_path / "spec.json",
            experiment_id="experiment-v3",
            expected_spec_sha256="1" * 64,
            expected_methodology_sha256="2" * 64,
            expected_manifest_sha256="3" * 64,
            expected_design_sha256="4" * 64,
            qualification_identity="identity",
            execution_sha=EXECUTION_SHA,
            expected_tree_sha="5" * 40,
            output_root=linked_parent / "output",
        )
    assert events == ["preflight"]


@pytest.mark.parametrize("role", ["input", "annotation"])
def test_staged_corpus_is_a_byte_snapshot_when_original_role_is_replaced(
    role: str, tmp_path: Path
) -> None:
    corpus_root = tmp_path / "corpus"
    _write(corpus_root)
    manifest = json.loads((corpus_root / "manifest.json").read_text(encoding="utf-8"))
    relative = manifest["pages"][0][role]["file"]
    original = corpus_root / relative

    with run_v3._stage_sealed_corpus(corpus_root) as staged_root:
        staged = staged_root / relative
        expected = staged.read_bytes()
        original.write_bytes(b"replacement")
        assert staged.read_bytes() == expected
        assert staged_root != corpus_root

    assert not staged_root.exists()


def test_candidate_corpus_view_contains_only_manifest_design_inputs_and_images(
    tmp_path: Path,
) -> None:
    corpus_root = tmp_path / "corpus"
    _write(corpus_root)
    manifest = json.loads((corpus_root / "manifest.json").read_text(encoding="utf-8"))
    allowed = {
        "manifest.json",
        manifest["design"]["file"],
        *(page[role]["file"] for page in manifest["pages"] for role in ("input", "image")),
    }
    forbidden = {
        page[role]["file"]
        for page in manifest["pages"]
        for role in ("source", "annotation")
    }

    with run_v3._stage_sealed_corpus(corpus_root) as staged_root:
        with run_v3._stage_candidate_corpus(staged_root) as candidate_root:
            assert {
                path.relative_to(candidate_root).as_posix()
                for path in candidate_root.rglob("*")
                if path.is_file()
            } == allowed
            assert (candidate_root / "manifest.json").read_bytes() == (
                staged_root / "manifest.json"
            ).read_bytes()
            assert (candidate_root / "corpus-design.json").read_bytes() == (
                staged_root / "corpus-design.json"
            ).read_bytes()
            assert not set(candidate_root.rglob("*")) & {
                candidate_root / relative for relative in forbidden
            }
            for relative in forbidden:
                with pytest.raises(FileNotFoundError):
                    (candidate_root / relative).read_bytes()
        assert not candidate_root.exists()
    assert not staged_root.exists()


def test_candidate_attempting_to_find_or_read_annotations_cannot_access_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus_root = tmp_path / "corpus"
    _write(corpus_root)

    class CandidateProbeComplete(Exception):
        pass

    with (
        run_v3._stage_sealed_corpus(corpus_root) as staged_root,
        run_v3._stage_candidate_corpus(staged_root) as candidate_root,
    ):
        design = json.loads(
            (candidate_root / "corpus-design.json").read_text(encoding="utf-8")
        )
        page = design["pages"][0]

        def probing_candidate(*_args: object, **_kwargs: object) -> None:
            assert not [
                path
                for path in candidate_root.rglob("*")
                if "annotation" in path.name.lower()
            ]
            with pytest.raises(FileNotFoundError):
                (candidate_root / page["annotation"]).read_bytes()
            with pytest.raises(FileNotFoundError):
                (candidate_root / page["source"]).read_bytes()
            raise CandidateProbeComplete

        monkeypatch.setattr(
            run_v3.run_arm_v3,
            "_verify_candidate_origin",
            lambda: probing_candidate,
        )
        with pytest.raises(CandidateProbeComplete):
            run_v3.run_arm_v3.execute_page(
                corpus_root=candidate_root,
                page_id=page["pageId"],
                arm_id=ArmId.CONTROL,
                execution_sha=EXECUTION_SHA,
                repeat=1,
                output_root=tmp_path / "output",
            )


def test_synthetic_clean_room_corpus_runs_the_real_v3_flow_in_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus_root = tmp_path / "corpus"
    output_root = tmp_path / "output"
    experiment_id = "synthetic-v3-full-flow"
    expected_spec_sha256 = "1" * 64
    expected_methodology_sha256 = "2" * 64
    expected_manifest_sha256 = "3" * 64
    expected_design_sha256 = "4" * 64
    expected_tree_sha = "5" * 40
    qualification_identity = "synthetic-v3-qualification"
    _write(corpus_root)
    expected_manifest_sha256 = run_v3.sha256_bytes((corpus_root / "manifest.json").read_bytes())
    expected_design_sha256 = run_v3.sha256_bytes((corpus_root / "corpus-design.json").read_bytes())

    monkeypatch.setattr(
        run_v3,
        "validate_preflight_v3",
        lambda **_kwargs: SimpleNamespace(spec={"experimentId": experiment_id}),
    )

    candidate_roots: set[Path] = set()

    def execute_page_in_process(
        *,
        corpus_root: Path,
        page_id: str,
        arm: ArmId,
        execution_sha: str,
        repeat: int,
        output_root: Path,
    ) -> None:
        candidate_roots.add(corpus_root)
        design = json.loads((corpus_root / "corpus-design.json").read_text(encoding="utf-8"))
        assert not any(
            (corpus_root / page[role]).exists()
            for page in design["pages"]
            for role in ("source", "annotation")
        )
        run_v3.run_arm_v3.execute_page(
            corpus_root=corpus_root,
            page_id=page_id,
            arm_id=arm,
            execution_sha=execution_sha,
            repeat=repeat,
            output_root=output_root,
        )

    monkeypatch.setattr(run_v3, "_run_fresh_process", execute_page_in_process)

    run_v3.execute(
        corpus_root=corpus_root,
        spec_path=tmp_path / "unused-spec.json",
        experiment_id=experiment_id,
        expected_spec_sha256=expected_spec_sha256,
        expected_methodology_sha256=expected_methodology_sha256,
        expected_manifest_sha256=expected_manifest_sha256,
        expected_design_sha256=expected_design_sha256,
        qualification_identity=qualification_identity,
        execution_sha=EXECUTION_SHA,
        expected_tree_sha=expected_tree_sha,
        output_root=output_root,
    )

    page_ids = tuple(f"page-{index:02d}" for index in range(24))
    expected_raw_records = {
        Path(arm.value)
        / f"repeat-{repeat}"
        / f"{page_id}.{kind}.json"
        for arm in ArmId
        for repeat in (1, 2, 3)
        for page_id in page_ids
        for kind in ("diagnostic", "ordering")
    }
    raw_root = output_root / "raw"
    assert {path.relative_to(raw_root) for path in raw_root.rglob("*.json")} == (
        expected_raw_records
    )
    assert len(candidate_roots) == 1
    assert not next(iter(candidate_roots)).exists()

    repeat_hashes = json.loads(
        (output_root / "summary/repeat-hashes.json").read_text(encoding="utf-8")
    )
    assert len(repeat_hashes) == 3
    assert len({record["qualificationResultSha256"] for record in repeat_hashes}) == 1
    for arm in ArmId:
        arm_hashes = json.loads(
            (output_root / "summary" / arm.value / "repeat-hashes.json").read_text(
                encoding="utf-8"
            )
        )
        assert len(arm_hashes) == 3
        assert len({record["resultSha256"] for record in arm_hashes}) == 1

    metadata_path = output_root / "summary/run-metadata.json"
    metadata_bytes = metadata_path.read_bytes()
    metadata = json.loads(metadata_bytes)
    assert metadata_bytes == canonical_json_bytes(metadata)
    assert metadata == {
        **metadata,
        "experimentId": experiment_id,
        "qualificationIdentity": qualification_identity,
        "executionSha": EXECUTION_SHA,
        "executionTreeSha": expected_tree_sha,
        "specSha256": expected_spec_sha256,
        "methodologySha256": expected_methodology_sha256,
        "manifestSha256": expected_manifest_sha256,
        "designSha256": expected_design_sha256,
        "runnerModule": run_v3.RUNNER_MODULE,
        "armRunnerModule": run_v3.ARM_RUNNER_MODULE,
        "repeatHashSeeds": list(run_v3.HASH_SEEDS),
        "armOrder": [arm.value for arm in ArmId],
        "pageOrder": list(page_ids),
        "evidenceStatus": "VALID",
        "evidenceErrors": [],
        "qualificationResultSha256": repeat_hashes[0]["qualificationResultSha256"],
    }
    assert len(metadata["repeatEvidence"]) == 3
    assert all(
        evidence["arms"][arm.value]["status"] == "COLLECTED"
        for evidence in metadata["repeatEvidence"]
        for arm in ArmId
    )

    exercise = json.loads(
        (output_root / "summary/exercise.json").read_text(encoding="utf-8")
    )
    verdict = json.loads(
        (output_root / "summary/verdict.json").read_text(encoding="utf-8")
    )
    assert verdict["verdict"] != Verdict.INVALID_EXPERIMENT.value
    if any(
        exercise["counts"][name]["count"] < minimum
        for name, minimum in exercise["minima"].items()
    ):
        assert verdict["verdict"].endswith("_INCONCLUSIVE")


def test_real_fresh_process_is_authenticated_and_deterministic_across_hash_seeds(
    tmp_path: Path,
) -> None:
    corpus_root = tmp_path / "corpus"
    output_root = tmp_path / "output"
    _write(corpus_root)
    manifest_sha256 = run_v3.sha256_bytes((corpus_root / "manifest.json").read_bytes())
    design_sha256 = run_v3.sha256_bytes(
        (corpus_root / "corpus-design.json").read_bytes()
    )
    arm = ArmId.CONTROL
    assert run_v3.ARM_RUNNER_MODULE == (
        "scripts.reading_order_post_v2_qualification.run_arm_v3"
    )
    assert run_v3.HASH_SEEDS == (101, 202, 303)
    expected_python_path = os.pathsep.join(
        (str(run_v3.REPO_ROOT), str(run_v3.REPO_ROOT / "backend" / "src"))
    )

    normalized_artifacts: list[dict[Path, bytes]] = []
    with run_v3._stage_sealed_corpus(
        corpus_root,
        expected_manifest_sha256=manifest_sha256,
        expected_design_sha256=design_sha256,
    ) as staged_root:
        design = run_v3.load_design(staged_root / "corpus-design.json")
        annotations, _c3_rejection_page_ids = (
            run_v3.compat.load_clean_room_annotations(staged_root)
        )
        page_id = design.pages[0].page_id
        annotation = {page.page_id: page for page in annotations}[page_id]
        trusted = run_v3._trusted_page_inputs(staged_root, design)[page_id]

        with run_v3._stage_candidate_corpus(staged_root) as candidate_root:
            candidate_files = {
                path.relative_to(candidate_root).as_posix()
                for path in candidate_root.rglob("*")
                if path.is_file()
            }
            forbidden = {
                page_record.source for page_record in design.pages
            } | {page_record.annotation for page_record in design.pages}
            assert not candidate_files & forbidden
            assert all(not (candidate_root / relative).exists() for relative in forbidden)

            for repeat, seed in enumerate(run_v3.HASH_SEEDS, start=1):
                child_env = run_v3._child_environment(seed)
                assert {
                    key: value
                    for key, value in child_env.items()
                    if key.upper().startswith("PYTHON")
                } == {
                    "PYTHONPATH": expected_python_path,
                    "PYTHONNOUSERSITE": "1",
                    "PYTHONHASHSEED": str(seed),
                }
                error = run_v3._run_fresh_process(
                    corpus_root=candidate_root,
                    page_id=page_id,
                    arm=arm,
                    execution_sha=EXECUTION_SHA,
                    repeat=repeat,
                    output_root=output_root,
                )
                assert error is None

                repeat_root = output_root / "raw" / arm.value / f"repeat-{repeat}"
                paths = {
                    kind: repeat_root / f"{page_id}.{kind}.json"
                    for kind in ("diagnostic", "ordering")
                }
                parsed = {kind: run_v3._load_output(path) for kind, path in paths.items()}
                diagnostic = parsed["diagnostic"]
                ordering = parsed["ordering"]
                for document in parsed.values():
                    assert document["experimentArm"] == arm.value
                    assert document["pageId"] == page_id
                    assert document["executionSha"] == EXECUTION_SHA
                assert diagnostic["finalOrder"] == ordering["finalOrder"]

                problems: list[str] = []
                authenticated_sha = run_v3.exercise_v3._diagnostic(
                    diagnostic,
                    arm=arm,
                    page=annotation,
                    trusted=trusted,
                    problems=problems,
                )
                assert problems == []
                assert authenticated_sha == EXECUTION_SHA

                artifacts: dict[Path, bytes] = {}
                for path in paths.values():
                    payload = path.read_bytes()
                    assert payload == canonical_json_bytes(run_v3._load_output(path))
                    relative = path.relative_to(output_root)
                    normalized = Path(
                        *(
                            "repeat-N" if part == f"repeat-{repeat}" else part
                            for part in relative.parts
                        )
                    )
                    artifacts[normalized] = payload
                normalized_artifacts.append(artifacts)

    assert normalized_artifacts[0] == normalized_artifacts[1] == normalized_artifacts[2]

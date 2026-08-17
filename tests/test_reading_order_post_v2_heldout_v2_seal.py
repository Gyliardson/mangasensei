from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scripts.reading_order_post_v2_qualification.contracts import (
    EXERCISE_MINIMA,
    SLICE_MINIMA,
    load_arm_input,
    validate_corpus,
)
from scripts.reading_order_post_v2_qualification.png_integrity import (
    validate_corpus_image_integrity,
)
from scripts.reading_order_post_v2_qualification.retired_guard import (
    assert_no_retired_post_v2_v1_reuse,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_REL = Path("assets/reading-order-post-v2/heldout-v2")
EXPECTED_CORPUS_ID = "mangasensei-reading-order-post-v2-heldout-v2"
EXPECTED_VERSION = "1.0.0"
EXPECTED_AUTHORSHIP = "new-project-authored-no-historical-v2-case-reuse"
EXPECTED_DESIGN_SHA256 = "0f9588a200e4fc1072b7d56bd70154032600e2735e75db7d40e96248fb8337aa"
EXPECTED_MANIFEST_SHA256 = "7ab2593cf80fb13a0cc09fdcc0042609930ca028d194ae1b646f9f1165ac46f5"
EXPECTED_TOTAL_PAIRS = 180
EXPECTED_TOTAL_SCORED_REGIONS = 104
GIT = "/usr/bin/git"

EXERCISE_BINDINGS: dict[str, tuple[str, str]] = {
    "c1_guarded_pairs": ("c1-boundary-positive", "authored-exercise:c1-guarded"),
    "c2_gutter_pairs": ("c2-gutter-bridge", "authored-exercise:c2-gutter"),
    "c2_overlap_pairs": (
        "c2-ambiguous-overlap-bridge",
        "authored-exercise:c2-overlap",
    ),
    "c2_pair_precedence_pairs": (
        "c2-pair-precedence-slot",
        "authored-exercise:c2-pair-precedence",
    ),
    "c2_fail_closed_no_relation_pairs": (
        "c2-one-sided-non-unique-fail-closed",
        "authored-exercise:c2-fail-closed-no-relation",
    ),
    "c2_conflict_cycle_fallback_pairs": (
        "c2-conflict-cycle-safety",
        "authored-exercise:c2-conflict-cycle-fallback",
    ),
    "c3_positive_pairs": (
        "c3-positive-recovery",
        "authored-exercise:c3-positive",
    ),
    "c3_zero_multiple_anchor_rejection_pairs": (
        "c3-zero-multiple-anchor-negative",
        "authored-exercise:c3-anchor-rejection",
    ),
    "c3_zero_multiple_companion_rejection_pairs": (
        "c3-zero-multiple-companion-negative",
        "authored-exercise:c3-companion-rejection",
    ),
    "c3_invalid_topology_rejection_pairs": (
        "c3-invalid-topology-negative",
        "authored-exercise:c3-invalid-topology-rejection",
    ),
    "c3_insufficient_visible_support_rejection_pairs": (
        "c3-insufficient-visible-support-negative",
        "authored-exercise:c3-insufficient-support-rejection",
    ),
    "b1_horizontal_pairs": (
        "b1-horizontal",
        "authored-exercise:b1-pair-bound-orientation",
    ),
    "b1_vertical_pairs": (
        "b1-vertical",
        "authored-exercise:b1-pair-bound-orientation",
    ),
    "b1_mixed_pairs": (
        "b1-mixed-orientation",
        "authored-exercise:b1-pair-bound-orientation",
    ),
}


def _git_output(*args: str) -> bytes:
    # Immutable HEAD access is the purpose of this focused seal test; argv is repository-owned.
    result = subprocess.run(  # noqa: S603
        [GIT, *args],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def _materialize_committed_corpus(tmp_path: Path) -> Path:
    listing = _git_output("ls-tree", "-r", "--name-only", "HEAD", "--", CORPUS_REL.as_posix())
    tracked = tuple(line for line in listing.decode("utf-8").splitlines() if line)
    assert tracked
    assert all(path.startswith(f"{CORPUS_REL.as_posix()}/") for path in tracked)

    expected_relative = {"corpus-design.json", "manifest.json"}
    for page_id in (f"Q{index:03d}" for index in range(101, 125)):
        expected_relative.update(
            {
                f"images/{page_id}.png",
                f"inputs/{page_id}.json",
                f"annotations/{page_id}.json",
            }
        )
    actual_relative = {
        Path(repo_path).relative_to(CORPUS_REL).as_posix() for repo_path in tracked
    }
    assert actual_relative == expected_relative

    destination_root = tmp_path / CORPUS_REL
    for repo_path in tracked:
        relative = Path(repo_path).relative_to(CORPUS_REL)
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(_git_output("show", f"HEAD:{repo_path}"))
    return destination_root


def _load_json(path: Path) -> dict[str, Any]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert all(isinstance(key, str) for key in payload)
    return payload


def test_post_v2_heldout_v2_seal_is_candidate_independent_and_immutable(
    tmp_path: Path,
) -> None:
    corpus_root = _materialize_committed_corpus(tmp_path)
    design, manifest, pages = validate_corpus(corpus_root)

    assert design.corpus_id == EXPECTED_CORPUS_ID
    assert design.version == EXPECTED_VERSION
    assert manifest.corpus_id == EXPECTED_CORPUS_ID
    assert manifest.version == EXPECTED_VERSION
    assert design.page_ids == tuple(f"Q{index:03d}" for index in range(101, 125))

    design_path = corpus_root / "corpus-design.json"
    manifest_path = corpus_root / "manifest.json"
    assert hashlib.sha256(design_path.read_bytes()).hexdigest() == EXPECTED_DESIGN_SHA256
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == EXPECTED_MANIFEST_SHA256

    design_payload = _load_json(design_path)
    assert design_payload["authorshipBoundary"] == EXPECTED_AUTHORSHIP
    assert design_payload["pageIds"] == list(design.page_ids)

    validate_corpus_image_integrity(corpus_root, design.page_ids)
    assert_no_retired_post_v2_v1_reuse(corpus_root)

    total_pairs = sum(len(page.qualification_pairs) for page in pages)
    total_scored = sum(len(page.reading_order) for page in pages)
    assert total_pairs == EXPECTED_TOTAL_PAIRS
    assert total_scored == EXPECTED_TOTAL_SCORED_REGIONS

    pair_counts: Counter[str] = Counter()
    page_sets: defaultdict[str, set[str]] = defaultdict(set)
    exercise_counts: Counter[str] = Counter()

    for page in pages:
        assert int(page.page_id[1:]) >= 101
        input_record = load_arm_input(corpus_root / "inputs" / f"{page.page_id}.json")
        assert [region.source_index for region in input_record.regions] == list(
            range(len(input_record.regions))
        )
        input_ids = {region.region_id for region in input_record.regions}
        annotation_ids = set(page.reading_order) | set(page.unscored_region_ids)
        assert input_ids == annotation_ids
        assert set(page.reading_order).isdisjoint(page.unscored_region_ids)

        by_id = {region.region_id: region for region in input_record.regions}
        tags = set(page.layout_tags)
        for pair in page.qualification_pairs:
            for slice_name in pair.slices:
                pair_counts[slice_name] += 1
                page_sets[slice_name].add(page.page_id)

            for exercise_name, (slice_name, tag) in EXERCISE_BINDINGS.items():
                if slice_name not in pair.slices or tag not in tags:
                    continue
                if (
                    exercise_name == "c2_conflict_cycle_fallback_pairs"
                    and "intentional-fallback" not in pair.slices
                ):
                    continue
                if exercise_name == "b1_horizontal_pairs":
                    assert by_id[pair.earlier].angle == 0
                    assert by_id[pair.later].angle == 0
                elif exercise_name == "b1_vertical_pairs":
                    assert by_id[pair.earlier].angle == 90
                    assert by_id[pair.later].angle == 90
                elif exercise_name == "b1_mixed_pairs":
                    assert {by_id[pair.earlier].angle, by_id[pair.later].angle} == {0, 90}
                exercise_counts[exercise_name] += 1

    for slice_name, minima in SLICE_MINIMA.items():
        assert pair_counts[slice_name] >= minima["minPairs"]
        assert len(page_sets[slice_name]) >= minima["minPages"]

    assert len(page_sets["combined-c1-c2-c3-b1"]) >= 4
    assert len(page_sets["intentional-fallback"]) >= 3
    assert len(page_sets["clean-control"]) >= 6

    for exercise_name, minimum in EXERCISE_MINIMA.items():
        assert exercise_counts[exercise_name] >= minimum

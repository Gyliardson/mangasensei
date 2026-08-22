from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from shutil import which
from types import ModuleType
from typing import Any, cast

import numpy as np
from PIL import Image
from scripts.reading_order_v3_authoring import (
    AUTHORSHIP_BOUNDARY,
    DESIGN_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
)

from mangasensei.ocr.diagnostics import (
    reading_order_post_v2_calibration as candidate_module,
)
from mangasensei.ocr.reading_order import segment_panel_groups

from . import DIAGNOSTIC_SCHEMA_VERSION
from .canonical import write_canonical_json
from .contracts import ArmId
from .fixtures import build_textblock_regions
from .run_arm import _box, _config
from .v3_clean_room_compat import load_arm_input

REPO_ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_PATH = (
    REPO_ROOT
    / "backend"
    / "src"
    / "mangasensei"
    / "ocr"
    / "diagnostics"
    / "reading_order_post_v2_calibration.py"
).resolve()
CANDIDATE_REPO_PATH = CANDIDATE_PATH.relative_to(REPO_ROOT).as_posix()
_PAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class _PageAssets:
    corpus_root: Path
    corpus_id: str
    version: str
    design_path: Path
    design_sha256: str
    source_relative: str
    image_relative: str
    input_relative: str
    annotation_relative: str
    image_path: Path
    input_path: Path


def _head_candidate_bytes() -> bytes:
    git = which("git")
    if git is None:
        raise RuntimeError("git is required to authenticate frozen candidate source")
    result = subprocess.run(  # noqa: S603
        [git, "show", f"HEAD:{CANDIDATE_REPO_PATH}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return result.stdout


def _verify_candidate_origin() -> Callable[..., Any]:
    module_file = getattr(candidate_module, "__file__", None)
    if not isinstance(module_file, str) or Path(module_file).resolve() != CANDIDATE_PATH:
        raise RuntimeError("frozen candidate module source mismatch")
    source_bytes = CANDIDATE_PATH.read_bytes()
    head_bytes = _head_candidate_bytes()
    if source_bytes != head_bytes:
        raise RuntimeError("frozen candidate source does not match authenticated HEAD source")

    source_sha256 = hashlib.sha256(head_bytes).hexdigest()
    module_name = f"{candidate_module.__package__}._authenticated_{source_sha256}"
    authenticated_module = ModuleType(module_name)
    authenticated_module.__package__ = candidate_module.__package__
    authenticated_module.__file__ = str(CANDIDATE_PATH)
    code = compile(
        head_bytes,
        str(CANDIDATE_PATH),
        "exec",
        dont_inherit=True,
        optimize=sys.flags.optimize,
    )
    if module_name in sys.modules:
        raise RuntimeError("authenticated candidate namespace collision")
    sys.modules[module_name] = authenticated_module
    try:
        exec(code, authenticated_module.__dict__)  # noqa: S102 - authenticated HEAD source
    finally:
        sys.modules.pop(module_name, None)
    candidate = authenticated_module.__dict__.get("run_post_v2_calibration_candidate")
    if not callable(candidate):
        raise RuntimeError("authenticated candidate callable is missing")
    return cast(Callable[..., Any], candidate)


def _reject_symlink_components(path: Path, *, role: str) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"corpus-design {role}: symlinked path component is forbidden")


def _relative_asset_value(value: object, *, role: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError(f"corpus-design {role}: safe normalized POSIX relative path required")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or bool(windows.drive)
        or "." in posix.parts
        or ".." in posix.parts
        or posix.as_posix() != value
    ):
        raise ValueError(f"corpus-design {role}: safe normalized POSIX relative path required")
    return value


def _canonical_asset_path(corpus_root: Path, value: object, *, role: str) -> Path:
    relative = _relative_asset_value(value, role=role)
    root_absolute = corpus_root.absolute()
    _reject_symlink_components(root_absolute, role=role)
    candidate = root_absolute / Path(*PurePosixPath(relative).parts)
    _reject_symlink_components(candidate, role=role)
    root = root_absolute.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"corpus-design {role}: path escapes corpus root")
    return resolved


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_page_assets(corpus_root: Path, page_id: str) -> _PageAssets:
    design_path = _canonical_asset_path(corpus_root, "corpus-design.json", role="design")
    try:
        design_bytes = design_path.read_bytes()
        design = json.loads(design_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{design_path}: invalid clean-room corpus design") from exc
    if not isinstance(design, dict):
        raise ValueError("corpus-design: object required")
    if design.get("schemaVersion") != DESIGN_SCHEMA_VERSION:
        raise ValueError("corpus-design: bad schema version")
    if design.get("authorshipBoundary") != AUTHORSHIP_BOUNDARY:
        raise ValueError("corpus-design: wrong authorship boundary")
    corpus_id = design.get("corpusId")
    version = design.get("version")
    if not isinstance(corpus_id, str) or not isinstance(version, str):
        raise ValueError("corpus-design: string corpus identity required")
    pages = design.get("pages")
    if not isinstance(pages, list):
        raise ValueError("corpus-design.pages: array required")
    matches = [page for page in pages if isinstance(page, dict) and page.get("pageId") == page_id]
    if len(matches) != 1:
        raise ValueError(f"requested page {page_id!r} must occur exactly once in corpus-design")
    record = matches[0]
    source_relative = _relative_asset_value(record.get("source"), role="source")
    image_relative = _relative_asset_value(record.get("image"), role="image")
    input_relative = _relative_asset_value(record.get("input"), role="input")
    annotation_relative = _relative_asset_value(record.get("annotation"), role="annotation")
    return _PageAssets(
        corpus_root=corpus_root,
        corpus_id=corpus_id,
        version=version,
        design_path=design_path,
        design_sha256=hashlib.sha256(design_bytes).hexdigest(),
        source_relative=source_relative,
        image_relative=image_relative,
        input_relative=input_relative,
        annotation_relative=annotation_relative,
        image_path=_canonical_asset_path(corpus_root, image_relative, role="image"),
        input_path=_canonical_asset_path(corpus_root, input_relative, role="input"),
    )


def _exact_keys(value: object, expected: set[str], *, where: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"manifest {where}: exact property set required")
    return value


def _manifest_role(
    value: object,
    *,
    page_id: str,
    role: str,
    expected_file: str,
) -> str:
    record = _exact_keys(value, {"file", "sha256"}, where=f"{page_id}.{role}")
    if record["file"] != expected_file:
        raise ValueError(f"manifest {page_id} {role}: canonical role path mismatch")
    digest = record["sha256"]
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"manifest {page_id} {role}: lowercase SHA-256 required")
    return digest


def _verify_sealed_page(
    assets: _PageAssets,
    page_id: str,
    *,
    manifest_identity: bytes | None,
) -> bytes:
    manifest_path = _canonical_asset_path(assets.corpus_root, "manifest.json", role="manifest")
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("manifest: invalid sealed manifest") from exc
    if manifest_identity is not None and manifest_bytes != manifest_identity:
        raise ValueError("manifest: sealed manifest identity changed during page execution")
    top = _exact_keys(
        manifest,
        {"schemaVersion", "corpusId", "version", "design", "pages", "inventory"},
        where="root",
    )
    if top["schemaVersion"] != MANIFEST_SCHEMA_VERSION:
        raise ValueError("manifest: bad schema version")
    if top["corpusId"] != assets.corpus_id or top["version"] != assets.version:
        raise ValueError("manifest: corpus identity mismatch")
    design_digest = _manifest_role(
        top["design"],
        page_id="design",
        role="record",
        expected_file="corpus-design.json",
    )
    if design_digest != assets.design_sha256 or _sha256_path(assets.design_path) != design_digest:
        raise ValueError("manifest design SHA-256 mismatch")
    if not isinstance(top["inventory"], list) or not isinstance(top["pages"], list):
        raise ValueError("manifest: pages and inventory arrays required")
    matches = [
        page
        for page in top["pages"]
        if isinstance(page, dict) and page.get("pageId") == page_id
    ]
    if len(matches) != 1:
        raise ValueError(f"manifest: exact page record required for {page_id}")
    page = _exact_keys(
        matches[0],
        {"pageId", "source", "image", "input", "annotation"},
        where=f"page {page_id}",
    )
    _manifest_role(
        page["source"], page_id=page_id, role="source", expected_file=assets.source_relative
    )
    image_digest = _manifest_role(
        page["image"], page_id=page_id, role="image", expected_file=assets.image_relative
    )
    input_digest = _manifest_role(
        page["input"], page_id=page_id, role="input", expected_file=assets.input_relative
    )
    _manifest_role(
        page["annotation"],
        page_id=page_id,
        role="annotation",
        expected_file=assets.annotation_relative,
    )
    if _sha256_path(assets.input_path) != input_digest:
        raise ValueError(f"manifest {page_id} input SHA-256 mismatch")
    if _sha256_path(assets.image_path) != image_digest:
        raise ValueError(f"manifest {page_id} image SHA-256 mismatch")
    return manifest_bytes


def _array_from_rgb_image(image: Image.Image) -> np.ndarray:
    pixels: np.ndarray = np.asarray(image)
    return pixels


def _decode_rgb_image(path: Path, *, width: int, height: int, page_id: str) -> np.ndarray:
    with Image.open(path) as image:
        if image.mode != "RGB":
            raise ValueError(f"{page_id}: image must be RGB")
        pixels = _array_from_rgb_image(image)
    if pixels.dtype != np.uint8:
        raise ValueError(f"{page_id}: decoded image must have uint8 dtype")
    if pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError(f"{page_id}: decoded image must have HxWx3 shape")
    if pixels.shape != (height, width, 3):
        raise ValueError(f"{page_id}: image/input dimensions disagree")
    return pixels


def _freeze(value: object) -> object:
    if isinstance(value, np.ndarray):
        return ("ndarray", value.dtype.str, value.shape, value.tobytes())
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    if hasattr(value, "__dict__"):
        return _freeze(vars(value))
    return value


def _full_snapshot(regions: tuple[Any, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            ref.region_id,
            ref.source_index,
            tuple(int(value) for value in ref.region.xyxy),
            _freeze(vars(ref.region)),
        )
        for ref in regions
    )


def _assert_result_integrity(
    regions: tuple[Any, ...], result: Any, before: tuple[tuple[object, ...], ...]
) -> tuple[str, ...]:
    if _full_snapshot(regions) != before:
        raise AssertionError("candidate modified frozen input region state")
    ordered = result.ordered_regions
    if len(ordered) != len(regions):
        raise AssertionError("candidate changed region count")
    if {id(ref.region) for ref in ordered} != {id(ref.region) for ref in regions}:
        raise AssertionError("candidate changed region object identity set")
    if {ref.region_id for ref in ordered} != {ref.region_id for ref in regions}:
        raise AssertionError("candidate changed stable region ID set")
    expected_identity = {
        id(ref.region): (ref.region_id, ref.source_index) for ref in regions
    }
    actual_identity = {
        id(ref.region): (ref.region_id, ref.source_index) for ref in ordered
    }
    if actual_identity != expected_identity:
        raise AssertionError("candidate changed region ID/sourceIndex identity mapping")

    final_order = tuple(ref.region_id for ref in ordered)
    if tuple(result.diagnostic.final_order) != final_order:
        raise AssertionError("candidate result and diagnostic final order disagree")
    assignments = result.diagnostic.assignments
    expected_assignment_count = len(regions) if result.diagnostic.segmentation_reliable else 0
    if len(assignments) != expected_assignment_count:
        raise AssertionError("candidate diagnostic assignment count disagrees with segmentation")
    if assignments and any(
        assignment.region_id != ref.region_id
        for assignment, ref in zip(assignments, regions, strict=True)
    ):
        raise AssertionError("candidate diagnostic assignment IDs disagree with input")
    return final_order


def execute_page(
    *,
    corpus_root: Path,
    page_id: str,
    arm_id: ArmId,
    execution_sha: str,
    repeat: int,
    output_root: Path,
) -> tuple[Path, Path]:
    if _PAGE_ID_RE.fullmatch(page_id) is None:
        raise ValueError("page-id must be a safe page ID")
    if repeat not in {1, 2, 3}:
        raise ValueError("repeat must be 1, 2, or 3")
    assets = _resolve_page_assets(corpus_root, page_id)
    manifest_identity = _verify_sealed_page(assets, page_id, manifest_identity=None)
    page = load_arm_input(assets.input_path)
    _verify_sealed_page(assets, page_id, manifest_identity=manifest_identity)
    if page.page_id != page_id:
        raise ValueError(f"{page_id}: input pageId mismatch")
    _verify_sealed_page(assets, page_id, manifest_identity=manifest_identity)
    pixels = _decode_rgb_image(
        assets.image_path,
        width=page.width,
        height=page.height,
        page_id=page_id,
    )
    _verify_sealed_page(assets, page_id, manifest_identity=manifest_identity)
    regions = build_textblock_regions(page)
    before = _full_snapshot(regions)

    pre_segmentation = segment_panel_groups(pixels)
    authenticated_candidate = _verify_candidate_origin()
    result = authenticated_candidate(
        pixels,
        regions,
        page_height=page.height,
        config=_config(arm_id),
    )
    final_order = _assert_result_integrity(regions, result, before)

    assignments: list[dict[str, object]] = []
    for region_index, assignment in enumerate(result.diagnostic.assignments):
        ref = regions[region_index]
        assignments.append(
            {
                "regionId": assignment.region_id,
                "sourceIndex": ref.source_index,
                "candidateGroupIndices": list(assignment.candidate_group_indices),
                "status": assignment.status,
                "reason": assignment.reason,
                "assignedGroupIndex": assignment.assigned_group_index,
                "uncertainNodeLabel": (
                    f"u{region_index:03d}" if assignment.assigned_group_index is None else None
                ),
            }
        )

    diagnostic = {
        "schemaVersion": DIAGNOSTIC_SCHEMA_VERSION,
        "experimentArm": arm_id.value,
        "executionSha": execution_sha,
        "pageId": page_id,
        "preSegmentation": {
            "reliable": pre_segmentation.reliable,
            "reason": pre_segmentation.reason,
            "boxCount": len(pre_segmentation.boxes),
            "boxes": [_box(box) for box in pre_segmentation.boxes],
        },
        "segmentation": {
            "reliable": result.diagnostic.segmentation_reliable,
            "reason": result.diagnostic.segmentation_reason,
            "boxes": [_box(box) for box in result.diagnostic.segmentation_boxes],
        },
        "recoveryReason": result.diagnostic.recovery_reason,
        "assignments": assignments,
        "relationEdges": [
            {
                "sourceNode": edge.source_node,
                "targetNode": edge.target_node,
                "rule": edge.rule,
            }
            for edge in result.diagnostic.relation_edges
        ],
        "nodeOrder": list(result.diagnostic.node_order),
        "fallbackReason": result.diagnostic.fallback_reason,
        "usedPanelEvidence": result.diagnostic.used_panel_evidence,
        "fallbackOrder": list(result.diagnostic.fallback_order),
        "finalOrder": list(final_order),
        "regionDirections": {
            ref.region_id: str(getattr(ref.region, "direction", "")) for ref in regions
        },
        "regionIntegrity": {
            "countPreserved": True,
            "objectIdentitySetPreserved": True,
            "contentConfidenceGeometryPreserved": True,
        },
    }
    ordering = {
        "schemaVersion": "reading-order-post-v2-ordering-v1",
        "experimentArm": arm_id.value,
        "executionSha": execution_sha,
        "pageId": page_id,
        "finalOrder": list(final_order),
    }
    arm_root = output_root / "raw" / arm_id.value / f"repeat-{repeat}"
    diagnostic_path = arm_root / f"{page_id}.diagnostic.json"
    ordering_path = arm_root / f"{page_id}.ordering.json"
    write_canonical_json(diagnostic_path, diagnostic)
    write_canonical_json(ordering_path, ordering)
    return diagnostic_path, ordering_path


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run one frozen post-v2 arm page from a clean-room v3 corpus"
    )
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--arm", choices=[arm.value for arm in ArmId], required=True)
    parser.add_argument("--execution-sha", required=True)
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    execute_page(
        corpus_root=args.corpus_root,
        page_id=args.page_id,
        arm_id=ArmId(args.arm),
        execution_sha=args.execution_sha,
        repeat=args.repeat,
        output_root=args.output_root,
    )


if __name__ == "__main__":
    main()

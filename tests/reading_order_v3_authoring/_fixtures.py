from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any, cast

from scripts.reading_order_v3_authoring import (
    ANNOTATION_SCHEMA_VERSION,
    AUTHORSHIP_BOUNDARY,
    DESIGN_SCHEMA_VERSION,
    INPUT_SCHEMA_VERSION,
    POSITIVE_FAMILIES,
    write_canonical_json,
    write_manifest,
)
from scripts.reading_order_v3_authoring.contracts import (
    PageAnnotation,
    load_annotation,
    load_design,
)

SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(data, zlib.crc32(kind)) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def _png(*, interlace: int = 0) -> bytes:
    ihdr = struct.pack(">IIBBBBB", 7, 5, 8, 2, 0, 0, interlace)
    raw = b"".join(b"\0" + bytes([row]) * 21 for row in range(5))
    return SIG + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")


def _slices(i: int) -> list[str]:
    s = {"clean-control"}
    for lo, hi, names in (
        (0, 3, ("c1-boundary-positive", "b1-horizontal")),
        (4, 7, ("c1-near-boundary-negative", "b1-vertical")),
        (8, 10, ("c2-gutter-bridge", "c2-ambiguous-overlap-bridge", "c2-pair-precedence-slot")),
        (11, 13, ("c2-one-sided-non-unique-fail-closed",)),
        (14, 15, ("c2-conflict-cycle-safety",)),
        (16, 19, ("c3-positive-recovery",)),
        (12, 15, ("b1-mixed-orientation",)),
        (8, 11, ("combined-c1-c2-c3-b1",)),
        (18, 20, ("intentional-fallback",)),
    ):
        if lo <= i <= hi:
            s.update(names)
    return sorted(s)


def _design(ids: tuple[str, ...] | None = None) -> dict[str, Any]:
    ids = ids or tuple(f"page-{i:02d}" for i in range(24))
    pages = []
    for i, page_id in enumerate(ids):
        family = POSITIVE_FAMILIES[i] if i < 8 else None
        pages.append({
            "pageId": page_id,
            "source": f"source/{page_id}.bin",
            "image": f"images/{page_id}.png",
            "input": f"inputs/{page_id}.json",
            "annotation": f"annotations/{page_id}.json",
            "authoringCoverage": {
                "positiveFamilies": [] if family is None else [family],
                "primaryPositiveFamily": family,
                "c3Rejection": i >= 16,
            },
        })
    return {
        "schemaVersion": DESIGN_SCHEMA_VERSION,
        "corpusId": "fresh-neutral-corpus",
        "version": "draft-v3.1",
        "authorshipBoundary": AUTHORSHIP_BOUNDARY,
        "provenanceDeclaration": {
            "priorHeldoutEvidenceInspected": False,
            "calibrationOutputsInspected": False,
            "candidateDiagnosticsInspected": False,
            "candidateExecuted": False,
            "qualificationExecuted": False,
            "annotationsAdaptedToCandidateOutput": False,
        },
        "pages": pages,
    }


def _page(page_id: str, i: int) -> tuple[dict[str, Any], dict[str, Any]]:
    ids = [f"region {i}:{j}" for j in range(4)]
    regions = [{
        "regionId": rid,
        "sourceIndex": j,
        "lines": [[[j, 0], [j + 1, 0], [j + 1, 1], [j, 1]]],
        "angle": float(j),
    } for j, rid in enumerate(ids)]
    ends = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3))
    pairs = [{
        "id": f"pair-{i:02d}-{n}",
        "earlier": ids[a],
        "later": ids[b],
        "slices": _slices(i),
    } for n, (a, b) in enumerate(ends)]
    inp = {
        "schemaVersion": INPUT_SCHEMA_VERSION,
        "pageId": page_id,
        "width": 7,
        "height": 5,
        "regions": regions,
    }
    ann = {
        "schemaVersion": ANNOTATION_SCHEMA_VERSION,
        "pageId": page_id,
        "readingOrder": ids,
        "unscoredRegionIds": [],
        "qualificationPairs": pairs,
    }
    return inp, ann


def _write(root: Path, design: dict[str, Any] | None = None, *, seal: bool = True) -> None:
    design = design or _design()
    write_canonical_json(root / "corpus-design.json", design)
    for i, page in enumerate(cast(list[dict[str, Any]], design["pages"])):
        paths = {r: root / page[r] for r in ("source", "image", "input", "annotation")}
        for path in paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        paths["source"].write_bytes(b"opaque\n")
        paths["image"].write_bytes(_png())
        inp, ann = _page(page["pageId"], i)
        write_canonical_json(paths["input"], inp)
        write_canonical_json(paths["annotation"], ann)
    if seal:
        write_manifest(root)


def _annotations(root: Path) -> tuple[Any, dict[str, PageAnnotation]]:
    design = load_design(root / "corpus-design.json")
    anns = {p.page_id: load_annotation(root / p.annotation) for p in design.pages}
    return design, anns

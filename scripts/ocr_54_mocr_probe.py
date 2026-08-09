from __future__ import annotations

import json
import os
import sys
import unicodedata
from pathlib import Path
from typing import Any

from huggingface_hub import snapshot_download
from manga_ocr import MangaOcr

MODEL_REPO = "kha-white/manga-ocr-base"
MODEL_REVISION = "aa6573bd10b0d446cbf622e29c3e084914df9741"


def _normalize(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _primary_texts(crop: dict[str, Any]) -> list[str]:
    variants = crop.get("primary_variants")
    if not isinstance(variants, dict):
        return []
    texts: list[str] = []
    for record in variants.values():
        if not isinstance(record, dict):
            continue
        text = str(record.get("text", "")).strip()
        if text:
            texts.append(text)
    return texts


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: ocr_54_mocr_probe.py <research-matrix.json>")

    matrix_path = Path(sys.argv[1]).resolve()
    root = matrix_path.parent
    payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    crops = payload.get("fallback_crops")
    if not isinstance(crops, list):
        raise RuntimeError("research matrix has no fallback_crops list")

    hf_home = Path(os.environ.get("HF_HOME", root / "hf-home"))
    model_dir = snapshot_download(
        repo_id=MODEL_REPO,
        revision=MODEL_REVISION,
        cache_dir=hf_home,
    )
    recognizer = MangaOcr(pretrained_model_name_or_path=model_dir, force_cpu=True)

    results: list[dict[str, Any]] = []
    nonempty_count = 0
    line_agreement_count = 0
    line_crop_count = 0
    anchor_recovery_count = 0
    anchor_block_count = 0

    for crop in crops:
        if not isinstance(crop, dict):
            continue
        crop_path = root / str(crop["path"])
        text = str(recognizer(str(crop_path))).strip()
        normalized = _normalize(text)
        nonempty_count += int(bool(text))

        primary_texts = _primary_texts(crop)
        primary_normalized = {_normalize(value) for value in primary_texts}
        agrees_with_primary = bool(normalized and normalized in primary_normalized)
        if crop.get("kind") == "line":
            line_crop_count += 1
            line_agreement_count += int(agrees_with_primary)

        anchor = crop.get("anchor")
        anchor_present = False
        if isinstance(anchor, str) and anchor:
            anchor_block_count += 1
            anchor_present = _normalize(anchor) in normalized
            anchor_recovery_count += int(anchor_present)

        results.append(
            {
                "id": crop.get("id"),
                "case": crop.get("case"),
                "kind": crop.get("kind"),
                "path": crop.get("path"),
                "text": text,
                "text_length": len(text),
                "normalized_text": normalized,
                "agrees_exactly_with_a_primary_variant": agrees_with_primary,
                "primary_variant_count": len(set(primary_normalized)),
                "anchor_present": anchor_present,
            }
        )

    output = {
        "research_contract": "ocr54-manga-ocr-fallback-v1",
        "package_version": "0.1.16",
        "model_repo": MODEL_REPO,
        "model_revision": MODEL_REVISION,
        "crop_count": len(results),
        "nonempty_count": nonempty_count,
        "line_crop_count": line_crop_count,
        "line_exact_primary_agreement_count": line_agreement_count,
        "anchor_block_count": anchor_block_count,
        "anchor_recovery_count": anchor_recovery_count,
        "results": results,
    }
    (root / "mocr-results.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        "OCR54_MOCR_COMPLETE "
        f"crops={len(results)} nonempty={nonempty_count} "
        f"line_exact_agreements={line_agreement_count}/{line_crop_count} "
        f"anchor_blocks={anchor_recovery_count}/{anchor_block_count}"
    )


if __name__ == "__main__":
    main()

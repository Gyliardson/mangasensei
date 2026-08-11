from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "assets" / "public-demo"
MANIFEST = CORPUS / "manifest.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_png(path: Path) -> None:
    with Image.open(path) as image:
        if image.size != (1440, 2048):
            raise SystemExit(f"wrong render dimensions: {path}: {image.size}")
        rgb = image.convert("RGB")
        rgb.save(path, format="PNG", optimize=False, compress_level=9)
    with Image.open(path) as checked:
        if (
            checked.mode != "RGB"
            or checked.size != (1440, 2048)
            or getattr(checked, "n_frames", 1) != 1
        ):
            raise SystemExit(f"non-canonical PNG: {path}")


def chromium_version() -> str:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("node executable is required to resolve the Playwright Chromium version")
    try:
        # `node` is resolved to an executable path and every remaining argument is a
        # source-controlled constant. No untrusted input reaches this subprocess.
        result = subprocess.run(  # noqa: S603
            [
                node,
                "-e",
                (
                    "import('playwright').then(async({chromium})=>{"
                    "const b=await chromium.launch({headless:true});"
                    "console.log(b.version());await b.close();})"
                ),
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"could not determine Playwright Chromium version: {exc}") from exc
    return result.stdout.strip().splitlines()[-1]


def main() -> None:
    page_ids = [
        "msdemo-001-station",
        "msdemo-002-library",
        "msdemo-003-laboratory",
        "msdemo-004-rain",
    ]
    for page_id in page_ids:
        image = CORPUS / "images" / f"{page_id}.png"
        normalize_png(image)
        annotation = CORPUS / "annotations" / f"{page_id}.json"
        data = json.loads(annotation.read_text(encoding="utf-8"))
        data["page"]["imageSha256"] = digest(image)
        write_json(annotation, data)

    toolchain_path = CORPUS / "provenance" / "toolchain.json"
    toolchain = json.loads(toolchain_path.read_text(encoding="utf-8"))
    toolchain["renderedChromiumVersion"] = chromium_version()
    write_json(toolchain_path, toolchain)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for page in manifest["pages"]:
        page["source"]["sha256"] = digest(CORPUS / page["source"]["file"])
        page["image"]["sha256"] = digest(CORPUS / page["image"]["file"])
        page["annotation"]["sha256"] = digest(CORPUS / page["annotation"]["file"])
    inventory = []
    for file in sorted(p for p in CORPUS.rglob("*") if p.is_file() and p != MANIFEST):
        inventory.append(
            {
                "file": file.relative_to(CORPUS).as_posix(),
                "sha256": digest(file),
                "bytes": file.stat().st_size,
            }
        )
    manifest["inventory"] = inventory
    write_json(MANIFEST, manifest)
    print(f"froze {len(page_ids)} pages and {len(inventory)} manifest inventory files")


if __name__ == "__main__":
    main()

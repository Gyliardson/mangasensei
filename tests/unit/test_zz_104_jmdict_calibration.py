from __future__ import annotations

import pytest

from mangasensei.linguistics.jmdict_packs import default_pack_registry_path
from scripts.update_jmdict_manifest import update_manifests


@pytest.mark.asyncio
async def test_calibrate_reviewed_german_pack_metadata() -> None:
    await update_manifests(
        default_pack_registry_path(),
        languages=("de",),
        check=True,
    )

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from mangasensei.application.pdf_imports import PdfImportCoordinator, _ClaimedImport
from mangasensei.domain.languages import StudyLanguage
from mangasensei.pdf_imports.spool import PdfSpoolError


def test_stale_renderer_manifest_fence_is_rejected_before_raster_consumption() -> None:
    import_id = uuid4()
    claim = _ClaimedImport(
        internal_id=1,
        public_id=import_id,
        fencing_token=2,
        source_sha256="00" * 32,
        study_language=StudyLanguage("pt-BR"),
    )
    stale_manifest = cast(
        Any,
        SimpleNamespace(
            import_id=import_id,
            fencing_token=1,
        ),
    )
    coordinator = object.__new__(PdfImportCoordinator)

    with pytest.raises(PdfSpoolError, match="identity or renderer provenance"):
        coordinator._validate_manifest(claim, stale_manifest)

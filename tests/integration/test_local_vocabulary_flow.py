from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from mangasensei.api.app import create_app
from mangasensei.config import Settings
from mangasensei.domain.models import BoundingBox, PageDimensions
from mangasensei.gemini.contracts import GeminiPageAnalysis, GeminiRegionAnalysis
from mangasensei.infrastructure.database.session import create_database
from mangasensei.linguistics.service import (
    DictionaryEntry,
    DictionaryLookupResult,
    LexicalFormIdentity,
    LinguisticService,
)
from mangasensei.ocr.contracts import OcrImage, OcrRegionResult, OcrResult
from mangasensei.ocr.fake import DEFAULT_FAKE_PROVENANCE
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import Worker

REGION_ID = "5ca22b32-6834-59db-a183-428a557a22e8"


def fixture_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 120), color=(248, 244, 235)).save(output, format="PNG")
    return output.getvalue()


class LocalVocabularyOcrFixture:
    async def analyze(self, image: OcrImage) -> OcrResult:
        dimensions = PageDimensions(width=80, height=120)
        bbox = BoundingBox(x=10, y=20, width=40, height=60)
        return OcrResult(
            image_sha256=image.sha256,
            provenance=DEFAULT_FAKE_PROVENANCE,
            regions=(
                OcrRegionResult(
                    id=REGION_ID,
                    dimensions=dimensions,
                    bbox=bbox,
                    normalized_bbox=bbox.normalize(dimensions),
                    polygon=((10, 20), (50, 20), (50, 80), (10, 80)),
                    angle=0.0,
                    confidence=0.97,
                    japanese_text="猫猫犬です",
                    reading_order=0,
                    detector="fixture",
                    recognizer="fixture",
                    upstream_commit="95227a2bb0fd306cd4f0c104d57284026f991b3a",
                ),
            ),
        )


class LocalVocabularyTokenizerFixture:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        assert text == "猫猫犬です"
        return (
            ("猫", "猫", "ネコ", "名詞"),
            ("猫", "猫", "ネコ", "名詞"),
            ("犬", "犬", "イヌ", "名詞"),
            ("です", "です", "デス", "助動詞"),
        )


class LocalVocabularyDictionaryFixture:
    version = "JMdict local-first test"
    digest = hashlib.sha256(version.encode()).digest()

    def lookup_candidates(self, lemma: str, reading: str) -> DictionaryLookupResult:
        del reading
        if lemma == "猫":
            return _result(
                DictionaryEntry(
                    identity=LexicalFormIdentity(
                        "JMdict",
                        "jmdict-1467640",
                        "猫",
                        "ねこ",
                    ),
                    meanings=("cat",),
                    source="JMdict local-first test",
                    jlpt_level="N5",
                    jlpt_official=False,
                )
            )
        if lemma == "犬":
            return _result(
                DictionaryEntry(
                    identity=LexicalFormIdentity(
                        "JMdict",
                        "jmdict-1186080",
                        "犬",
                        "いぬ",
                    ),
                    meanings=("dog",),
                    source="JMdict local-first test",
                    jlpt_level="N5",
                    jlpt_official=False,
                )
            )
        if lemma == "猫猫":
            return _result(
                DictionaryEntry(
                    identity=LexicalFormIdentity(
                        "JMdict",
                        "jmdict-multi-token-fixture",
                        "猫猫",
                        "ねこねこ",
                    ),
                    meanings=("paired cats",),
                    source="JMdict local-first test",
                    jlpt_level=None,
                    jlpt_official=False,
                )
            )
        if lemma == "犬です":
            return DictionaryLookupResult.from_candidates(
                (
                    DictionaryEntry(
                        identity=LexicalFormIdentity(
                            "JMdict",
                            "jmdict-ambiguous-a",
                            "犬です",
                            "いぬです",
                        ),
                        meanings=("ambiguous A",),
                        source="JMdict local-first test",
                        jlpt_level=None,
                        jlpt_official=False,
                    ),
                    DictionaryEntry(
                        identity=LexicalFormIdentity(
                            "JMdict",
                            "jmdict-ambiguous-b",
                            "犬です",
                            "いぬです",
                        ),
                        meanings=("ambiguous B",),
                        source="JMdict local-first test",
                        jlpt_level=None,
                        jlpt_official=False,
                    ),
                )
            )
        return DictionaryLookupResult.from_candidates(())


def _result(entry: DictionaryEntry) -> DictionaryLookupResult:
    return DictionaryLookupResult.from_candidates((entry,))


class PartialGeminiFixture:
    async def analyze(
        self, *, prompt: str, schema: type[GeminiPageAnalysis]
    ) -> GeminiPageAnalysis:
        payload = json.loads(prompt)
        candidates = payload["regions"][0]["vocabulary_candidates"]
        assert "猫猫犬です" in prompt
        assert [candidate["surface"] for candidate in candidates] == ["猫猫", "猫", "犬"]
        assert all(candidate["surface"] != "犬です" for candidate in candidates)
        return schema(
            regions=(
                GeminiRegionAnalysis(
                    region_id=REGION_ID,
                    translation="There are cats and a dog.",
                    explanation="Contextual fixture analysis.",
                    grammar_points=("です",),
                    vocabulary_ids=(candidates[0]["id"],),
                ),
            )
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_no_gemini_page_exposes_local_vocabulary_in_token_order(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    data = await _process_page(clean_postgres_url, tmp_path, gemini=None)
    region = data["regions"][0]

    assert data["status"] == "completed"
    assert region["translation"] is None
    assert region["explanation"] is None
    assert region["grammar"] == []
    assert region["vocabulary"] == expected_local_vocabulary()
    assert [token["dictionaryId"] for token in region["tokens"]] == [
        "jmdict-1467640",
        "jmdict-1467640",
        "jmdict-1186080",
        None,
    ]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gemini_links_do_not_filter_or_reorder_local_vocabulary(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    data = await _process_page(
        clean_postgres_url,
        tmp_path,
        gemini=PartialGeminiFixture(),
    )
    region = data["regions"][0]

    assert region["translation"] == "There are cats and a dog."
    assert region["explanation"] == "Contextual fixture analysis."
    assert region["grammar"] == ["です"]
    assert region["vocabulary"] == expected_local_vocabulary()


async def _process_page(
    database_url: str,
    root: Path,
    *,
    gemini: Any,
) -> dict[str, Any]:
    application_settings = Settings(
        environment="test",
        database_url=database_url,
        storage_root=root,
        capability_peppers=("local-vocabulary-flow-pepper-00000001",),
    )
    app = create_app(application_settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "local-vocabulary-flow-0001"},
            files={"image": ("page.png", fixture_image(), "image/png")},
        )
        upload_data = upload.json()["data"]

        engine, sessions = create_database(database_url)
        worker = Worker(
            sessions=sessions,
            storage=LocalFilesystemStorage(root),
            ocr=LocalVocabularyOcrFixture(),
            linguistics=LinguisticService(
                LocalVocabularyTokenizerFixture(),
                LocalVocabularyDictionaryFixture(),
            ),
            gemini=gemini,
            worker_id="local-vocabulary-worker",
            lease_seconds=60,
        )
        try:
            assert await worker.run_once()
            result = await client.get(
                f"/api/v1/pages/{upload_data['pageId']}",
                headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
            )
            assert result.status_code == 200
            return result.json()["data"]
        finally:
            await engine.dispose()


def expected_local_vocabulary() -> list[dict[str, object]]:
    return [
        {
            "id": "jmdict-multi-token-fixture",
            "surface": "猫猫",
            "lemma": "猫猫",
            "reading": "ネコネコ",
            "meanings": ["paired cats"],
            "source": "JMdict local-first test",
            "jlpt": None,
        },
        {
            "id": "jmdict-1467640",
            "surface": "猫",
            "lemma": "猫",
            "reading": "ネコ",
            "meanings": ["cat"],
            "source": "JMdict local-first test",
            "jlpt": {"level": "N5", "official": False},
        },
        {
            "id": "jmdict-1186080",
            "surface": "犬",
            "lemma": "犬",
            "reading": "イヌ",
            "meanings": ["dog"],
            "source": "JMdict local-first test",
            "jlpt": {"level": "N5", "official": False},
        },
    ]

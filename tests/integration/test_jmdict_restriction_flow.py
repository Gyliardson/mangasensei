from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from mangasensei.api.app import create_app
from mangasensei.config import Settings
from mangasensei.domain.models import BoundingBox, PageDimensions
from mangasensei.gemini.contracts import GeminiPageAnalysis, GeminiRegionAnalysis
from mangasensei.infrastructure.database.session import create_database
from mangasensei.linguistics.jmdict import JsonJmdictDictionary
from mangasensei.linguistics.jmdict_bootstrap import convert_simplified_jmdict
from mangasensei.linguistics.service import LinguisticService
from mangasensei.ocr.contracts import OcrImage, OcrRegionResult, OcrResult
from mangasensei.ocr.fake import DEFAULT_FAKE_PROVENANCE
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import Worker

REGION_ID = "5ca22b32-6834-59db-a183-428a557a22e8"


def fixture_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 120), color=(248, 244, 235)).save(output, format="PNG")
    return output.getvalue()


class RestrictedOcrFixture:
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
                    japanese_text="半平",
                    reading_order=0,
                    detector="fixture",
                    recognizer="fixture",
                    upstream_commit="95227a2bb0fd306cd4f0c104d57284026f991b3a",
                ),
            ),
        )


class RestrictedTokenizerFixture:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        assert text == "半平"
        return (("半平", "半平", "ハンペイ", "名詞"),)


class RestrictedGeminiFixture:
    async def analyze(self, *, prompt: str, schema: type[GeminiPageAnalysis]) -> GeminiPageAnalysis:
        payload = json.loads(prompt)
        vocabulary_id = payload["regions"][0]["vocabulary_candidates"][0]["id"]
        return schema(
            regions=(
                GeminiRegionAnalysis(
                    region_id=REGION_ID,
                    translation="Fish cake.",
                    explanation="Fixture explanation.",
                    grammar_points=(),
                    vocabulary_ids=(vocabulary_id,),
                ),
            )
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_restricted_jmdict_meanings_propagate_to_page_response(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    dictionary_path = tmp_path / "jmdict.json"
    dictionary_path.write_bytes(
        convert_simplified_jmdict(
            actual_restricted_payload(),
            version="jmdict-simplified-3.6.2+test",
            language="eng",
            source_url="https://example.test/jmdict-eng.json.zip",
            license_id="CC-BY-SA-4.0",
            attribution="JMdict data provided by EDRDG",
        )
    )
    dictionary = JsonJmdictDictionary(dictionary_path)
    application_settings = Settings(
        environment="test",
        database_url=clean_postgres_url,
        storage_root=tmp_path,
        capability_peppers=("jmdict-restriction-flow-pepper-00000001",),
    )
    app = create_app(application_settings)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "jmdict-restriction-flow-0001"},
            files={"image": ("page.png", fixture_image(), "image/png")},
        )
        upload_data = upload.json()["data"]

        engine, sessions = create_database(clean_postgres_url)
        worker = Worker(
            sessions=sessions,
            storage=LocalFilesystemStorage(tmp_path),
            ocr=RestrictedOcrFixture(),
            linguistics=LinguisticService(RestrictedTokenizerFixture(), dictionary),
            gemini=RestrictedGeminiFixture(),
            worker_id="jmdict-restriction-worker",
            lease_seconds=60,
        )

        assert await worker.run_once()

        result = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        assert result.status_code == 200
        data = result.json()["data"]
        assert data["status"] == "completed"
        assert data["regions"][0]["vocabulary"] == [
            {
                "id": "jmdict-1010230",
                "surface": "半平",
                "lemma": "半平",
                "reading": "ハンペイ",
                "meanings": ["pounded fish cake"],
                "source": "JMdict jmdict-simplified-3.6.2+test",
                "effectiveLanguage": "en",
                "fallbackUsed": False,
                "fallbackReason": None,
                "sourceRef": (
                    f"jmdict:en:{dictionary.version}:{dictionary.digest.hex()[:16]}"
                ),
                "jlpt": None,
            }
        ]
        await engine.dispose()


def actual_restricted_payload() -> dict[str, object]:
    # Derived from real pinned JMdict entry 1010230. The second sense applies
    # only to 半片 and must therefore not reach the 半平 / はんぺい page token.
    return {
        "words": [
            {
                "id": "1010230",
                "kanji": [
                    {"text": "半片", "common": False, "tags": []},
                    {"text": "半平", "common": False, "tags": []},
                ],
                "kana": [
                    {
                        "text": "はんぺん",
                        "common": True,
                        "tags": [],
                        "appliesToKanji": ["*"],
                    },
                    {
                        "text": "はんぺい",
                        "common": False,
                        "tags": [],
                        "appliesToKanji": ["半平"],
                    },
                ],
                "sense": [
                    sense(["pounded fish cake"]),
                    sense(
                        ["half a slice", "half a ticket", "ticket stub"],
                        applies_to_kanji=["半片"],
                    ),
                ],
            }
        ]
    }


def sense(
    glosses: list[str], *, applies_to_kanji: list[str] | None = None
) -> dict[str, object]:
    return {
        "gloss": [
            {"lang": "eng", "text": text, "gender": None, "type": None}
            for text in glosses
        ],
        "appliesToKanji": applies_to_kanji or ["*"],
        "appliesToKana": ["*"],
        "dialect": [],
        "field": [],
        "info": [],
        "languageSource": [],
        "misc": [],
        "partOfSpeech": ["n"],
        "related": [],
        "antonym": [],
    }

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import func, select

from mangasensei.api.app import create_app
from mangasensei.config import Settings
from mangasensei.domain.models import BoundingBox, PageDimensions
from mangasensei.infrastructure.database.analysis_models import (
    GeminiCallRecord,
    LinguisticRunRecord,
    OcrRunRecord,
)
from mangasensei.infrastructure.database.dictionary_projection_models import (
    DictionaryProjectionItemRecord,
    DictionaryProjectionRecord,
)
from mangasensei.infrastructure.database.lexical_models import LexicalMatchRecord
from mangasensei.infrastructure.database.session import create_database
from mangasensei.infrastructure.database.study_models import StudyResultRecord
from mangasensei.linguistics.jmdict_glosses import (
    JmdictGlossLookup,
    JmdictGlossLookupStatus,
    JmdictGlossSourceReference,
    LocalizedJmdictGlossResolver,
)
from mangasensei.linguistics.service import (
    DictionaryEntry,
    DictionaryLookupResult,
    LexicalFormIdentity,
    LexicalHypothesis,
    LinguisticService,
)
from mangasensei.ocr.contracts import OcrImage, OcrRegionResult, OcrResult
from mangasensei.ocr.fake import DEFAULT_FAKE_PROVENANCE
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.dictionary_projection import DictionaryProjectionWorker

_REGION_ID = "ad78b2d1-8aa4-55d2-8584-106f61182328"
_VERSION = "JMdict projection fixture 20260810"
_EN_DIGEST = hashlib.sha256(b"projection-en").digest()


def _image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 120), color=(248, 244, 235)).save(output, format="PNG")
    return output.getvalue()


class CountingOcr:
    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, image: OcrImage) -> OcrResult:
        self.calls += 1
        dimensions = PageDimensions(width=80, height=120)
        bbox = BoundingBox(x=10, y=20, width=40, height=60)
        return OcrResult(
            image_sha256=image.sha256,
            provenance=DEFAULT_FAKE_PROVENANCE,
            regions=(
                OcrRegionResult(
                    id=_REGION_ID,
                    dimensions=dimensions,
                    bbox=bbox,
                    normalized_bbox=bbox.normalize(dimensions),
                    polygon=((10, 20), (50, 20), (50, 80), (10, 80)),
                    angle=0.0,
                    confidence=0.97,
                    japanese_text="猫犬行く",
                    reading_order=0,
                    detector="fixture",
                    recognizer="fixture",
                    upstream_commit="95227a2bb0fd306cd4f0c104d57284026f991b3a",
                ),
            ),
        )


class CountingTokenizer:
    def __init__(self) -> None:
        self.calls = 0

    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        self.calls += 1
        assert text == "猫犬行く"
        return (
            ("猫", "猫", "ネコ", "名詞"),
            ("犬", "犬", "イヌ", "名詞"),
            ("行く", "行く", "イク", "動詞"),
        )

    def lexical_hypotheses(
        self, text: str, *, max_span_tokens: int
    ) -> tuple[LexicalHypothesis, ...]:
        assert text == "猫犬行く"
        assert max_span_tokens >= 2
        return (LexicalHypothesis(0, 2, "猫犬", "ねこいぬ"),)


class EnglishDictionary:
    version = _VERSION
    digest = _EN_DIGEST

    _entries = {
        ("猫", "ねこ"): ("jmdict-cat", ("cat",)),
        ("犬", "いぬ"): ("jmdict-dog", ("dog",)),
        ("行く", "いく"): ("jmdict-go", ("to go",)),
        ("猫犬", "ねこいぬ"): ("jmdict-catdog", ("cat-dog compound",)),
    }

    def lookup_candidates(self, lemma: str, reading: str) -> DictionaryLookupResult:
        key = (lemma, _hiragana(reading))
        raw = self._entries.get(key)
        if raw is None:
            return DictionaryLookupResult.from_candidates(())
        entry_id, meanings = raw
        return DictionaryLookupResult.from_candidates(
            (
                DictionaryEntry(
                    identity=LexicalFormIdentity(
                        dictionary_namespace="JMdict",
                        entry_id=entry_id,
                        lemma=lemma,
                        reading=key[1],
                    ),
                    meanings=meanings,
                    source=_VERSION,
                    jlpt_level=None,
                    jlpt_official=False,
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class _FixturePack:
    language: str
    source: JmdictGlossSourceReference

    def lookup_identity(self, identity: LexicalFormIdentity) -> JmdictGlossLookup:
        if self.language == "en":
            meanings = {
                "jmdict-cat": ("cat",),
                "jmdict-dog": ("dog",),
                "jmdict-go": ("to go",),
                "jmdict-catdog": ("cat-dog compound",),
            }[identity.entry_id]
            status = JmdictGlossLookupStatus.FOUND
        elif identity.entry_id in {"jmdict-cat", "jmdict-catdog"}:
            meanings = {
                "jmdict-cat": ("Katze",),
                "jmdict-catdog": ("Katzenhund",),
            }[identity.entry_id]
            status = JmdictGlossLookupStatus.FOUND
        elif identity.entry_id == "jmdict-go":
            meanings = ()
            status = JmdictGlossLookupStatus.FORM_NOT_FOUND
        else:
            meanings = ()
            status = JmdictGlossLookupStatus.ENTRY_NOT_FOUND
        return JmdictGlossLookup(
            identity=identity,
            status=status,
            meanings=meanings,
            source=self.source,
        )


class _Provider:
    def __init__(self) -> None:
        self.loads: list[str] = []
        self._packs = {
            "en": _FixturePack(
                "en",
                JmdictGlossSourceReference(
                    dataset="JMdict",
                    language="en",
                    version=_VERSION,
                    digest_sha256=_EN_DIGEST.hex(),
                ),
            ),
            "de": _FixturePack(
                "de",
                JmdictGlossSourceReference(
                    dataset="JMdict",
                    language="de",
                    version=_VERSION,
                    digest_sha256=hashlib.sha256(b"projection-de").hexdigest(),
                ),
            ),
        }

    def is_supported_language(self, language: str) -> bool:
        return language in self._packs

    def get_pack(self, language: str) -> _FixturePack:
        self.loads.append(language)
        return self._packs[language]


def _hiragana(reading: str) -> str:
    return "".join(chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char for char in reading)


def _settings(database_url: str, root: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        storage_root=root,
        model_cache=root / "models",
        capability_peppers=("dictionary-projection-test-pepper-000001",),
    )


def _worker(
    database_url: str,
    root: Path,
    *,
    ocr: CountingOcr,
    tokenizer: CountingTokenizer,
    provider: _Provider,
) -> tuple[DictionaryProjectionWorker, object]:
    engine, sessions = create_database(database_url)
    worker = DictionaryProjectionWorker(
        sessions=sessions,
        storage=LocalFilesystemStorage(root),
        ocr=ocr,
        linguistics=LinguisticService(tokenizer, EnglishDictionary()),
        gemini=None,
        worker_id="dictionary-projection-worker",
        lease_seconds=60,
        gloss_resolver=LocalizedJmdictGlossResolver(provider),
    )
    return worker, engine


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dictionary_reprojection_is_local_durable_mixed_and_axis_independent(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    app = create_app(_settings(clean_postgres_url, tmp_path))
    ocr = CountingOcr()
    tokenizer = CountingTokenizer()
    provider = _Provider()
    worker, engine = _worker(
        clean_postgres_url,
        tmp_path,
        ocr=ocr,
        tokenizer=tokenizer,
        provider=provider,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "dictionary-upload-0001"},
            files={
                "image": ("page.png", _image(), "image/png"),
                "studyLanguage": (None, "en"),
            },
        )
        assert upload.status_code == 202
        upload_data = upload.json()["data"]
        assert await worker.run_once()

        original = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        original_data = original.json()["data"]
        assert original_data["studyLanguage"] == "en"
        assert original_data["dictionaryLanguage"] == "en"
        assert original_data["requestedDictionaryLanguage"] == "en"
        assert original_data["fallbackDictionaryLanguage"] == "en"
        assert all(
            item["effectiveLanguage"] == "en"
            and item["fallbackUsed"] is False
            and item["fallbackReason"] is None
            for item in original_data["regions"][0]["vocabulary"]
        )

        de_request = await client.post(
            f"/api/v1/pages/{upload_data['pageId']}/reprocess",
            headers={
                "X-Page-Token": upload_data["capabilities"]["reprocessPage"],
                "Idempotency-Key": "dictionary-de-0001",
            },
            json={"dictionaryLanguage": "de"},
        )
        assert de_request.status_code == 202
        de_job_id = de_request.json()["data"]["jobId"]
        assert de_request.json()["data"]["studyLanguage"] == "en"
        assert de_request.json()["data"]["requestedDictionaryLanguage"] == "de"

        duplicate = await client.post(
            f"/api/v1/pages/{upload_data['pageId']}/reprocess",
            headers={
                "X-Page-Token": upload_data["capabilities"]["reprocessPage"],
                "Idempotency-Key": "dictionary-de-0001",
            },
            json={"dictionaryLanguage": "de"},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["data"]["jobId"] == de_job_id
        assert duplicate.json()["data"]["created"] is False

        pending = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        assert pending.json()["data"]["resultAvailable"] is True
        assert pending.json()["data"]["requestedDictionaryLanguage"] == "en"

        assert await worker.run_once()
        de_page = (
            await client.get(
                f"/api/v1/pages/{upload_data['pageId']}",
                headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
            )
        ).json()["data"]
        by_id = {item["id"]: item for item in de_page["regions"][0]["vocabulary"]}
        assert de_page["studyLanguage"] == "en"
        assert de_page["dictionaryLanguage"] == "en"
        assert de_page["requestedDictionaryLanguage"] == "de"
        assert de_page["fallbackDictionaryLanguage"] == "en"
        assert by_id["jmdict-cat"]["meanings"] == ["Katze"]
        assert by_id["jmdict-cat"]["effectiveLanguage"] == "de"
        assert by_id["jmdict-cat"]["fallbackUsed"] is False
        assert by_id["jmdict-dog"]["meanings"] == ["dog"]
        assert by_id["jmdict-dog"]["effectiveLanguage"] == "en"
        assert by_id["jmdict-dog"]["fallbackReason"] == "requested_entry_not_found"
        assert by_id["jmdict-go"]["meanings"] == ["to go"]
        assert by_id["jmdict-go"]["fallbackReason"] == "requested_form_not_found"
        assert by_id["jmdict-catdog"]["meanings"] == ["Katzenhund"]
        assert {source["productLanguage"] for source in de_page["dictionarySources"]} == {
            "de",
            "en",
        }

        pt_request = await client.post(
            f"/api/v1/pages/{upload_data['pageId']}/reprocess",
            headers={
                "X-Page-Token": upload_data["capabilities"]["reprocessPage"],
                "Idempotency-Key": "dictionary-ptbr-0001",
            },
            json={"dictionaryLanguage": "pt-BR"},
        )
        assert pt_request.status_code == 202
        assert await worker.run_once()
        pt_page = (
            await client.get(
                f"/api/v1/pages/{upload_data['pageId']}",
                headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
            )
        ).json()["data"]
        assert pt_page["studyLanguage"] == "en"
        assert pt_page["requestedDictionaryLanguage"] == "pt-BR"
        assert {source["productLanguage"] for source in pt_page["dictionarySources"]} == {"en"}
        assert all(
            item["effectiveLanguage"] == "en"
            and item["fallbackUsed"] is True
            and item["fallbackReason"] == "unsupported_requested_language"
            for item in pt_page["regions"][0]["vocabulary"]
        )

        study_request = await client.post(
            f"/api/v1/pages/{upload_data['pageId']}/reprocess",
            headers={
                "X-Page-Token": upload_data["capabilities"]["reprocessPage"],
                "Idempotency-Key": "study-after-dictionary-0001",
            },
            json={"studyLanguage": "pt-BR"},
        )
        assert study_request.status_code == 202
        assert await worker.run_once()
        after_study = (
            await client.get(
                f"/api/v1/pages/{upload_data['pageId']}",
                headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
            )
        ).json()["data"]
        assert after_study["studyLanguage"] == "pt-BR"
        assert after_study["requestedDictionaryLanguage"] == "pt-BR"

        invalid = await client.post(
            f"/api/v1/pages/{upload_data['pageId']}/reprocess",
            headers={
                "X-Page-Token": upload_data["capabilities"]["reprocessPage"],
                "Idempotency-Key": "dictionary-invalid-0001",
            },
            json={"dictionaryLanguage": "es"},
        )
        assert invalid.status_code == 422
        both = await client.post(
            f"/api/v1/pages/{upload_data['pageId']}/reprocess",
            headers={
                "X-Page-Token": upload_data["capabilities"]["reprocessPage"],
                "Idempotency-Key": "dictionary-both-0001",
            },
            json={"studyLanguage": "en", "dictionaryLanguage": "de"},
        )
        assert both.status_code == 422

    _, sessions = create_database(clean_postgres_url)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(OcrRunRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(LinguisticRunRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(GeminiCallRecord)) == 0
        assert await session.scalar(select(func.count()).select_from(StudyResultRecord)) == 2
        assert await session.scalar(select(func.count()).select_from(DictionaryProjectionRecord)) == 2
        assert await session.scalar(select(func.count()).select_from(DictionaryProjectionItemRecord)) == 8
        identities = tuple(
            (
                await session.execute(
                    select(
                        LexicalMatchRecord.dictionary_namespace,
                        LexicalMatchRecord.dictionary_entry_id,
                        LexicalMatchRecord.form_lemma,
                        LexicalMatchRecord.form_reading,
                    ).order_by(LexicalMatchRecord.id)
                )
            ).all()
        )
        assert {identity[1] for identity in identities} >= {
            "jmdict-cat",
            "jmdict-dog",
            "jmdict-go",
            "jmdict-catdog",
        }
    assert ocr.calls == 1
    assert tokenizer.calls == 1
    # German is only touched by the explicit German projection; pt-BR asks English directly.
    assert "de" in provider.loads
    await engine.dispose()  # type: ignore[attr-defined]

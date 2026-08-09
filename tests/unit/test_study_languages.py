import json

import pytest

from mangasensei.domain.languages import (
    CONTENT_LANGUAGE,
    DEFAULT_STUDY_LANGUAGE,
    LOCAL_DICTIONARY_LANGUAGE,
    StudyLanguage,
)
from mangasensei.gemini.service import build_page_prompt


def test_language_contract_is_explicit_and_backward_compatible() -> None:
    assert CONTENT_LANGUAGE.value == "ja"
    assert DEFAULT_STUDY_LANGUAGE is StudyLanguage.PORTUGUESE_BRAZIL
    assert LOCAL_DICTIONARY_LANGUAGE.value == "en"
    assert {language.value for language in StudyLanguage} == {"pt-BR", "en"}


def test_unsupported_study_language_is_rejected() -> None:
    with pytest.raises(ValueError):
        StudyLanguage("es")


def test_gemini_prompt_contains_structured_english_study_language() -> None:
    prompt = build_page_prompt(
        prompt_version="test-v1",
        regions={"region-1": "猫です"},
        vocabulary_by_region={"region-1": ()},
        study_language=StudyLanguage.ENGLISH,
    )

    payload = json.loads(prompt)
    assert payload["study_language"] == "en"
    assert payload["regions"][0]["japanese_text"] == "猫です"


def test_gemini_prompt_defaults_to_portuguese_brazil() -> None:
    prompt = build_page_prompt(
        prompt_version="test-v1",
        regions={"region-1": "猫です"},
        vocabulary_by_region={"region-1": ()},
    )

    assert json.loads(prompt)["study_language"] == "pt-BR"

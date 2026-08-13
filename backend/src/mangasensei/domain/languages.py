"""Explicit language contracts for Japanese-content study flows."""

from enum import StrEnum


class ContentLanguage(StrEnum):
    JAPANESE = "ja"


class StudyLanguage(StrEnum):
    PORTUGUESE_BRAZIL = "pt-BR"
    ENGLISH = "en"


class DictionaryLanguage(StrEnum):
    ENGLISH = "en"


CONTENT_LANGUAGE = ContentLanguage.JAPANESE
DEFAULT_STUDY_LANGUAGE = StudyLanguage.PORTUGUESE_BRAZIL
DEFAULT_DICTIONARY_LANGUAGE = DictionaryLanguage.ENGLISH
FALLBACK_DICTIONARY_LANGUAGE = DictionaryLanguage.ENGLISH
# Backward-compatible English-only field used by historical StudyResult records.
LOCAL_DICTIONARY_LANGUAGE = DictionaryLanguage.ENGLISH

from __future__ import annotations

import copy
import os
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from mangasensei.config import Settings
from mangasensei.domain.languages import StudyLanguage
from mangasensei.gemini.adapter import GoogleGenAiAdapter
from mangasensei.gemini.contracts import GeminiPageAnalysis
from mangasensei.gemini.errors import GeminiProviderError, GeminiResponseError
from mangasensei.gemini.service import PAGE_STUDY_PROMPT_VERSION, build_page_prompt


class _SmokeStatus(BaseModel):
    status: str


class _SmokeNestedItem(BaseModel):
    value: str


class _SmokeNestedEnvelope(BaseModel):
    items: tuple[_SmokeNestedItem, ...]


class _ProductionSchemaProxy:
    _provider_schema: ClassVar[dict[str, Any]] = {}

    @classmethod
    def use_provider_schema(cls, schema: dict[str, Any]) -> None:
        cls._provider_schema = copy.deepcopy(schema)

    @classmethod
    def model_json_schema(cls) -> dict[str, Any]:
        return copy.deepcopy(cls._provider_schema)

    @classmethod
    def model_validate_json(cls, value: str) -> GeminiPageAnalysis:
        return GeminiPageAnalysis.model_validate_json(value)


@pytest.mark.gemini_smoke
@pytest.mark.asyncio
async def test_real_gemini_interactions_production_shaped_structured_output() -> None:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("GOOGLE_API_KEY is not configured; real Gemini smoke was not executed")

    settings = Settings(_env_file=None)
    prompt = build_page_prompt(
        prompt_version=PAGE_STUDY_PROMPT_VERSION,
        regions={"synthetic-region-001": "テストです"},
        vocabulary_by_region={"synthetic-region-001": ()},
        study_language=StudyLanguage.ENGLISH,
    )
    production_adapter = GoogleGenAiAdapter(
        model=settings.gemini_model,
        api_key=api_key,
        timeout_seconds=30,
        max_attempts=1,
    )
    production_failure: GeminiProviderError | None = None
    try:
        try:
            result = await production_adapter.analyze(
                prompt=prompt,
                schema=GeminiPageAnalysis,
            )
        except GeminiProviderError as exc:
            production_failure = exc
            _print_provider_failure("production_default", exc)
        else:
            print("GEMINI_DIAGNOSTIC production_default=pass")
            assert len(result.regions) == 1
            assert result.regions[0].region_id == "synthetic-region-001"
            return
    finally:
        await production_adapter.close()

    diagnostics = await _run_schema_diagnostics(
        api_key=api_key,
        model=settings.gemini_model,
        production_prompt=prompt,
    )
    assert production_failure is not None
    pytest.fail(
        "production-shaped Gemini request was rejected; "
        f"safe differential results: {', '.join(diagnostics)}",
        pytrace=False,
    )


async def _run_schema_diagnostics(
    *,
    api_key: str,
    model: str,
    production_prompt: str,
) -> list[str]:
    adapter = GoogleGenAiAdapter(
        model=model,
        api_key=api_key,
        timeout_seconds=30,
        max_attempts=1,
        max_output_tokens=128,
    )
    results: list[str] = []
    try:
        flat_accepted = await _diagnostic_call(
            adapter=adapter,
            label="control_flat_128",
            prompt='Return exactly one JSON object with status set to "ok".',
            schema=_SmokeStatus,
            results=results,
        )
        if not flat_accepted:
            return results

        await _diagnostic_call(
            adapter=adapter,
            label="control_nested_refs_128",
            prompt='Return exactly {"items":[{"value":"ok"}]}.',
            schema=_SmokeNestedEnvelope,
            results=results,
        )

        raw_production_schema = GeminiPageAnalysis.model_json_schema()
        _ProductionSchemaProxy.use_provider_schema(raw_production_schema)
        current_accepted = await _diagnostic_call(
            adapter=adapter,
            label="production_current_128",
            prompt=production_prompt,
            schema=_ProductionSchemaProxy,
            results=results,
        )
        if current_accepted:
            return results

        inlined = _inline_local_defs(raw_production_schema)
        if await _call_production_variant(
            adapter=adapter,
            label="production_inline_refs_128",
            prompt=production_prompt,
            provider_schema=inlined,
            results=results,
        ):
            return results

        without_max_items = _remove_schema_keys(inlined, frozenset({"maxItems"}))
        if await _call_production_variant(
            adapter=adapter,
            label="production_inline_no_max_items_128",
            prompt=production_prompt,
            provider_schema=without_max_items,
            results=results,
        ):
            return results

        without_titles = _remove_schema_keys(without_max_items, frozenset({"title"}))
        if await _call_production_variant(
            adapter=adapter,
            label="production_inline_no_titles_128",
            prompt=production_prompt,
            provider_schema=without_titles,
            results=results,
        ):
            return results

        without_additional_properties = _remove_schema_keys(
            without_titles,
            frozenset({"additionalProperties"}),
        )
        await _call_production_variant(
            adapter=adapter,
            label="production_inline_no_additional_properties_128",
            prompt=production_prompt,
            provider_schema=without_additional_properties,
            results=results,
        )
        return results
    finally:
        await adapter.close()


async def _call_production_variant(
    *,
    adapter: GoogleGenAiAdapter,
    label: str,
    prompt: str,
    provider_schema: dict[str, Any],
    results: list[str],
) -> bool:
    _ProductionSchemaProxy.use_provider_schema(provider_schema)
    return await _diagnostic_call(
        adapter=adapter,
        label=label,
        prompt=prompt,
        schema=_ProductionSchemaProxy,
        results=results,
    )


async def _diagnostic_call(
    *,
    adapter: GoogleGenAiAdapter,
    label: str,
    prompt: str,
    schema: Any,
    results: list[str],
) -> bool:
    try:
        await adapter.analyze(prompt=prompt, schema=schema)
    except GeminiProviderError as exc:
        outcome = f"provider_{exc.status_code or 'unknown'}_{exc.kind.value}"
        results.append(f"{label}:{outcome}")
        _print_provider_failure(label, exc)
        return False
    except GeminiResponseError:
        outcome = "provider_accepted_response_validation_failed"
        results.append(f"{label}:{outcome}")
        print(f"GEMINI_DIAGNOSTIC {label}={outcome}")
        return True
    else:
        results.append(f"{label}:pass")
        print(f"GEMINI_DIAGNOSTIC {label}=pass")
        return True


def _print_provider_failure(label: str, exc: GeminiProviderError) -> None:
    status = exc.status_code if exc.status_code is not None else "unknown"
    print(
        "GEMINI_DIAGNOSTIC "
        f"{label}=provider_error status={status} kind={exc.kind.value} "
        f"retryable={str(exc.retryable).lower()}"
    )


def _inline_local_defs(schema: dict[str, Any]) -> dict[str, Any]:
    root = copy.deepcopy(schema)
    definitions = root.pop("$defs", {})
    if not isinstance(definitions, dict):
        return root

    def expand(value: Any, stack: tuple[str, ...] = ()) -> Any:
        if isinstance(value, list):
            return [expand(item, stack) for item in value]
        if not isinstance(value, dict):
            return value

        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            name = reference.removeprefix("#/$defs/")
            target = definitions.get(name)
            if isinstance(target, dict) and name not in stack:
                expanded = expand(copy.deepcopy(target), (*stack, name))
                if isinstance(expanded, dict):
                    merged = dict(expanded)
                    merged.update(
                        {
                            key: expand(item, stack)
                            for key, item in value.items()
                            if key != "$ref"
                        }
                    )
                    return merged

        return {key: expand(item, stack) for key, item in value.items()}

    expanded_root = expand(root)
    assert isinstance(expanded_root, dict)
    return expanded_root


def _remove_schema_keys(value: Any, keys: frozenset[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _remove_schema_keys(item, keys)
            for key, item in value.items()
            if key not in keys
        }
    if isinstance(value, list):
        return [_remove_schema_keys(item, keys) for item in value]
    return value

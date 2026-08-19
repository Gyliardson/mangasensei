"""Candidate-independent clean-room tooling for Reading Order v3 corpus authoring."""

from .canonical import sha256_path, write_canonical_json
from .contracts import (
    ANNOTATION_SCHEMA_VERSION,
    AUTHORING_SLICES,
    AUTHORSHIP_BOUNDARY,
    C3_REJECTION_FAMILY,
    DESIGN_MINIMA,
    DESIGN_SCHEMA_VERSION,
    INPUT_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    POSITIVE_FAMILIES,
    SLICE_MINIMA,
    ContractError,
    CoverageSummary,
)
from .validate import build_manifest, validate_corpus, validate_rgb_png, write_manifest

__all__ = [
    "ANNOTATION_SCHEMA_VERSION",
    "AUTHORING_SLICES",
    "AUTHORSHIP_BOUNDARY",
    "C3_REJECTION_FAMILY",
    "DESIGN_MINIMA",
    "DESIGN_SCHEMA_VERSION",
    "INPUT_SCHEMA_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "POSITIVE_FAMILIES",
    "SLICE_MINIMA",
    "ContractError",
    "CoverageSummary",
    "build_manifest",
    "sha256_path",
    "validate_corpus",
    "validate_rgb_png",
    "write_canonical_json",
    "write_manifest",
]

"""Versioned filesystem protocol between PDF import coordinator and renderer."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

PDF_RASTER_CONTRACT_VERSION = "pdfium-raster-v1"
PDF_RENDER_DPI = 200
PDF_RENDER_SCALE = PDF_RENDER_DPI / 72.0
PDF_OUTPUT_MEDIA_TYPE = "image/png"
PDF_OUTPUT_BACKGROUND_RGBA = (255, 255, 255, 255)
PDF_PNG_COMPRESS_LEVEL = 6
PDF_PNG_OPTIMIZE = False
PDFIUM_EXPECTED_BUILD = 7947
PYPDFIUM2_EXPECTED_VERSION = "5.12.1"

PdfImportErrorCode = Literal[
    "pdf_invalid",
    "pdf_encrypted_unsupported",
    "pdf_page_limit",
    "pdf_geometry_limit",
    "pdf_pixel_limit",
    "pdf_raster_bytes_limit",
    "pdf_renderer_timeout",
    "pdf_renderer_crash",
    "pdf_temp_storage_exhausted",
    "pdf_render_failed",
    "pdf_raster_validation_failed",
    "pdf_manifest_invalid",
]


class _ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PdfRenderRequest(_ProtocolModel):
    protocol: Literal["mangasensei-pdf-render-request-v1"] = "mangasensei-pdf-render-request-v1"
    import_id: UUID
    fencing_token: int = Field(ge=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raster_contract: Literal["pdfium-raster-v1"] = PDF_RASTER_CONTRACT_VERSION
    max_pages: int = Field(ge=1, le=200)
    max_side: int = Field(ge=1, le=10_000)
    max_page_pixels: int = Field(ge=1, le=25_000_000)
    max_aggregate_pixels: int = Field(ge=1, le=1_000_000_000)
    max_page_raster_bytes: int = Field(ge=1, le=12 * 1024 * 1024)
    max_aggregate_raster_bytes: int = Field(ge=1, le=512 * 1024 * 1024)
    max_spool_bytes: int = Field(ge=1, le=768 * 1024 * 1024)


class PdfRasterPage(_ProtocolModel):
    ordinal: int = Field(ge=0, le=199)
    filename: str = Field(pattern=r"^page-[0-9]{6}\.png$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(gt=0, le=12 * 1024 * 1024)
    width: int = Field(gt=0, le=10_000)
    height: int = Field(gt=0, le=10_000)
    embedded_rotation: Literal[0, 90, 180, 270]
    page_bbox: tuple[float, float, float, float]


class PdfRendererProvenance(_ProtocolModel):
    pypdfium2: str
    pdfium: str
    pdfium_build: int
    pdfium_flags: tuple[str, ...]
    pillow: str
    native_library: str


class PdfRasterManifest(_ProtocolModel):
    protocol: Literal["mangasensei-pdf-raster-manifest-v1"] = (
        "mangasensei-pdf-raster-manifest-v1"
    )
    import_id: UUID
    fencing_token: int = Field(ge=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raster_contract: Literal["pdfium-raster-v1"] = PDF_RASTER_CONTRACT_VERSION
    page_count: int = Field(ge=1, le=200)
    aggregate_pixels: int = Field(ge=1, le=1_000_000_000)
    aggregate_raster_bytes: int = Field(ge=1, le=512 * 1024 * 1024)
    pages: tuple[PdfRasterPage, ...]
    renderer: PdfRendererProvenance


class PdfRenderFailure(_ProtocolModel):
    protocol: Literal["mangasensei-pdf-render-failure-v1"] = (
        "mangasensei-pdf-render-failure-v1"
    )
    import_id: UUID
    fencing_token: int = Field(ge=1)
    error_code: PdfImportErrorCode


class PdfRendererHeartbeat(_ProtocolModel):
    protocol: Literal["mangasensei-pdf-renderer-heartbeat-v1"] = (
        "mangasensei-pdf-renderer-heartbeat-v1"
    )
    instance_id: str = Field(min_length=1, max_length=128)
    monotonic_ns: int = Field(gt=0)
    renderer: PdfRendererProvenance

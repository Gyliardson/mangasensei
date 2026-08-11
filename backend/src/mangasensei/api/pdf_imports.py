"""HTTP surface for truthful asynchronous PDF import state."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import FastAPI, File, Form, Header, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse

from mangasensei.application.pdf_imports import (
    DocumentImportCreateResult,
    DocumentImportView,
    PdfAdmissionError,
    PdfImportQueryService,
    PdfImportService,
)
from mangasensei.domain.languages import DEFAULT_STUDY_LANGUAGE, StudyLanguage


def _success(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "data": None, "error": {"code": code, "message": message}}


def _create_data(result: DocumentImportCreateResult) -> dict[str, Any]:
    return {
        "importId": str(result.import_id),
        "sourceKind": result.source_kind,
        "status": result.status,
        "rasterContract": result.raster_contract,
        "expiresAt": result.expires_at.isoformat(),
        "capabilities": {"readDocumentImport": result.read_token},
    }


def _view_data(view: DocumentImportView) -> dict[str, Any]:
    data: dict[str, Any] = {
        "importId": str(view.import_id),
        "sourceKind": view.source_kind,
        "status": view.status,
        "rasterContract": view.raster_contract,
        "pageCount": view.page_count,
        "errorCode": view.error_code,
        "createdAt": view.created_at.isoformat(),
        "expiresAt": view.expires_at.isoformat(),
        "document": None,
    }
    if view.document_id is not None and view.document_capabilities is not None:
        data["document"] = {
            "documentId": str(view.document_id),
            "capabilities": {
                "readDocument": view.document_capabilities.read_document,
                "readDocumentImage": view.document_capabilities.read_document_image,
                "reprocessDocument": view.document_capabilities.reprocess_document,
                "manageDocument": view.document_capabilities.manage_document,
            },
        }
    return data


def register_pdf_import_routes(
    app: FastAPI,
    *,
    imports: PdfImportService,
    queries: PdfImportQueryService,
) -> None:
    @app.exception_handler(PdfAdmissionError)
    async def pdf_admission_error(_: Request, exc: PdfAdmissionError) -> JSONResponse:
        return JSONResponse(status_code=422, content=_error(exc.code, str(exc)))

    @app.post("/api/v1/document-imports")
    async def upload_pdf_import(
        response: Response,
        pdf: Annotated[UploadFile, File(description="Bounded local PDF source")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        study_language: Annotated[StudyLanguage, Form(alias="studyLanguage")] = (
            DEFAULT_STUDY_LANGUAGE
        ),
    ) -> dict[str, Any]:
        result = await imports.create(
            reader=pdf.read,
            declared_media_type=pdf.content_type,
            original_filename=pdf.filename or "document.pdf",
            idempotency_key=idempotency_key,
            study_language=study_language,
        )
        response.status_code = status.HTTP_202_ACCEPTED if result.created else status.HTTP_200_OK
        return _success(_create_data(result))

    @app.get("/api/v1/document-imports/{import_id}")
    async def get_pdf_import(
        import_id: UUID,
        import_token: Annotated[str, Header(alias="X-Document-Import-Token")],
    ) -> dict[str, Any]:
        view = await queries.get(import_id=import_id, token=import_token)
        return _success(_view_data(view))

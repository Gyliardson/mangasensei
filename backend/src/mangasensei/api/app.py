"""MangaSensei FastAPI application factory."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID

from fastapi import Body, FastAPI, File, Form, Header, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

import mangasensei
from mangasensei.application.authorization import PageAuthorizer, ResourceNotFoundError
from mangasensei.application.document_authorization import DocumentAuthorizer
from mangasensei.application.document_queries import DocumentQueryService
from mangasensei.application.idempotency import InvalidIdempotencyKeyError
from mangasensei.application.page_queries import PageQueryService
from mangasensei.application.reprocessing import (
    AnalysisInProgressError,
    ReprocessIdempotencyConflictError,
    ReprocessService,
)
from mangasensei.application.uploads import IdempotencyConflictError, UploadResult, UploadService
from mangasensei.config import Settings
from mangasensei.domain.languages import DEFAULT_STUDY_LANGUAGE, StudyLanguage
from mangasensei.infrastructure.capabilities import CapabilityService
from mangasensei.infrastructure.database.session import create_database
from mangasensei.infrastructure.rate_limits import PostgreSQLRateLimiter
from mangasensei.storage.images import ImageValidationError, ImageValidator
from mangasensei.storage.local import LocalFilesystemStorage

_EXPECTED_DATABASE_REVISION = "b7d2f4a91c63"
_HTTP_REQUESTS = Counter(
    "http_requests",
    "HTTP requests completed by method and status code.",
    ("method", "status"),
    namespace="mangasensei",
)
_HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration by method.",
    ("method",),
    namespace="mangasensei",
)


class ReprocessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    study_language: StudyLanguage = Field(alias="studyLanguage")


def _success(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "data": None, "error": {"code": code, "message": message}}


def _upload_data(result: UploadResult) -> dict[str, Any]:
    return {
        "pageId": str(result.page_id),
        "jobId": str(result.job_id),
        "contentSha256": result.content_sha256,
        "width": result.width,
        "height": result.height,
        "mediaType": result.media_type,
        "expiresAt": result.expires_at.isoformat(),
        "studyLanguage": result.study_language,
        "capabilities": {
            "readPage": result.capabilities.read_page,
            "readImage": result.capabilities.read_image,
            "reprocessPage": result.capabilities.reprocess_page,
        },
    }


def create_app(settings: Settings) -> FastAPI:
    database_url, capability_peppers = settings.require_runtime_config()
    engine, sessions = create_database(database_url)
    storage = LocalFilesystemStorage(settings.storage_root)
    capability_service = CapabilityService(capability_peppers)
    upload_service = UploadService(
        sessions=sessions,
        storage=storage,
        capability_service=capability_service,
        idempotency_pepper=capability_peppers[0],
    )
    authorizer = PageAuthorizer(sessions, capability_service)
    document_authorizer = DocumentAuthorizer(sessions, capability_service)
    page_queries = PageQueryService(sessions)
    document_queries = DocumentQueryService(sessions)
    reprocess_service = ReprocessService(
        sessions,
        idempotency_pepper=capability_peppers[0],
    )
    rate_limiter = PostgreSQLRateLimiter(
        sessions,
        pepper=capability_peppers[0],
    )
    validator = ImageValidator(
        max_bytes=settings.max_upload_bytes,
        max_pixels=settings.max_image_pixels,
        max_side=settings.max_image_side,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(
        title="MangaSensei API",
        version=mangasensei.__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started_at = time.perf_counter()
        try:
            policy = _rate_limit_policy(request, settings)
            response: Response
            if policy is not None and not await rate_limiter.allow(
                client_key=request.client.host if request.client is not None else "unknown",
                action=policy[0],
                limit=policy[1],
            ):
                response = JSONResponse(
                    status_code=429,
                    content=_error(
                        "rate_limit_exceeded",
                        "Muitas requisições. Tente novamente em instantes.",
                    ),
                    headers={"Retry-After": "60"},
                )
            else:
                response = await call_next(request)
        except Exception:
            _HTTP_REQUESTS.labels(method=request.method, status="500").inc()
            _HTTP_DURATION.labels(method=request.method).observe(time.perf_counter() - started_at)
            raise
        _HTTP_REQUESTS.labels(method=request.method, status=str(response.status_code)).inc()
        _HTTP_DURATION.labels(method=request.method).observe(time.perf_counter() - started_at)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; "
            "script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'"
        )
        if request.url.path.startswith("/api/v1/"):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.exception_handler(ImageValidationError)
    async def image_error(_: Request, exc: ImageValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content=_error("invalid_image", str(exc)))

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_error(_: Request, __: IdempotencyConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=_error("idempotency_conflict", "A chave já foi usada por outra requisição."),
        )

    @app.exception_handler(ReprocessIdempotencyConflictError)
    async def reprocess_idempotency_error(
        _: Request, __: ReprocessIdempotencyConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=_error("idempotency_conflict", "A chave já foi usada por outra requisição."),
        )

    @app.exception_handler(InvalidIdempotencyKeyError)
    async def invalid_idempotency(_: Request, __: InvalidIdempotencyKeyError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error("invalid_idempotency_key", "A chave de idempotência é inválida."),
        )

    @app.exception_handler(AnalysisInProgressError)
    async def analysis_in_progress(_: Request, __: AnalysisInProgressError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=_error("analysis_in_progress", "A página já possui uma análise em andamento."),
        )

    @app.exception_handler(ResourceNotFoundError)
    async def not_found(_: Request, __: ResourceNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content=_error("not_found", "Recurso não encontrado."))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error("invalid_request", "A requisição não atende ao contrato esperado."),
        )

    @app.post("/api/v1/pages")
    async def upload_page(
        response: Response,
        image: Annotated[UploadFile, File(description="Static JPEG, PNG or WebP page")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        study_language: Annotated[StudyLanguage, Form(alias="studyLanguage")] = (
            DEFAULT_STUDY_LANGUAGE
        ),
    ) -> dict[str, Any]:
        content = await _read_limited(image, settings.max_upload_bytes)
        validated = await asyncio.to_thread(
            validator.validate,
            content,
            declared_media_type=image.content_type or "",
        )
        result = await upload_service.create(
            image=validated,
            original_filename=image.filename or "page",
            idempotency_key=idempotency_key,
            study_language=study_language,
        )
        response.status_code = status.HTTP_202_ACCEPTED if result.created else status.HTTP_200_OK
        return _success(_upload_data(result))

    @app.get("/api/v1/pages/{page_id}/image")
    async def download_image(
        page_id: UUID,
        page_token: Annotated[str, Header(alias="X-Page-Token")],
    ) -> Response:
        authorized = await authorizer.authorize_image(page_id=page_id, token=page_token)
        content = await storage.read(authorized.storage_key)
        return Response(
            content=content,
            media_type=authorized.media_type,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/v1/pages/{page_id}/reprocess")
    async def reprocess_page(
        response: Response,
        page_id: UUID,
        page_token: Annotated[str, Header(alias="X-Page-Token")],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        payload: Annotated[ReprocessRequest | None, Body()] = None,
    ) -> dict[str, Any]:
        authorized = await authorizer.authorize_reprocess(page_id=page_id, token=page_token)
        result = await reprocess_service.create(
            page_id=authorized.internal_id,
            idempotency_key=idempotency_key,
            study_language=payload.study_language if payload is not None else None,
        )
        response.status_code = status.HTTP_202_ACCEPTED if result.created else status.HTTP_200_OK
        return _success(
            {
                "jobId": str(result.job_id),
                "status": result.status,
                "studyLanguage": result.study_language,
                "created": result.created,
            }
        )

    @app.get("/api/v1/pages/{page_id}")
    async def get_page(
        page_id: UUID,
        page_token: Annotated[str, Header(alias="X-Page-Token")],
    ) -> dict[str, Any]:
        authorized = await authorizer.authorize_page(page_id=page_id, token=page_token)
        data = await page_queries.get(authorized.internal_id)
        data["pageId"] = str(authorized.public_id)
        data["expiresAt"] = authorized.expires_at.isoformat()
        data["imageUrl"] = f"/api/v1/pages/{authorized.public_id}/image"
        return _success(data)

    @app.get("/api/v1/pages/{page_id}/status")
    async def get_page_status(
        page_id: UUID,
        page_token: Annotated[str, Header(alias="X-Page-Token")],
    ) -> dict[str, Any]:
        authorized = await authorizer.authorize_page(page_id=page_id, token=page_token)
        data = await page_queries.get(authorized.internal_id)
        return _success(
            {
                "status": data["status"],
                "error": data["error"],
                "resultAvailable": data["resultAvailable"],
            }
        )

    @app.get("/api/v1/documents/{document_id}")
    async def get_document(
        document_id: UUID,
        document_token: Annotated[str, Header(alias="X-Document-Token")],
    ) -> dict[str, Any]:
        authorized = await document_authorizer.authorize_document(
            document_id=document_id,
            token=document_token,
        )
        data = await document_queries.get(authorized.internal_id)
        data["documentId"] = str(authorized.public_id)
        data["sourceKind"] = authorized.source_kind
        data["orderRevision"] = authorized.order_revision
        data["expiresAt"] = authorized.expires_at.isoformat()
        return _success(data)

    @app.get("/api/v1/documents/{document_id}/progress")
    async def get_document_progress(
        document_id: UUID,
        document_token: Annotated[str, Header(alias="X-Document-Token")],
    ) -> dict[str, Any]:
        authorized = await document_authorizer.authorize_document(
            document_id=document_id,
            token=document_token,
        )
        return _success(await document_queries.get_progress(authorized.internal_id))

    @app.get("/api/v1/documents/{document_id}/pages/{page_id}")
    async def get_document_page(
        document_id: UUID,
        page_id: UUID,
        document_token: Annotated[str, Header(alias="X-Document-Token")],
    ) -> dict[str, Any]:
        authorized = await document_authorizer.authorize_page(
            document_id=document_id,
            page_id=page_id,
            token=document_token,
        )
        data = await page_queries.get(authorized.internal_id)
        data["pageId"] = str(authorized.public_id)
        data["expiresAt"] = authorized.expires_at.isoformat()
        data["imageUrl"] = f"/api/v1/documents/{document_id}/pages/{page_id}/image"
        return _success(data)

    @app.get("/api/v1/documents/{document_id}/pages/{page_id}/image")
    async def download_document_image(
        document_id: UUID,
        page_id: UUID,
        document_token: Annotated[str, Header(alias="X-Document-Token")],
    ) -> Response:
        authorized = await document_authorizer.authorize_image(
            document_id=document_id,
            page_id=page_id,
            token=document_token,
        )
        content = await storage.read(authorized.storage_key)
        return Response(
            content=content,
            media_type=authorized.media_type,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return _success({"status": "ok", "version": mangasensei.__version__})

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/ready")
    async def ready() -> JSONResponse:
        try:
            async with sessions() as session:
                revision = (
                    await session.execute(text("SELECT version_num FROM public.alembic_version"))
                ).scalar_one()
            if revision != _EXPECTED_DATABASE_REVISION:
                raise RuntimeError("database schema is not current")
            await storage.probe()
        except Exception:
            return JSONResponse(
                status_code=503,
                content=_error("not_ready", "A aplicação ainda não está pronta."),
            )
        return JSONResponse(
            content=_success({"status": "ready", "databaseRevision": _EXPECTED_DATABASE_REVISION})
        )

    if settings.frontend_dist is not None:
        frontend_dist = settings.frontend_dist.resolve()
        if (frontend_dist / "index.html").is_file():
            app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


async def _read_limited(upload: UploadFile, maximum: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(min(1024 * 1024, maximum + 1 - total)):
        total += len(chunk)
        if total > maximum:
            raise ImageValidationError("image exceeds maximum byte size")
        chunks.append(chunk)
    return b"".join(chunks)


def _rate_limit_policy(request: Request, settings: Settings) -> tuple[str, int] | None:
    if not request.url.path.startswith("/api/v1/"):
        return None
    if request.method == "POST" and request.url.path == "/api/v1/pages":
        return "upload", settings.upload_rate_limit_per_minute
    if request.method == "POST" and request.url.path.endswith("/reprocess"):
        return "reprocess", settings.reprocess_rate_limit_per_minute
    return "api", settings.api_rate_limit_per_minute

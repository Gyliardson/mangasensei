"""FastAPI composition root."""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import FastAPI, File, Header, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from mangasensei import __version__
from mangasensei.application.authorization import (
    CapabilityAuthorizer,
    ResourceNotFoundError,
)
from mangasensei.application.page_queries import PageQueryService
from mangasensei.application.reprocessing import AnalysisInProgressError, ReprocessService
from mangasensei.application.uploads import UploadService
from mangasensei.config import Settings
from mangasensei.infrastructure.database.session import create_database
from mangasensei.infrastructure.rate_limits import InMemoryRateLimiter, RateLimitExceededError
from mangasensei.storage.images import ImageValidationError, ImageValidator
from mangasensei.storage.local import LocalFilesystemStorage

_EXPECTED_DATABASE_REVISION = "f41c7a9498fa"
Handler = Callable[[Request], Awaitable[Response]]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    database_url, peppers = settings.require_runtime_config()
    _, sessions = create_database(database_url)
    storage = LocalFilesystemStorage(settings.storage_root)
    validator = ImageValidator(
        max_bytes=settings.max_upload_bytes,
        max_pixels=settings.max_image_pixels,
        max_side=settings.max_image_side,
    )
    upload_service = UploadService(
        sessions,
        storage,
        capability_pepper=peppers[0],
        retention_hours=settings.retention_hours,
    )
    authorizer = CapabilityAuthorizer(sessions, peppers=peppers)
    page_queries = PageQueryService(sessions)
    reprocess_service = ReprocessService(sessions, idempotency_pepper=peppers[0])
    limiter = InMemoryRateLimiter()
    app = FastAPI(
        title="MangaSensei",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def security_and_rate_limit(request: Request, call_next: Handler) -> Response:
        if request.url.path.startswith("/api/v1/"):
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                content_type = request.headers.get("content-type", "")
                if "application/x-www-form-urlencoded" in content_type:
                    return JSONResponse(
                        status_code=415,
                        content=_error("unsupported_media_type", "Tipo de conteúdo não suportado."),
                    )
            policy = _rate_limit_policy(request, settings)
            if policy is not None:
                bucket, limit = policy
                try:
                    await limiter.consume(
                        bucket=f"{bucket}:{_client_key(request)}",
                        limit=limit,
                        window_seconds=60,
                    )
                except RateLimitExceededError:
                    return JSONResponse(
                        status_code=429,
                        content=_error(
                            "rate_limited",
                            "Muitas requisições. Aguarde um momento e tente novamente.",
                        ),
                        headers={"Retry-After": "60"},
                    )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.exception_handler(ResourceNotFoundError)
    async def resource_not_found(_: Request, __: ResourceNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=_error("not_found", "Recurso não encontrado."),
        )

    @app.exception_handler(AnalysisInProgressError)
    async def analysis_in_progress(_: Request, __: AnalysisInProgressError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=_error(
                "analysis_in_progress",
                "Já existe uma análise ativa para esta página.",
            ),
        )

    @app.exception_handler(ImageValidationError)
    async def invalid_image(_: Request, exc: ImageValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content=_error("invalid_image", str(exc)))

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
    ) -> dict[str, Any]:
        authorized = await authorizer.authorize_reprocess(page_id=page_id, token=page_token)
        result = await reprocess_service.create(
            page_id=authorized.internal_id,
            idempotency_key=idempotency_key,
        )
        response.status_code = status.HTTP_202_ACCEPTED if result.created else status.HTTP_200_OK
        return _success(
            {
                "jobId": str(result.job_id),
                "status": result.status,
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

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return _success({"status": "ok", "version": __version__})

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


def _client_key(request: Request) -> str:
    client = request.client
    return client.host if client is not None else "unknown"


def _success(data: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "data": None, "error": {"code": code, "message": message}}


def _upload_data(result: Any) -> dict[str, Any]:
    return {
        "pageId": str(result.page_id),
        "jobId": str(result.job_id),
        "contentSha256": result.content_sha256,
        "width": result.width,
        "height": result.height,
        "mediaType": result.media_type,
        "expiresAt": result.expires_at.isoformat(),
        "capabilities": {
            "readPage": result.capabilities.read_page,
            "readImage": result.capabilities.read_image,
            "reprocessPage": result.capabilities.reprocess_page,
        },
    }

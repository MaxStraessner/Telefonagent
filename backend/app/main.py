import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import router as api_router
from app.calendar.errors import CalendarError
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    version=settings.backend_version,
    docs_url=None if settings.is_production else "/docs",
    openapi_url=None if settings.is_production else "/openapi.json",
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api/v1")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), payment=(), usb=()"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    if request.url.path.startswith("/api/v1/auth"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "request_error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail}, headers=exc.headers)


@app.exception_handler(CalendarError)
async def calendar_exception_handler(
    request: Request, exc: CalendarError
) -> JSONResponse:
    status_by_code = {
        "invalid_conversation_session": 404,
        "calendar_not_found": 404,
        "calendar_connection_not_found": 404,
        "booking_not_found": 404,
        "slot_not_found": 404,
        "provider_not_configured": 503,
        "provider_unavailable": 503,
        "provider_rate_limited": 429,
        "slot_no_longer_available": 409,
        "duplicate_booking": 409,
        "reauthorization_required": 409,
    }
    return JSONResponse(
        status_code=status_by_code.get(exc.code, 400),
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unbehandelter Fehler", extra={"path": request.url.path})
    return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "Ein interner Fehler ist aufgetreten."}})

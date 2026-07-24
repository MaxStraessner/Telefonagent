from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import Tenant, TenantRole
from app.repositories import TenantRepository
from app.services.authentication import (
    AuthenticatedSession,
    AuthenticationService,
    CsrfValidationError,
    SessionInvalidError,
)


@dataclass(frozen=True)
class TenantContext:
    id: UUID
    tenant: Tenant


@dataclass(frozen=True)
class UserContext:
    id: UUID
    role: TenantRole
    username: str = ""
    email: str | None = None
    display_name: str = ""
    is_platform_admin: bool = False


def request_origin(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/")
    referer = request.headers.get("referer")
    if not referer:
        return None
    parsed = urlsplit(referer)
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def validate_browser_request(
    request: Request, settings: Settings, *, require_ajax: bool = False
) -> None:
    origin = request_origin(request)
    if origin is None or origin not in settings.allowed_request_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "request_origin_rejected",
                "message": "Die Anfrage stammt nicht von einer erlaubten Herkunft.",
            },
        )
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site and fetch_site not in {"same-origin", "same-site", "none"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "cross_site_request_rejected",
                "message": "Siteübergreifende Anfragen sind nicht erlaubt.",
            },
        )
    if require_ajax and request.headers.get("x-requested-with") != "Telefonagent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ajax_request_required",
                "message": "Die Anmeldung erfordert eine vertrauenswürdige Browseranfrage.",
            },
        )


def get_authenticated_session(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedSession:
    service = AuthenticationService(db, settings)
    try:
        authenticated = service.authenticate(
            request.cookies.get(settings.session_cookie_name)
        )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            validate_browser_request(request, settings)
            service.validate_csrf(
                authenticated,
                request.cookies.get(settings.csrf_cookie_name),
                request.headers.get("x-csrf-token"),
            )
        return authenticated
    except SessionInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "authentication_required",
                "message": "Bitte melden Sie sich an.",
            },
        ) from exc
    except CsrfValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "csrf_validation_failed",
                "message": "Die Sicherheitsprüfung der Anfrage ist fehlgeschlagen.",
            },
        ) from exc


def get_tenant_context(
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
) -> TenantContext:
    return TenantContext(id=authenticated.tenant.id, tenant=authenticated.tenant)


def get_tenant_repository(
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TenantRepository:
    return TenantRepository(db, context.id)


def get_user_context(
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
) -> UserContext:
    role = (
        TenantRole.employee
        if authenticated.membership.role == TenantRole.member
        else authenticated.membership.role
    )
    return UserContext(
        id=authenticated.user.id,
        username=authenticated.user.username,
        email=authenticated.user.email,
        display_name=authenticated.user.display_name,
        role=role,
        is_platform_admin=authenticated.user.is_platform_admin,
    )


def require_agent_admin(
    user: UserContext = Depends(get_user_context),
) -> UserContext:
    if user.role not in {TenantRole.owner, TenantRole.admin}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "agent_configuration_forbidden",
                "message": "Nur Owner und Admins dürfen die Konfiguration ändern.",
            },
        )
    return user

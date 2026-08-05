from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import PlatformRole, Tenant, TenantMembership, TenantRole, TenantStatus
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
    role: TenantRole | None
    username: str = ""
    email: str | None = None
    display_name: str = ""
    is_platform_admin: bool = False
    platform_role: PlatformRole | None = None


@dataclass(frozen=True)
class AccessContext:
    authenticated: AuthenticatedSession
    user: UserContext
    platform_role: PlatformRole | None
    membership: TenantMembership | None
    active_tenant: Tenant | None
    mode: str
    permissions: frozenset[str]


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
        if authenticated.user.must_change_password and not any(
            request.url.path.endswith(suffix)
            for suffix in (
                "/auth/session",
                "/auth/me",
                "/auth/logout",
                "/auth/change-password",
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "password_change_required",
                    "message": "Bitte ändern Sie zuerst Ihr vorläufiges Passwort.",
                },
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
    if authenticated.tenant is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "company_context_required",
                "message": "Bitte wÃ¤hlen Sie zuerst einen Unternehmenskontext.",
            },
        )
    if authenticated.tenant.status not in {TenantStatus.trial, TenantStatus.active}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "company_inactive",
                "message": "Dieses Unternehmen ist fÃ¼r Fachfunktionen gesperrt.",
            },
        )
    return TenantContext(id=authenticated.tenant.id, tenant=authenticated.tenant)


def get_tenant_repository(
    context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> TenantRepository:
    return TenantRepository(db, context.id)


def get_user_context(
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
) -> UserContext:
    role = authenticated.membership.role if authenticated.membership else None
    return UserContext(
        id=authenticated.user.id,
        username=authenticated.user.username,
        email=authenticated.user.email,
        display_name=authenticated.user.display_name,
        role=role,
        is_platform_admin=authenticated.user.platform_role is not None,
        platform_role=authenticated.user.platform_role,
    )


def get_access_context(
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
) -> AccessContext:
    user = get_user_context(authenticated)
    permissions: set[str] = set()
    platform_role = authenticated.user.platform_role
    if platform_role in {PlatformRole.owner, PlatformRole.admin}:
        permissions.update(
            {
                "platform.read",
                "platform.companies.manage",
                "platform.company_users.manage",
                "platform.audit.read",
                "company.context.select",
            }
        )
    if platform_role == PlatformRole.owner:
        permissions.add("platform.admins.manage")
    if authenticated.membership and authenticated.membership.is_active:
        permissions.update({"company.read", "company.features.use"})
        if authenticated.membership.role == TenantRole.company_admin:
            permissions.update(
                {"company.manage", "company.users.manage", "company.audit.read"}
            )
    if authenticated.user.must_change_password:
        permissions.clear()
    return AccessContext(
        authenticated=authenticated,
        user=user,
        platform_role=platform_role,
        membership=authenticated.membership,
        active_tenant=authenticated.tenant,
        mode="company" if authenticated.tenant else "platform",
        permissions=frozenset(permissions),
    )


def _forbidden(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": code, "message": message},
    )


def require_platform_admin(
    context: AccessContext = Depends(get_access_context),
) -> AccessContext:
    if context.platform_role not in {PlatformRole.owner, PlatformRole.admin}:
        raise _forbidden(
            "platform_admin_required", "Diese Aktion erfordert Plattformrechte."
        )
    return context


def require_platform_owner(
    context: AccessContext = Depends(get_access_context),
) -> AccessContext:
    if context.platform_role != PlatformRole.owner:
        raise _forbidden(
            "platform_owner_required",
            "Diese Aktion ist dem Plattforminhaber vorbehalten.",
        )
    return context


def require_company_admin(
    context: AccessContext = Depends(get_access_context),
) -> AccessContext:
    if context.active_tenant is None or (
        context.membership is None
        or context.membership.role != TenantRole.company_admin
    ) and context.platform_role not in {PlatformRole.owner, PlatformRole.admin}:
        raise _forbidden(
            "company_admin_required",
            "Diese Aktion erfordert Unternehmensadministrationsrechte.",
        )
    return context


def require_permission(permission: str):
    def guard(
        context: AccessContext = Depends(get_access_context),
    ) -> AccessContext:
        if permission not in context.permissions:
            raise _forbidden(
                "permission_denied", "Sie sind zu dieser Aktion nicht berechtigt."
            )
        return context

    return guard


def require_agent_admin(
    user: UserContext = Depends(get_user_context),
) -> UserContext:
    if user.role != TenantRole.company_admin and user.platform_role not in {
        PlatformRole.owner,
        PlatformRole.admin,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "agent_configuration_forbidden",
                "message": "Nur Owner und Admins dürfen die Konfiguration ändern.",
            },
        )
    return user

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_authenticated_session,
    validate_browser_request,
)
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import TenantRole
from app.schemas.auth import (
    AuthMeResponse,
    AuthTenant,
    AuthUser,
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    SessionResponse,
)
from app.services.authentication import (
    AuthenticatedSession,
    AuthenticationService,
    InvalidCredentialsError,
    SessionSecrets,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


def _role_name(role: TenantRole) -> str:
    return "employee" if role == TenantRole.member else role.value


def _session_response(
    authenticated: AuthenticatedSession, response_type: type[SessionResponse]
) -> SessionResponse:
    return response_type(
        user=AuthUser(
            id=authenticated.user.id,
            username=authenticated.user.username,
            email=authenticated.user.email,
            display_name=authenticated.user.display_name,
            role=_role_name(authenticated.membership.role),
            is_platform_admin=authenticated.user.is_platform_admin,
        ),
        tenant=AuthTenant(
            id=authenticated.tenant.id,
            slug=authenticated.tenant.slug,
            name=authenticated.tenant.name,
        ),
        idle_expires_at=authenticated.session.idle_expires_at,
        absolute_expires_at=authenticated.session.absolute_expires_at,
    )


def _set_auth_cookies(
    response: Response,
    settings: Settings,
    authenticated: AuthenticatedSession,
    secrets: SessionSecrets,
) -> None:
    now = datetime.now(timezone.utc)
    expires_at = authenticated.session.absolute_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    max_age = max(0, int((expires_at - now).total_seconds()))
    common = {
        "secure": settings.is_production,
        "samesite": "lax",
        "path": "/",
        "max_age": max_age,
    }
    response.set_cookie(
        settings.session_cookie_name,
        secrets.token,
        httponly=True,
        **common,
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        secrets.csrf_token,
        httponly=False,
        **common,
    )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.is_production,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        settings.csrf_cookie_name,
        path="/",
        secure=settings.is_production,
        httponly=False,
        samesite="lax",
    )


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LoginResponse:
    validate_browser_request(request, settings, require_ajax=True)
    client_ip = request.client.host if request.client else "unknown"
    try:
        authenticated, secrets = AuthenticationService(db, settings).login(
            payload.username, payload.password, client_ip
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "invalid_credentials",
                "message": "Benutzername oder Passwort ist ungültig.",
            },
        ) from exc
    _set_auth_cookies(response, settings, authenticated, secrets)
    return _session_response(authenticated, LoginResponse)  # type: ignore[return-value]


@router.get("/me", response_model=AuthMeResponse)
def me(
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
) -> AuthMeResponse:
    session = _session_response(authenticated, SessionResponse)
    return AuthMeResponse(user=session.user, tenant=session.tenant)


@router.get("/session", response_model=SessionResponse)
def session(
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
) -> SessionResponse:
    return _session_response(authenticated, SessionResponse)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    AuthenticationService(db, settings).logout(authenticated)
    _clear_auth_cookies(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/change-password", response_model=SessionResponse)
def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    try:
        replacement, secrets = AuthenticationService(db, settings).change_password(
            authenticated, payload.current_password, payload.new_password
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "current_password_invalid",
                "message": "Das aktuelle Passwort ist ungültig.",
            },
        ) from exc
    _set_auth_cookies(response, settings, replacement, secrets)
    return _session_response(replacement, SessionResponse)

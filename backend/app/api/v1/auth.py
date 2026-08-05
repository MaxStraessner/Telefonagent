from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_authenticated_session,
    validate_browser_request,
)
from app.core.config import Settings, get_settings
from app.core.security import hash_password, normalize_username
from app.db.session import get_db
from app.models import AppUser, TenantMembership, TenantRole
from app.repositories.auth import AuthRepository
from app.schemas.auth import (
    AuthMeResponse,
    AuthTenant,
    AuthUser,
    ChangePasswordRequest,
    InitialSetupRequest,
    InitialSetupResponse,
    InitialSetupStatusResponse,
    LoginRequest,
    LoginResponse,
    ManagedUser,
    ManagedUserPasswordReset,
    ManagedUserUpdate,
    ManagedUserWrite,
    SessionResponse,
)
from app.services.authentication import (
    AuthenticatedSession,
    AuthenticationService,
    InvalidCredentialsError,
    SessionSecrets,
)
from app.services.initial_setup import (
    InitialSetupInvalidCodeError,
    InitialSetupService,
    InitialSetupUnavailableError,
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


def _managed_user(user: AppUser, membership: TenantMembership) -> ManagedUser:
    return ManagedUser(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        role=_role_name(membership.role),
        is_platform_admin=user.is_platform_admin,
        is_active=user.is_active and membership.is_active,
    )


def _can_manage_role(actor: TenantRole, role: TenantRole) -> bool:
    return actor == TenantRole.owner or (actor == TenantRole.admin and role == TenantRole.employee)


def _require_account_manager(authenticated: AuthenticatedSession) -> TenantRole:
    role = authenticated.membership.role
    if role not in {TenantRole.owner, TenantRole.admin}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "account_management_forbidden", "message": "Nur Owner und Admins dürfen Konten verwalten."},
        )
    return role


def _managed_membership_or_404(db: Session, tenant_id, user_id) -> TenantMembership:
    membership = db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == user_id))
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "user_not_found", "message": "Benutzer nicht gefunden."})
    return membership


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


@router.get("/setup-status", response_model=InitialSetupStatusResponse)
def initial_setup_status(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> InitialSetupStatusResponse:
    validate_browser_request(request, settings, require_ajax=True)
    return InitialSetupStatusResponse(
        available=InitialSetupService(db, settings).available()
    )


@router.post("/initial-setup", response_model=InitialSetupResponse)
def initial_setup(
    payload: InitialSetupRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> InitialSetupResponse:
    validate_browser_request(request, settings, require_ajax=True)
    client_ip = request.client.host if request.client else "unknown"
    try:
        result = InitialSetupService(db, settings).complete(
            setup_code=payload.setup_code,
            company_name=payload.company_name,
            industry=payload.industry,
            timezone_name=payload.timezone,
            username=payload.username,
            display_name=payload.display_name,
            email=payload.email,
            password=payload.password,
            client_ip=client_ip,
        )
    except InitialSetupInvalidCodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "initial_setup_code_invalid",
                "message": "Der Einrichtungscode ist ungültig oder derzeit gesperrt.",
            },
        ) from exc
    except InitialSetupUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "initial_setup_unavailable",
                "message": "Die Ersteinrichtung ist nicht verfügbar.",
            },
        ) from exc
    _set_auth_cookies(response, settings, result.authenticated, result.secrets)
    return _session_response(result.authenticated, InitialSetupResponse)  # type: ignore[return-value]


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


@router.get("/users", response_model=list[ManagedUser])
def managed_users(
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
    db: Session = Depends(get_db),
) -> list[ManagedUser]:
    _require_account_manager(authenticated)
    rows = db.execute(
        select(AppUser, TenantMembership)
        .join(TenantMembership, TenantMembership.user_id == AppUser.id)
        .where(TenantMembership.tenant_id == authenticated.tenant.id)
        .order_by(AppUser.display_name, AppUser.username)
    ).all()
    return [_managed_user(user, membership) for user, membership in rows]


@router.post("/users", response_model=ManagedUser, status_code=status.HTTP_201_CREATED)
def create_managed_user(
    payload: ManagedUserWrite,
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
    db: Session = Depends(get_db),
) -> ManagedUser:
    actor_role = _require_account_manager(authenticated)
    requested_role = TenantRole(payload.role)
    if not _can_manage_role(actor_role, requested_role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "role_assignment_forbidden", "message": "Admins dürfen nur Mitarbeiterkonten anlegen."})
    normalized = normalize_username(payload.username)
    if db.scalar(select(AppUser.id).where(AppUser.normalized_username == normalized)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "username_conflict", "message": "Dieser Benutzername ist bereits vergeben."})
    user = AppUser(username=payload.username.strip(), normalized_username=normalized, password_hash=hash_password(payload.password), email=payload.email, display_name=payload.display_name, is_active=True, is_platform_admin=False, password_changed_at=datetime.now(timezone.utc))
    membership = TenantMembership(tenant_id=authenticated.tenant.id, user=user, role=requested_role, is_active=True)
    db.add_all([user, membership])
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "user_conflict", "message": "Benutzername oder E-Mail-Adresse ist bereits vergeben."}) from exc
    db.refresh(user)
    db.refresh(membership)
    return _managed_user(user, membership)


@router.put("/users/{user_id}", response_model=ManagedUser)
def update_managed_user(
    user_id: UUID,
    payload: ManagedUserUpdate,
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ManagedUser:
    actor_role = _require_account_manager(authenticated)
    membership = _managed_membership_or_404(db, authenticated.tenant.id, user_id)
    if membership.role == TenantRole.owner or not _can_manage_role(actor_role, membership.role) or user_id == authenticated.user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "user_update_forbidden", "message": "Dieses Konto darf nicht von Ihnen geändert werden."})
    requested_role = TenantRole(payload.role)
    if not _can_manage_role(actor_role, requested_role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "role_assignment_forbidden", "message": "Admins dürfen nur Mitarbeiterkonten verwalten."})
    user = db.get(AppUser, user_id)
    assert user is not None
    user.display_name, user.email, user.is_active = payload.display_name, payload.email, payload.is_active
    membership.role, membership.is_active = requested_role, payload.is_active
    if not payload.is_active:
        AuthRepository(db).revoke_user_sessions(user.id, "user_deactivated_by_manager")
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "email_conflict", "message": "Diese E-Mail-Adresse ist bereits vergeben."}) from exc
    return _managed_user(user, membership)


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_managed_user_password(
    user_id: UUID,
    payload: ManagedUserPasswordReset,
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    actor_role = _require_account_manager(authenticated)
    membership = _managed_membership_or_404(db, authenticated.tenant.id, user_id)
    if membership.role == TenantRole.owner or not _can_manage_role(actor_role, membership.role) or user_id == authenticated.user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "password_reset_forbidden", "message": "Dieses Passwort darf nicht von Ihnen zurückgesetzt werden."})
    user = db.get(AppUser, user_id)
    assert user is not None
    user.password_hash = hash_password(payload.password)
    user.password_changed_at = datetime.now(timezone.utc)
    AuthRepository(db).revoke_user_sessions(user.id, "password_reset_by_manager")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

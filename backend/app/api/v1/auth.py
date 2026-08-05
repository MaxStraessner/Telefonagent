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
from app.models import AppUser, PlatformRole, TenantMembership, TenantRole
from app.repositories.auth import AuthRepository
from app.schemas.auth import (
    AuthMembership,
    AuthMeResponse,
    AuthTenant,
    AuthUser,
    ChangePasswordRequest,
    ContextSelectionRequest,
    ForgotPasswordRequest,
    InitialSetupRequest,
    InitialSetupResponse,
    InitialSetupStatusResponse,
    InvitationAcceptRequest,
    InvitationPreviewResponse,
    LoginRequest,
    LoginResponse,
    ManagedUser,
    ManagedUserPasswordReset,
    ManagedUserUpdate,
    ManagedUserWrite,
    ResetPasswordRequest,
    SessionResponse,
)
from app.services.account_lifecycle import (
    AccountLifecycleService,
    AccountTokenInvalidError,
)
from app.services.authentication import (
    AuthenticatedSession,
    AuthenticationService,
    InvalidCredentialsError,
    SessionInvalidError,
    SessionSecrets,
)
from app.services.initial_setup import (
    InitialSetupInvalidCodeError,
    InitialSetupService,
    InitialSetupUnavailableError,
)
from app.services.mail import MailAdapter, build_mail_adapter

router = APIRouter(prefix="/auth", tags=["authentication"])


def get_mail_adapter(
    settings: Settings = Depends(get_settings),
) -> MailAdapter:
    return build_mail_adapter(settings)


def _role_name(role: TenantRole | None) -> str | None:
    return role.value if role else None


def _requested_role(value: str) -> TenantRole:
    return TenantRole.company_admin if value in {"admin", "company_admin"} else TenantRole.company_user


def _permissions(authenticated: AuthenticatedSession) -> list[str]:
    values: set[str] = set()
    if authenticated.user.platform_role in {PlatformRole.owner, PlatformRole.admin}:
        values.update(
            {
                "platform.read",
                "platform.companies.manage",
                "platform.company_users.manage",
                "platform.audit.read",
                "company.context.select",
            }
        )
    if authenticated.user.platform_role == PlatformRole.owner:
        values.add("platform.admins.manage")
    if authenticated.membership and authenticated.membership.is_active:
        values.update({"company.read", "company.features.use"})
        if authenticated.membership.role == TenantRole.company_admin:
            values.update(
                {"company.manage", "company.users.manage", "company.audit.read"}
            )
    if authenticated.user.must_change_password:
        values.clear()
    return sorted(values)


def _session_response(
    authenticated: AuthenticatedSession, response_type: type[SessionResponse]
) -> SessionResponse:
    membership = authenticated.membership
    tenant = authenticated.tenant
    tenant_response = (
        AuthTenant(id=tenant.id, slug=tenant.slug, name=tenant.name)
        if tenant
        else None
    )
    return response_type(
        user=AuthUser(
            id=authenticated.user.id,
            username=authenticated.user.username,
            email=authenticated.user.email,
            display_name=authenticated.user.display_name,
            role=_role_name(membership.role if membership else None),
            platform_role=(
                authenticated.user.platform_role.value
                if authenticated.user.platform_role
                else None
            ),
            is_platform_admin=authenticated.user.platform_role is not None,
            must_change_password=authenticated.user.must_change_password,
        ),
        tenant=tenant_response,
        active_company=tenant_response,
        membership=(
            AuthMembership(
                tenant_id=membership.tenant_id,
                role=membership.role.value,
                is_primary_admin=membership.is_primary_admin,
            )
            if membership
            else None
        ),
        permissions=_permissions(authenticated),
        mode="company" if tenant else "platform",
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
        platform_role=user.platform_role.value if user.platform_role else None,
        is_platform_admin=user.platform_role is not None,
        must_change_password=user.must_change_password,
        is_active=user.is_active and membership.is_active,
    )


def _can_manage_role(actor: TenantRole, role: TenantRole) -> bool:
    return actor == TenantRole.company_admin and role in {
        TenantRole.company_admin,
        TenantRole.company_user,
    }


def _require_account_manager(authenticated: AuthenticatedSession) -> TenantRole:
    role = authenticated.membership.role if authenticated.membership else None
    if authenticated.tenant is None or (
        role != TenantRole.company_admin
        and authenticated.user.platform_role not in {PlatformRole.owner, PlatformRole.admin}
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "account_management_forbidden", "message": "Nur Owner und Admins dürfen Konten verwalten."},
        )
    return role or TenantRole.company_admin


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


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> Response:
    validate_browser_request(request, settings, require_ajax=True)
    AccountLifecycleService(db, settings, mailer).request_password_reset(
        payload.identifier
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def recover_password(
    payload: ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> Response:
    validate_browser_request(request, settings, require_ajax=True)
    try:
        AccountLifecycleService(db, settings, mailer).reset_password(
            payload.token, payload.new_password
        )
    except AccountTokenInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "password_reset_invalid",
                "message": "Der Link ist ungültig oder abgelaufen.",
            },
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/invitations/{token}", response_model=InvitationPreviewResponse
)
def invitation_preview(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> InvitationPreviewResponse:
    validate_browser_request(request, settings, require_ajax=True)
    try:
        preview = AccountLifecycleService(
            db, settings, mailer
        ).invitation_preview(token)
    except AccountTokenInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "invitation_invalid",
                "message": "Die Einladung ist ungültig oder abgelaufen.",
            },
        ) from exc
    return InvitationPreviewResponse(
        email=preview.email,
        display_name=preview.display_name,
        company_name=preview.company_name,
        role=preview.role,
        expires_at=preview.expires_at,
    )


@router.post(
    "/invitations/{token}", status_code=status.HTTP_204_NO_CONTENT
)
def accept_invitation(
    token: str,
    payload: InvitationAcceptRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> Response:
    validate_browser_request(request, settings, require_ajax=True)
    try:
        AccountLifecycleService(db, settings, mailer).accept_invitation(
            token, payload.password
        )
    except AccountTokenInvalidError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invitation_invalid",
                "message": "Die Einladung ist ungültig oder abgelaufen.",
            },
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=AuthMeResponse)
def me(
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
) -> AuthMeResponse:
    session = _session_response(authenticated, SessionResponse)
    return AuthMeResponse(
        user=session.user,
        tenant=session.tenant,
        active_company=session.active_company,
        membership=session.membership,
        permissions=session.permissions,
        mode=session.mode,
    )


@router.get("/session", response_model=SessionResponse)
def session(
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
) -> SessionResponse:
    return _session_response(authenticated, SessionResponse)


@router.post("/context", response_model=SessionResponse)
def select_company_context(
    payload: ContextSelectionRequest,
    response: Response,
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    try:
        replacement, secrets = AuthenticationService(db, settings).switch_context(
            authenticated, payload.company_id
        )
    except SessionInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "company_context_forbidden",
                "message": "Dieser Unternehmenskontext darf nicht ausgewÃ¤hlt werden.",
            },
        ) from exc
    _set_auth_cookies(response, settings, replacement, secrets)
    return _session_response(replacement, SessionResponse)


@router.delete("/context", response_model=SessionResponse)
def clear_company_context(
    response: Response,
    authenticated: AuthenticatedSession = Depends(get_authenticated_session),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    try:
        replacement, secrets = AuthenticationService(db, settings).clear_context(
            authenticated
        )
    except SessionInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "company_context_clear_forbidden",
                "message": "Der Unternehmenskontext kann nicht verlassen werden.",
            },
        ) from exc
    _set_auth_cookies(response, settings, replacement, secrets)
    return _session_response(replacement, SessionResponse)


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
    requested_role = _requested_role(payload.role)
    if not _can_manage_role(actor_role, requested_role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "role_assignment_forbidden", "message": "Admins dürfen nur Mitarbeiterkonten anlegen."})
    if (
        requested_role == TenantRole.company_admin
        and authenticated.user.platform_role is None
        and not (authenticated.membership and authenticated.membership.is_primary_admin)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "primary_admin_required",
                "message": "Nur der primäre Administrator darf weitere Administratoren ernennen.",
            },
        )
    normalized = normalize_username(payload.username)
    if db.scalar(select(AppUser.id).where(AppUser.normalized_username == normalized)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "username_conflict", "message": "Dieser Benutzername ist bereits vergeben."})
    normalized_email = normalize_username(payload.email) if payload.email else None
    user = AppUser(username=payload.username.strip(), normalized_username=normalized, password_hash=hash_password(payload.password), email=payload.email, normalized_email=normalized_email, display_name=payload.display_name, is_active=True, is_platform_admin=False, must_change_password=True, password_changed_at=datetime.now(timezone.utc))
    assert authenticated.tenant is not None
    membership = TenantMembership(tenant_id=authenticated.tenant.id, user=user, role=requested_role, is_active=True, is_primary_admin=False)
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
    if membership.is_primary_admin or not _can_manage_role(actor_role, membership.role) or user_id == authenticated.user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "user_update_forbidden", "message": "Dieses Konto darf nicht von Ihnen geändert werden."})
    requested_role = _requested_role(payload.role)
    if not _can_manage_role(actor_role, requested_role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "role_assignment_forbidden", "message": "Admins dürfen nur Mitarbeiterkonten verwalten."})
    if (
        requested_role != membership.role
        and authenticated.user.platform_role is None
        and not (authenticated.membership and authenticated.membership.is_primary_admin)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "primary_admin_required",
                "message": "Nur der primäre Administrator darf Rollen ändern.",
            },
        )
    user = db.get(AppUser, user_id)
    assert user is not None
    user.display_name, user.email, user.normalized_email, user.is_active = payload.display_name, payload.email, normalize_username(payload.email) if payload.email else None, payload.is_active
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
    if membership.is_primary_admin or not _can_manage_role(actor_role, membership.role) or user_id == authenticated.user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "password_reset_forbidden", "message": "Dieses Passwort darf nicht von Ihnen zurückgesetzt werden."})
    user = db.get(AppUser, user_id)
    assert user is not None
    user.password_hash = hash_password(payload.password)
    user.password_changed_at = datetime.now(timezone.utc)
    user.must_change_password = True
    AuthRepository(db).revoke_user_sessions(user.id, "password_reset_by_manager")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

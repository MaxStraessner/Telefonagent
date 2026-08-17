from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.dependencies import AccessContext, require_platform_admin, require_platform_owner
from app.api.v1.auth import get_mail_adapter
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import (
    AppUser,
    AuditLog,
    Invitation,
    PlatformRole,
    Tenant,
    TenantMembership,
    TenantSettings,
    TenantStatus,
)
from app.schemas.accounts import (
    AuditLogResponse,
    CompanyCreate,
    CompanyDetail,
    CompanyStatusUpdate,
    CompanySummary,
    CompanyUpdate,
    CompanyUserCreate,
    CompanyUserInvite,
    CompanyUserResponse,
    CompanyUserUpdate,
    InvitationResponse,
    PlatformAdminCreate,
    PlatformAdminInvite,
    PlatformAdminResponse,
    PlatformAdminUpdate,
    PlatformDashboardResponse,
    PrimaryAdminTransfer,
)
from app.schemas.telephony import (
    CompanyTelephonyResponse,
    TwilioAssignmentRequest,
    TwilioNumberResponse,
)
from app.services.account_management import (
    AccountConflictError,
    AccountDeliveryError,
    AccountInvariantError,
    AccountManagementService,
    AccountNotFoundError,
    AccountReauthenticationError,
    audit_query,
    invitation_query,
)
from app.services.mail import MailAdapter
from app.services.twilio import (
    TwilioServiceError,
    TwilioTelephonyService,
    require_twilio_provider,
)

router = APIRouter(prefix="/platform", tags=["platform"])


def _service(
    request: Request,
    context: AccessContext,
    db: Session,
    settings: Settings,
    mailer: MailAdapter,
) -> AccountManagementService:
    return AccountManagementService(
        db,
        settings,
        mailer,
        actor=context.authenticated.user,
        request_id=request.headers.get("x-request-id"),
        client_ip=request.client.host if request.client else None,
    )


def _account_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AccountNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "account_not_found", "message": str(exc)})
    if isinstance(exc, AccountReauthenticationError):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "reauthentication_failed", "message": str(exc)})
    if isinstance(exc, AccountInvariantError):
        return HTTPException(status.HTTP_409_CONFLICT, detail={"code": "account_invariant", "message": str(exc)})
    if isinstance(exc, AccountDeliveryError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "mail_delivery_failed", "message": str(exc)})
    return HTTPException(status.HTTP_409_CONFLICT, detail={"code": "account_conflict", "message": str(exc)})


def _company_or_404(db: Session, company_id: UUID) -> Tenant:
    tenant = db.get(Tenant, company_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "company_not_found", "message": "Unternehmen nicht gefunden."})
    return tenant


def _telephony_error(exc: TwilioServiceError) -> HTTPException:
    if exc.code in {"twilio_not_configured", "provider_sync_failed", "provider_number_unavailable", "provider_numbers_unavailable"}:
        response_status = status.HTTP_503_SERVICE_UNAVAILABLE
    elif exc.code in {"telephony_not_assigned", "phone_number_not_found"}:
        response_status = status.HTTP_404_NOT_FOUND
    elif exc.code == "invalid_phone_number":
        response_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    else:
        response_status = status.HTTP_409_CONFLICT
    return HTTPException(
        response_status,
        detail={"code": exc.code, "message": exc.message, **exc.details},
    )


def _membership_or_404(db: Session, company_id: UUID, user_id: UUID) -> tuple[AppUser, TenantMembership]:
    row = db.execute(
        select(AppUser, TenantMembership)
        .join(TenantMembership, TenantMembership.user_id == AppUser.id)
        .where(TenantMembership.tenant_id == company_id, AppUser.id == user_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "company_user_not_found", "message": "Unternehmensbenutzer nicht gefunden."})
    return row


def _company_summary(db: Session, tenant: Tenant) -> CompanySummary:
    active_count = db.scalar(
        select(func.count(TenantMembership.id))
        .join(AppUser, AppUser.id == TenantMembership.user_id)
        .where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.is_active.is_(True),
            AppUser.is_active.is_(True),
        )
    ) or 0
    primary = db.scalar(
        select(TenantMembership.id)
        .join(AppUser, AppUser.id == TenantMembership.user_id)
        .where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.is_primary_admin.is_(True),
            TenantMembership.is_active.is_(True),
            AppUser.is_active.is_(True),
        )
    )
    return CompanySummary(
        id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        legal_name=tenant.legal_name,
        status=tenant.status.value,
        is_demo=tenant.is_demo,
        active_user_count=active_count,
        has_primary_admin=primary is not None,
        onboarding_complete=primary is not None,
        created_at=tenant.created_at,
    )


def company_detail(db: Session, tenant: Tenant) -> CompanyDetail:
    summary = _company_summary(db, tenant)
    tenant_settings = db.scalar(select(TenantSettings).where(TenantSettings.tenant_id == tenant.id))
    return CompanyDetail(
        **summary.model_dump(),
        industry=tenant.industry,
        timezone=tenant.timezone,
        contact_name=tenant.contact_name,
        contact_email=tenant.contact_email,
        contact_phone=tenant.contact_phone,
        default_language=tenant_settings.default_language if tenant_settings else "de",
    )


def company_user_response(user: AppUser, membership: TenantMembership) -> CompanyUserResponse:
    return CompanyUserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=membership.role.value,
        is_active=user.is_active and membership.is_active,
        is_primary_admin=membership.is_primary_admin,
        must_change_password=user.must_change_password,
        last_login_at=user.last_login_at,
    )


def invitation_response(service: AccountManagementService, invitation: Invitation) -> InvitationResponse:
    role = invitation.tenant_role.value if invitation.tenant_role else invitation.platform_role.value
    return InvitationResponse(
        id=invitation.id,
        email=invitation.email,
        username=invitation.username,
        display_name=invitation.display_name,
        role=role,
        expires_at=invitation.expires_at,
        status=service.invitation_status(invitation),
        created_at=invitation.created_at,
    )


def platform_admin_response(user: AppUser) -> PlatformAdminResponse:
    assert user.platform_role is not None
    return PlatformAdminResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        platform_role=user.platform_role.value,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        last_login_at=user.last_login_at,
    )


def audit_response(entry: AuditLog) -> AuditLogResponse:
    return AuditLogResponse.model_validate(entry, from_attributes=True)


@router.get("/dashboard", response_model=PlatformDashboardResponse)
def dashboard(
    _context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> PlatformDashboardResponse:
    counts = dict(db.execute(select(Tenant.status, func.count(Tenant.id)).group_by(Tenant.status)).all())
    active_users = db.scalar(
        select(func.count(TenantMembership.id))
        .join(AppUser, AppUser.id == TenantMembership.user_id)
        .where(TenantMembership.is_active.is_(True), AppUser.is_active.is_(True))
    ) or 0
    pending = db.scalar(
        select(func.count(Invitation.id)).where(
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
    ) or 0
    return PlatformDashboardResponse(
        companies_total=sum(counts.values()),
        companies_trial=counts.get(TenantStatus.trial, 0),
        companies_active=counts.get(TenantStatus.active, 0),
        companies_suspended=counts.get(TenantStatus.suspended, 0),
        companies_archived=counts.get(TenantStatus.archived, 0),
        active_company_users=active_users,
        pending_invitations=pending,
    )


@router.get("/companies", response_model=list[CompanySummary])
def companies(
    search: str | None = Query(default=None, max_length=200),
    company_status: TenantStatus | None = Query(default=None, alias="status"),
    _context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> list[CompanySummary]:
    statement = select(Tenant)
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(or_(Tenant.name.ilike(pattern), Tenant.slug.ilike(pattern), Tenant.legal_name.ilike(pattern)))
    if company_status:
        statement = statement.where(Tenant.status == company_status)
    tenants = db.scalars(statement.order_by(Tenant.name)).all()
    return [_company_summary(db, tenant) for tenant in tenants]


@router.post("/companies", response_model=CompanyDetail, status_code=status.HTTP_201_CREATED)
def create_company(
    payload: CompanyCreate,
    request: Request,
    context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> CompanyDetail:
    try:
        tenant = _service(request, context, db, settings, mailer).create_company(payload)
    except (AccountConflictError, AccountInvariantError, AccountDeliveryError) as exc:
        db.rollback()
        raise _account_error(exc) from exc
    return company_detail(db, tenant)


@router.get("/companies/{company_id}", response_model=CompanyDetail)
def get_company(
    company_id: UUID,
    _context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> CompanyDetail:
    return company_detail(db, _company_or_404(db, company_id))


@router.put("/companies/{company_id}", response_model=CompanyDetail)
def update_company(
    company_id: UUID,
    payload: CompanyUpdate,
    request: Request,
    context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> CompanyDetail:
    tenant = _company_or_404(db, company_id)
    tenant = _service(request, context, db, settings, mailer).update_company(tenant, payload)
    return company_detail(db, tenant)


@router.post("/companies/{company_id}/status", response_model=CompanyDetail)
def set_company_status(
    company_id: UUID,
    payload: CompanyStatusUpdate,
    request: Request,
    context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> CompanyDetail:
    try:
        tenant = _service(request, context, db, settings, mailer).set_company_status(
            _company_or_404(db, company_id), TenantStatus(payload.status)
        )
    except AccountInvariantError as exc:
        raise _account_error(exc) from exc
    return company_detail(db, tenant)


@router.get("/telephony/twilio/numbers", response_model=list[TwilioNumberResponse])
def twilio_numbers(
    _context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[TwilioNumberResponse]:
    try:
        return TwilioTelephonyService(
            db, settings, require_twilio_provider(settings)
        ).list_numbers()
    except TwilioServiceError as exc:
        raise _telephony_error(exc) from exc


@router.get(
    "/companies/{company_id}/telephony",
    response_model=CompanyTelephonyResponse,
)
def company_telephony(
    company_id: UUID,
    _context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CompanyTelephonyResponse:
    return TwilioTelephonyService(db, settings).status(
        _company_or_404(db, company_id)
    )


@router.put(
    "/companies/{company_id}/telephony/twilio",
    response_model=CompanyTelephonyResponse,
)
def assign_company_twilio_number(
    company_id: UUID,
    payload: TwilioAssignmentRequest,
    context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CompanyTelephonyResponse:
    tenant = _company_or_404(db, company_id)
    try:
        response = TwilioTelephonyService(
            db, settings, require_twilio_provider(settings)
        ).assign(tenant, payload.phone_number, transfer=payload.transfer)
    except TwilioServiceError as exc:
        raise _telephony_error(exc) from exc
    db.add(AuditLog(
        actor_user_id=context.authenticated.user.id,
        tenant_id=tenant.id,
        platform_role=context.platform_role.value if context.platform_role else None,
        action=(
            "telephony.twilio.transfer"
            if payload.transfer
            else "telephony.twilio.assign"
        ),
        target_type="tenant_inbound_route",
        target_id=response.phone_number_sid,
        outcome="success",
        metadata_after={"sync_status": response.sync_status, "error_code": response.error_code},
    ))
    db.commit()
    return response


@router.delete(
    "/companies/{company_id}/telephony/twilio",
    response_model=CompanyTelephonyResponse,
)
def remove_company_twilio_number(
    company_id: UUID,
    context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CompanyTelephonyResponse:
    tenant = _company_or_404(db, company_id)
    service = TwilioTelephonyService(db, settings)
    previous = service.status(tenant)
    response = service.remove(tenant)
    db.add(AuditLog(
        actor_user_id=context.authenticated.user.id,
        tenant_id=tenant.id,
        platform_role=context.platform_role.value if context.platform_role else None,
        action="telephony.twilio.remove",
        target_type="tenant_inbound_route",
        target_id=previous.phone_number_sid,
        outcome="success",
        metadata_before={"phone_number": previous.phone_number},
    ))
    db.commit()
    return response


@router.post(
    "/companies/{company_id}/telephony/twilio/sync",
    response_model=CompanyTelephonyResponse,
)
def sync_company_twilio_number(
    company_id: UUID,
    context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CompanyTelephonyResponse:
    tenant = _company_or_404(db, company_id)
    try:
        response = TwilioTelephonyService(
            db, settings, require_twilio_provider(settings)
        ).sync(tenant)
    except TwilioServiceError as exc:
        raise _telephony_error(exc) from exc
    db.add(AuditLog(
        actor_user_id=context.authenticated.user.id,
        tenant_id=tenant.id,
        platform_role=context.platform_role.value if context.platform_role else None,
        action="telephony.twilio.sync",
        target_type="tenant_inbound_route",
        target_id=response.phone_number_sid,
        outcome="success",
        metadata_after={"sync_status": response.sync_status, "error_code": response.error_code},
    ))
    db.commit()
    return response


@router.get("/companies/{company_id}/users", response_model=list[CompanyUserResponse])
def company_users(
    company_id: UUID,
    _context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> list[CompanyUserResponse]:
    _company_or_404(db, company_id)
    rows = db.execute(
        select(AppUser, TenantMembership)
        .join(TenantMembership, TenantMembership.user_id == AppUser.id)
        .where(TenantMembership.tenant_id == company_id)
        .order_by(AppUser.display_name)
    ).all()
    return [company_user_response(*row) for row in rows]


@router.post(
    "/companies/{company_id}/users",
    response_model=CompanyUserResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_company_user(
    company_id: UUID,
    payload: CompanyUserCreate,
    request: Request,
    context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> CompanyUserResponse:
    try:
        user, membership = _service(request, context, db, settings, mailer).create_company_user(
            _company_or_404(db, company_id), payload
        )
    except (AccountConflictError, AccountInvariantError) as exc:
        db.rollback()
        raise _account_error(exc) from exc
    return company_user_response(user, membership)


@router.put("/companies/{company_id}/users/{user_id}", response_model=CompanyUserResponse)
def update_company_user(
    company_id: UUID,
    user_id: UUID,
    payload: CompanyUserUpdate,
    request: Request,
    context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> CompanyUserResponse:
    tenant = _company_or_404(db, company_id)
    user, membership = _membership_or_404(db, company_id, user_id)
    try:
        _service(request, context, db, settings, mailer).update_company_user(
            tenant, user, membership, payload, actor_membership=None
        )
    except (AccountConflictError, AccountInvariantError) as exc:
        raise _account_error(exc) from exc
    return company_user_response(user, membership)


@router.post("/companies/{company_id}/primary-admin", response_model=CompanyUserResponse)
def transfer_primary_admin(
    company_id: UUID,
    payload: PrimaryAdminTransfer,
    request: Request,
    context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> CompanyUserResponse:
    tenant = _company_or_404(db, company_id)
    user, membership = _membership_or_404(db, company_id, payload.user_id)
    try:
        _service(request, context, db, settings, mailer).transfer_primary_admin(
            tenant, membership, actor_membership=None
        )
    except AccountInvariantError as exc:
        raise _account_error(exc) from exc
    return company_user_response(user, membership)


@router.get("/companies/{company_id}/invitations", response_model=list[InvitationResponse])
def company_invitations(
    company_id: UUID,
    request: Request,
    context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> list[InvitationResponse]:
    _company_or_404(db, company_id)
    service = _service(request, context, db, settings, mailer)
    return [invitation_response(service, item) for item in db.scalars(invitation_query(tenant_id=company_id)).all()]


@router.post("/companies/{company_id}/invitations", response_model=InvitationResponse, status_code=status.HTTP_201_CREATED)
def create_company_invitation(
    company_id: UUID,
    payload: CompanyUserInvite,
    request: Request,
    context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> InvitationResponse:
    service = _service(request, context, db, settings, mailer)
    try:
        invitation = service.issue_company_invitation(_company_or_404(db, company_id), payload)
    except (AccountConflictError, AccountDeliveryError) as exc:
        db.rollback()
        raise _account_error(exc) from exc
    return invitation_response(service, invitation)


@router.delete("/companies/{company_id}/invitations/{invitation_id}", response_model=InvitationResponse)
def revoke_company_invitation(
    company_id: UUID,
    invitation_id: UUID,
    request: Request,
    context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> InvitationResponse:
    invitation = db.scalar(select(Invitation).where(Invitation.id == invitation_id, Invitation.tenant_id == company_id))
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "invitation_not_found", "message": "Einladung nicht gefunden."})
    service = _service(request, context, db, settings, mailer)
    try:
        service.revoke_invitation(invitation)
    except AccountInvariantError as exc:
        raise _account_error(exc) from exc
    return invitation_response(service, invitation)


@router.get("/admins", response_model=list[PlatformAdminResponse])
def platform_admins(
    _context: AccessContext = Depends(require_platform_owner),
    db: Session = Depends(get_db),
) -> list[PlatformAdminResponse]:
    users = db.scalars(select(AppUser).where(AppUser.platform_role.in_([PlatformRole.owner, PlatformRole.admin])).order_by(AppUser.display_name)).all()
    return [platform_admin_response(user) for user in users]


@router.post("/admins", response_model=PlatformAdminResponse, status_code=status.HTTP_201_CREATED)
def create_platform_admin(
    payload: PlatformAdminCreate,
    request: Request,
    context: AccessContext = Depends(require_platform_owner),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> PlatformAdminResponse:
    try:
        user = _service(request, context, db, settings, mailer).create_platform_admin(payload)
    except (AccountConflictError, AccountReauthenticationError) as exc:
        db.rollback()
        raise _account_error(exc) from exc
    return platform_admin_response(user)


@router.post("/admins/invitations", response_model=InvitationResponse, status_code=status.HTTP_201_CREATED)
def invite_platform_admin(
    payload: PlatformAdminInvite,
    request: Request,
    context: AccessContext = Depends(require_platform_owner),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> InvitationResponse:
    service = _service(request, context, db, settings, mailer)
    try:
        invitation = service.issue_platform_admin_invitation(payload)
    except (AccountConflictError, AccountDeliveryError, AccountReauthenticationError) as exc:
        db.rollback()
        raise _account_error(exc) from exc
    return invitation_response(service, invitation)


@router.put("/admins/{user_id}", response_model=PlatformAdminResponse)
def update_platform_admin(
    user_id: UUID,
    payload: PlatformAdminUpdate,
    request: Request,
    context: AccessContext = Depends(require_platform_owner),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> PlatformAdminResponse:
    target = db.get(AppUser, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "platform_admin_not_found", "message": "Plattformadministrator nicht gefunden."})
    try:
        target = _service(request, context, db, settings, mailer).update_platform_admin(target, payload)
    except (AccountConflictError, AccountInvariantError, AccountNotFoundError, AccountReauthenticationError) as exc:
        raise _account_error(exc) from exc
    return platform_admin_response(target)


@router.get("/audit", response_model=list[AuditLogResponse])
def platform_audit(
    company_id: UUID | None = None,
    action: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    _context: AccessContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> list[AuditLogResponse]:
    entries = db.scalars(audit_query(tenant_id=company_id, action=action).limit(limit)).all()
    return [audit_response(entry) for entry in entries]

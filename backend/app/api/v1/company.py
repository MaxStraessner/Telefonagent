from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import AccessContext, TenantContext, get_tenant_context, require_company_admin
from app.api.v1.auth import get_mail_adapter
from app.api.v1.platform import (
    _account_error,
    _membership_or_404,
    _service,
    audit_response,
    company_detail,
    company_user_response,
    invitation_response,
)
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import AppUser, Invitation, TenantMembership
from app.schemas.accounts import (
    AuditLogResponse,
    CompanyDetail,
    CompanyOperationalUpdate,
    CompanyUserInvite,
    CompanyUserResponse,
    CompanyUserUpdate,
    InvitationResponse,
    PrimaryAdminTransfer,
)
from app.services.account_management import (
    AccountConflictError,
    AccountDeliveryError,
    AccountInvariantError,
    audit_query,
    invitation_query,
)
from app.services.mail import MailAdapter

router = APIRouter(prefix="/company", tags=["company"])


@router.get("", response_model=CompanyDetail)
def own_company(
    tenant_context: TenantContext = Depends(get_tenant_context),
    _context: AccessContext = Depends(require_company_admin),
    db: Session = Depends(get_db),
) -> CompanyDetail:
    return company_detail(db, tenant_context.tenant)


@router.put("", response_model=CompanyDetail)
def update_own_company(
    payload: CompanyOperationalUpdate,
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    context: AccessContext = Depends(require_company_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> CompanyDetail:
    tenant = _service(request, context, db, settings, mailer).update_company_operational(
        tenant_context.tenant, payload
    )
    return company_detail(db, tenant)


@router.get("/users", response_model=list[CompanyUserResponse])
def own_company_users(
    tenant_context: TenantContext = Depends(get_tenant_context),
    _context: AccessContext = Depends(require_company_admin),
    db: Session = Depends(get_db),
) -> list[CompanyUserResponse]:
    rows = db.execute(
        select(AppUser, TenantMembership)
        .join(TenantMembership, TenantMembership.user_id == AppUser.id)
        .where(TenantMembership.tenant_id == tenant_context.id)
        .order_by(AppUser.display_name)
    ).all()
    return [company_user_response(*row) for row in rows]


@router.put("/users/{user_id}", response_model=CompanyUserResponse)
def update_own_company_user(
    user_id: UUID,
    payload: CompanyUserUpdate,
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    context: AccessContext = Depends(require_company_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> CompanyUserResponse:
    user, membership = _membership_or_404(db, tenant_context.id, user_id)
    try:
        _service(request, context, db, settings, mailer).update_company_user(
            tenant_context.tenant,
            user,
            membership,
            payload,
            actor_membership=context.membership,
        )
    except (AccountConflictError, AccountInvariantError) as exc:
        raise _account_error(exc) from exc
    return company_user_response(user, membership)


@router.post("/primary-admin", response_model=CompanyUserResponse)
def transfer_own_primary_admin(
    payload: PrimaryAdminTransfer,
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    context: AccessContext = Depends(require_company_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> CompanyUserResponse:
    user, membership = _membership_or_404(db, tenant_context.id, payload.user_id)
    try:
        _service(request, context, db, settings, mailer).transfer_primary_admin(
            tenant_context.tenant,
            membership,
            actor_membership=context.membership,
        )
    except AccountInvariantError as exc:
        raise _account_error(exc) from exc
    return company_user_response(user, membership)


@router.get("/invitations", response_model=list[InvitationResponse])
def own_company_invitations(
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    context: AccessContext = Depends(require_company_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> list[InvitationResponse]:
    service = _service(request, context, db, settings, mailer)
    return [
        invitation_response(service, item)
        for item in db.scalars(invitation_query(tenant_id=tenant_context.id)).all()
    ]


@router.post("/invitations", response_model=InvitationResponse, status_code=status.HTTP_201_CREATED)
def create_own_company_invitation(
    payload: CompanyUserInvite,
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    context: AccessContext = Depends(require_company_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> InvitationResponse:
    service = _service(request, context, db, settings, mailer)
    try:
        invitation = service.issue_company_invitation(tenant_context.tenant, payload)
    except (AccountConflictError, AccountDeliveryError) as exc:
        db.rollback()
        raise _account_error(exc) from exc
    return invitation_response(service, invitation)


@router.delete("/invitations/{invitation_id}", response_model=InvitationResponse)
def revoke_own_company_invitation(
    invitation_id: UUID,
    request: Request,
    tenant_context: TenantContext = Depends(get_tenant_context),
    context: AccessContext = Depends(require_company_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mailer: MailAdapter = Depends(get_mail_adapter),
) -> InvitationResponse:
    invitation = db.scalar(
        select(Invitation).where(
            Invitation.id == invitation_id,
            Invitation.tenant_id == tenant_context.id,
        )
    )
    if invitation is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "invitation_not_found", "message": "Einladung nicht gefunden."},
        )
    service = _service(request, context, db, settings, mailer)
    try:
        service.revoke_invitation(invitation)
    except AccountInvariantError as exc:
        raise _account_error(exc) from exc
    return invitation_response(service, invitation)


@router.get("/audit", response_model=list[AuditLogResponse])
def own_company_audit(
    tenant_context: TenantContext = Depends(get_tenant_context),
    _context: AccessContext = Depends(require_company_admin),
    db: Session = Depends(get_db),
) -> list[AuditLogResponse]:
    entries = db.scalars(audit_query(tenant_id=tenant_context.id).limit(200)).all()
    return [audit_response(entry) for entry in entries]

import base64
from email.message import EmailMessage
from urllib.parse import urlencode

import httpx

from app.core.config import Settings
from app.services.mail import MailDeliveryError, OutboundMail

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send"
STATE_PREFIX = "gmail."
TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


def authorization_url(settings: Settings, state: str, challenge: str) -> str:
    params = {
        "client_id": settings.google_calendar_client_id,
        "redirect_uri": settings.google_calendar_redirect_uri,
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        "access_type": "offline",
        "include_granted_scopes": "false",
        "prompt": "consent select_account",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def error_code(response: httpx.Response) -> str | None:
    try:
        error = response.json().get("error")
        if isinstance(error, str):
            return error
        if isinstance(error, dict):
            reasons = error.get("errors", [])
            return next((item["reason"] for item in reasons if "reason" in item), None)
    except (AttributeError, TypeError, ValueError):
        return None
    return None


async def token_request(client: httpx.AsyncClient, settings: Settings, data: dict) -> dict:
    try:
        response = await client.post(TOKEN_URL, data={
            "client_id": settings.google_calendar_client_id,
            "client_secret": settings.google_calendar_client_secret,
            **data,
        })
    except httpx.RequestError:
        raise MailDeliveryError("Google ist nicht erreichbar.", code="gmail_token_unavailable", retryable=True) from None
    if not response.is_success:
        rejected = error_code(response)
        raise MailDeliveryError(
            "Google hat die Autorisierung abgelehnt.",
            code="gmail_reconnect_required" if rejected == "invalid_grant" else "gmail_token_rejected",
            retryable=response.status_code == 429 or response.status_code >= 500,
        )
    try:
        payload = response.json()
        scope = payload.get("scope")
        if scope is not None and set(scope.split()) != {GMAIL_SCOPE}:
            raise MailDeliveryError("Google hat eine andere Berechtigung erteilt.", code="gmail_scope_mismatch")
        if not isinstance(payload["access_token"], str) or not payload["access_token"]:
            raise ValueError
    except (AttributeError, ValueError, KeyError, TypeError):
        raise MailDeliveryError("Ungültige Google-Antwort.", code="gmail_token_invalid", retryable=True) from None
    return payload


async def exchange_code(settings: Settings, code: str, verifier: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        tokens = await token_request(client, settings, {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": settings.google_calendar_redirect_uri,
        })
    refresh = tokens.get("refresh_token")
    if tokens.get("scope") is None or not isinstance(refresh, str) or not refresh:
        raise MailDeliveryError("Google hat keine dauerhafte Sendeberechtigung erteilt.", code="gmail_consent_incomplete")
    return refresh


async def send_gmail(settings: Settings, refresh_token: str, mail: OutboundMail) -> None:
    message = EmailMessage()
    message["To"] = mail.recipient
    message["Subject"] = mail.subject
    message["Message-ID"] = mail.message_id
    message.set_content(mail.text)
    if mail.html:
        message.add_alternative(mail.html, subtype="html")
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    async with httpx.AsyncClient(timeout=10) as client:
        tokens = await token_request(client, settings, {
            "grant_type": "refresh_token", "refresh_token": refresh_token,
        })
        try:
            response = await client.post(
                SEND_URL, json={"raw": raw},
                headers={"Authorization": "Bearer " + tokens["access_token"]},
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise MailDeliveryError("Gmail ist nicht erreichbar.", code="gmail_connection_failed", retryable=True) from None
        except httpx.RequestError:
            raise MailDeliveryError("Gmail-Zustellung unklar.", code="gmail_submission_unknown", uncertain=True) from None
    if not response.is_success:
        retryable = response.status_code == 429 or (
            response.status_code == 403 and error_code(response) in {"rateLimitExceeded", "userRateLimitExceeded"}
        )
        raise MailDeliveryError(
            "Gmail hat den Versand nicht bestätigt.",
            code="gmail_reconnect_required" if response.status_code == 401 else "gmail_send_rejected",
            retryable=retryable,
            uncertain=response.status_code >= 500,
        )

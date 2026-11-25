from decimal import Decimal, InvalidOperation
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.core.config import Settings
from app.models import CallSession
from app.services.gmail_notifications import utc
from app.services.mail import OutboundMail
from app.services.twilio import ACCOUNT_SID_PATTERN, CALL_SID_PATTERN


async def twilio_cost(settings: Settings, call_sid: str | None) -> str | None:
    account = settings.twilio_account_sid
    token = settings.twilio_auth_token
    if not account or not ACCOUNT_SID_PATTERN.fullmatch(account) or not token or not call_sid or not CALL_SID_PATTERN.fullmatch(call_sid):
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"https://api.twilio.com/2010-04-01/Accounts/{account}/Calls/{call_sid}.json",
                auth=(account, token),
            )
        response.raise_for_status()
        payload = response.json()
        currency = payload.get("price_unit") or ""
        if payload.get("price") is None or len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            return None
        cost = abs(Decimal(str(payload["price"])))
        if cost.is_finite():
            return f"{cost:f} {currency.upper()}".replace(".", ",")
    except (httpx.HTTPError, ValueError, InvalidOperation, AttributeError, TypeError):
        return None
    return None


def call_mail(
    call: CallSession, settings: Settings, sections: list[str], timezone_name: str, cost: str | None,
) -> OutboundMail:
    details = call.analysis_details or {}
    try:
        local_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        local_zone = ZoneInfo("UTC")
    started = utc(call.started_at or call.created_at).astimezone(local_zone)
    callback = {True: "Ja", False: "Nein"}.get(details.get("callback_required"), "Keine Angabe")
    priority = {"urgent": "Dringend", "important": "Wichtig", "normal": "Normal"}
    no_analysis = "Keine Auswertung verfügbar"
    sections_data = {
        "caller": ("Anrufende Person", [
            f"Name: {call.caller_name or 'Unbekannt'}",
            f"Telefonnummer: {call.caller_phone or 'Unbekannt'}",
            *([f"Genannte Nummer: {details['contact_phone']}"] if details.get("contact_phone") else []),
        ]),
        "time": ("Gesprächsdaten", [
            started.strftime("%d.%m.%Y, %H:%M Uhr (%Z)"),
            f"Dauer: {call.duration_seconds // 60} Min. {call.duration_seconds % 60:02d} Sek."
            if call.duration_seconds is not None else "Dauer: Nicht verfügbar",
        ]),
        "summary": ("KI-Zusammenfassung", [call.summary or no_analysis]),
        "concern": ("Anliegen und Dringlichkeit", [
            details.get("main_concern") or no_analysis,
            f"Dringlichkeit: {priority.get(call.priority, 'Keine Angabe') if call.analysis_status == 'completed' else 'Keine Angabe'}",
        ]),
        "tasks": ("Rückruf und offene Aufgaben", [f"Rückruf gewünscht: {callback}", *(call.action_items or [
            "Keine offenen Aufgaben erfasst" if call.analysis_status == "completed" else no_analysis,
        ])]),
        "transcript": ("Gesprächstranskript", [
            f"{'Anrufende Person' if turn.get('speaker') == 'customer' else 'Telefonassistent'}"
            f"{' (vorläufig)' if turn.get('state') == 'provisional' else ' (unterbrochen)' if turn.get('state') == 'interrupted' else ''}: {turn['text']}"
            for turn in call.transcript or [] if turn.get("text")
        ] or ["Kein Transkript verfügbar"]),
        "cost": ("Telefoniekosten", [cost] if cost else []),
    }
    text = ["Gesprächsbericht · Telefonagent"]
    blocks = []
    for section in sections:
        heading, lines = sections_data[section]
        if not lines:
            continue
        text.extend(["", heading, *lines])
        paragraphs = "".join(
            f'<p style="margin:6px 0;white-space:pre-wrap">{escape(line)}</p>' for line in lines
        )
        blocks.append(
            f'<h2 style="font-size:17px;color:#16304a;margin:24px 0 8px">{escape(heading)}</h2>{paragraphs}'
        )
    link = f"{settings.frontend_url.rstrip('/')}/anrufe/{call.id}"
    text.extend(["", f"Gespräch in Telefonagent öffnen: {link}"])
    html = (
        '<html lang="de"><body style="margin:0;background:#f2f5f8;color:#172b3c;font:15px/1.6 Arial,sans-serif">'
        '<div style="max-width:640px;margin:24px auto;background:white">'
        '<div style="background:#16304a;color:white;padding:24px"><p style="margin:0">Telefonagent</p>'
        '<h1 style="font-size:26px;margin:0">Gesprächsbericht</h1></div>'
        f'<div style="padding:0 24px 28px">{"".join(blocks)}'
        f'<p style="margin-top:28px"><a style="color:#174f89" href="{escape(link, quote=True)}">'
        'Gespräch in Telefonagent öffnen</a></p></div></div></body></html>'
    )
    return OutboundMail(
        call.notification_recipient, "Gesprächsbericht · Telefonagent", "\n".join(text),
        message_id=f"<call-{call.id}@telefonagent.local>", html=html,
    )

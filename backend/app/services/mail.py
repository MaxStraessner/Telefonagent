import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from smtplib import SMTP, SMTP_SSL, SMTPException, SMTPRecipientsRefused, SMTPResponseException
from typing import Protocol

from app.core.config import Settings


class MailDeliveryError(Exception):
    def __init__(self, message: str, *, code: str = "smtp_failed", retryable: bool = False, uncertain: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.uncertain = uncertain


@dataclass(frozen=True)
class OutboundMail:
    recipient: str
    subject: str
    text: str
    message_id: str | None = None
    html: str | None = None


class MailAdapter(Protocol):
    def send(self, message: OutboundMail) -> None: ...


class DisabledMailAdapter:
    def send(self, message: OutboundMail) -> None:
        raise MailDeliveryError("E-Mail-Zustellung ist nicht konfiguriert.")


class SmtpMailAdapter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, message: OutboundMail) -> None:
        if not self.settings.smtp_host or not self.settings.smtp_from_address:
            raise MailDeliveryError("E-Mail-Zustellung ist nicht konfiguriert.")
        mail = EmailMessage()
        mail["From"] = self.settings.smtp_from_address
        mail["To"] = message.recipient
        mail["Subject"] = message.subject
        if message.message_id:
            mail["Message-ID"] = message.message_id
        mail.set_content(message.text)
        submitted = False
        acknowledged = False
        try:
            context = ssl.create_default_context()
            connection = SMTP_SSL(self.settings.smtp_host, self.settings.smtp_port, timeout=10, context=context) if self.settings.smtp_use_ssl else SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=10)
            with connection as smtp:
                if self.settings.smtp_starttls and not self.settings.smtp_use_ssl:
                    smtp.starttls(context=context)
                if self.settings.smtp_username:
                    smtp.login(
                        self.settings.smtp_username,
                        self.settings.smtp_password or "",
                    )
                submitted = True
                smtp.send_message(mail)
                # QUIT failure cannot undo the acknowledged DATA submission.
                acknowledged = True
                return
        except SMTPRecipientsRefused as exc:
            if acknowledged:
                return
            retryable = bool(exc.recipients) and all(400 <= response[0] < 500 for response in exc.recipients.values())
            raise MailDeliveryError("E-Mail-Zustellung wurde abgelehnt.", code="smtp_rejected", retryable=retryable) from exc
        except SMTPResponseException as exc:
            if acknowledged:
                return
            raise MailDeliveryError("E-Mail-Zustellung wurde abgelehnt.", code="smtp_rejected", retryable=400 <= exc.smtp_code < 500) from exc
        except (OSError, SMTPException) as exc:
            if acknowledged:
                return
            raise MailDeliveryError("E-Mail-Zustellung ist fehlgeschlagen.", code="smtp_uncertain" if submitted else "smtp_connection_failed", retryable=not submitted, uncertain=submitted) from exc


def mail_transport_available(settings: Settings) -> bool:
    return bool(settings.mail_enabled and settings.smtp_host and settings.smtp_from_address and (settings.smtp_starttls or settings.smtp_use_ssl))


def build_mail_adapter(settings: Settings) -> MailAdapter:
    if mail_transport_available(settings):
        return SmtpMailAdapter(settings)
    return DisabledMailAdapter()

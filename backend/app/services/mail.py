from dataclasses import dataclass
from email.message import EmailMessage
from smtplib import SMTP, SMTPException
from typing import Protocol

from app.core.config import Settings


class MailDeliveryError(Exception):
    pass


@dataclass(frozen=True)
class OutboundMail:
    recipient: str
    subject: str
    text: str


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
        mail.set_content(message.text)
        try:
            with SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=10) as smtp:
                if self.settings.smtp_starttls:
                    smtp.starttls()
                if self.settings.smtp_username:
                    smtp.login(
                        self.settings.smtp_username,
                        self.settings.smtp_password or "",
                    )
                smtp.send_message(mail)
        except (OSError, SMTPException) as exc:
            raise MailDeliveryError("E-Mail-Zustellung ist fehlgeschlagen.") from exc


def build_mail_adapter(settings: Settings) -> MailAdapter:
    if (
        settings.mail_enabled
        and settings.smtp_host
        and settings.smtp_from_address
    ):
        return SmtpMailAdapter(settings)
    return DisabledMailAdapter()

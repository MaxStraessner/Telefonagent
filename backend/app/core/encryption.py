import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.calendar.errors import CalendarConfigurationError


class CalendarTokenCipher:
    """Authenticated server-side encryption for OAuth credentials and PKCE state."""

    def __init__(self, key: str | None):
        if not key:
            raise CalendarConfigurationError(
                "provider_not_configured",
                "Der serverseitige Schlüssel zur Kalender-Tokenverschlüsselung fehlt.",
            )
        try:
            raw = base64.urlsafe_b64decode(key.encode("ascii"))
            if len(raw) != 32:
                raise ValueError("invalid key size")
            self._fernet = Fernet(key.encode("ascii"))
            self.signing_key = hashlib.sha256(raw + b"telefonagent-calendar-slots").digest()
        except (ValueError, TypeError) as exc:
            raise CalendarConfigurationError(
                "provider_not_configured",
                "CALENDAR_TOKEN_ENCRYPTION_KEY muss ein gültiger Fernet-Schlüssel sein.",
            ) from exc

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise CalendarConfigurationError(
                "token_decryption_failed",
                "Gespeicherte Kalenderzugangsdaten konnten nicht entschlüsselt werden.",
            ) from exc

import hashlib
import hmac
import secrets
import unicodedata
from dataclasses import dataclass

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

MINIMUM_PASSWORD_LENGTH = 15
MAXIMUM_PASSWORD_LENGTH = 128
UNUSABLE_PASSWORD_HASH = "!unusable!"

password_hash = PasswordHash(
    (
        Argon2Hasher(
            time_cost=2,
            memory_cost=19 * 1024,
            parallelism=1,
        ),
    )
)
_DUMMY_PASSWORD_HASH = password_hash.hash("dummy-password-that-can-never-authenticate")


@dataclass(frozen=True)
class SessionSecrets:
    token: str
    token_hash: str
    csrf_token: str
    csrf_token_hash: str


def normalize_username(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def validate_new_password(value: str) -> str:
    length = len(value)
    if length < MINIMUM_PASSWORD_LENGTH or length > MAXIMUM_PASSWORD_LENGTH:
        raise ValueError(
            f"Das Passwort muss zwischen {MINIMUM_PASSWORD_LENGTH} und "
            f"{MAXIMUM_PASSWORD_LENGTH} Zeichen lang sein."
        )
    return value


def hash_password(value: str) -> str:
    return password_hash.hash(validate_new_password(value))


def verify_password(value: str, encoded_hash: str | None) -> tuple[bool, str | None]:
    candidate_hash = (
        encoded_hash
        if encoded_hash and not encoded_hash.startswith(UNUSABLE_PASSWORD_HASH)
        else _DUMMY_PASSWORD_HASH
    )
    valid, updated_hash = password_hash.verify_and_update(value, candidate_hash)
    if not encoded_hash or encoded_hash.startswith(UNUSABLE_PASSWORD_HASH):
        return False, None
    return valid, updated_hash


def generate_session_secrets() -> SessionSecrets:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    return SessionSecrets(
        token=token,
        token_hash=sha256_token(token),
        csrf_token=csrf_token,
        csrf_token_hash=sha256_token(csrf_token),
    )


def sha256_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pseudonymize(value: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def constant_time_token_matches(raw_value: str, expected_hash: str) -> bool:
    return hmac.compare_digest(sha256_token(raw_value), expected_hash)

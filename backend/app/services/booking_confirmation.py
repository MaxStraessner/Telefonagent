import re
from enum import StrEnum


class BookingConfirmationDecision(StrEnum):
    confirmed = "confirmed"
    rejected = "rejected"
    change_requested = "change_requested"
    unclear = "unclear"


CONFIRMATIONS = {
    "ja",
    "ja bitte",
    "ja das passt",
    "genau",
    "das passt",
    "passt",
    "mach das",
    "machen sie das",
    "bitte eintragen",
    "bitte tragen sie den termin ein",
    "gerne",
    "okay",
    "ok",
    "in ordnung",
    "den nehme ich",
    "den termin nehme ich",
}


def normalize_confirmation(value: str) -> str:
    return " ".join(re.sub(r"[^a-zäöüß0-9\s]", " ", value.strip().casefold()).split())


def classify_booking_confirmation(value: str) -> BookingConfirmationDecision:
    normalized = normalize_confirmation(value)
    if not normalized:
        return BookingConfirmationDecision.unclear
    if re.search(r"\b(aber|ändern|aendern|lieber|stattdessen|verschieben|später|spaeter|früher|frueher|andere[snr]?)\b", normalized):
        return BookingConfirmationDecision.change_requested
    if re.search(r"\b(nein|doch nicht|nicht eintragen|abbrechen|auf keinen fall)\b", normalized):
        return BookingConfirmationDecision.rejected
    if normalized in CONFIRMATIONS:
        return BookingConfirmationDecision.confirmed
    return BookingConfirmationDecision.unclear

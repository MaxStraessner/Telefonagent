import pytest

from app.services.booking_confirmation import BookingConfirmationDecision, classify_booking_confirmation


@pytest.mark.parametrize(
    "utterance",
    [
        "Ja",
        "Ja, bitte!",
        "Genau.",
        "Das passt",
        "Mach das",
        "Bitte eintragen",
        "Gerne",
        "Okay",
        "In Ordnung",
        "Den nehme ich",
    ],
)
def test_natural_confirmation_variants_are_accepted(utterance):
    assert classify_booking_confirmation(utterance) == BookingConfirmationDecision.confirmed


@pytest.mark.parametrize("utterance", ["Nein", "Bitte nicht eintragen", "Doch nicht", "Abbrechen"])
def test_rejections_are_not_accepted(utterance):
    assert classify_booking_confirmation(utterance) == BookingConfirmationDecision.rejected


@pytest.mark.parametrize(
    "utterance",
    ["Ja, aber lieber um zwölf", "Bitte stattdessen morgen", "Ich möchte den Termin ändern"],
)
def test_change_requests_require_updated_summary_and_new_confirmation(utterance):
    assert classify_booking_confirmation(utterance) == BookingConfirmationDecision.change_requested


@pytest.mark.parametrize("utterance", ["Vielleicht", "Wie war die Uhrzeit?", "Hm"])
def test_unclear_answers_are_not_accepted(utterance):
    assert classify_booking_confirmation(utterance) == BookingConfirmationDecision.unclear

# Leistungen, Terminbuchung und vollständige Sprachausgabe

## Technisches Verhalten

- Terminarten verweisen verpflichtend auf genau eine Leistung. Name, Beschreibung und Dauer werden aus der Leistung gelesen.
- Bestehende Terminarten werden durch Migration `0004` tenantbezogen mit einer namensgleichen Leistung verknüpft; fehlt sie, wird sie aus Name, Beschreibung und Dauer der Terminart angelegt.
- Buchungen speichern Leistungsname, Dauer, Puffer, Terminformat, Ort und Kalendername als historischen Schnappschuss.
- Die sichtbare Terminzeit umfasst nur die Leistungsdauer. Die Konfliktprüfung verwendet `blocked_start_at` und `blocked_end_at` einschließlich der Puffer.
- Eine Buchung wird zuerst lokal als `pending` gespeichert. Erst eine bestätigte externe Ereignis-ID setzt sie auf `confirmed` und `sync_status=synced`.
- Providerfehler ergeben `failed`; ein Fehler bei der lokalen Bestätigung nach externer Erstellung wird als `needs_reconciliation` sichtbar protokolliert.
- Wiederholte Aufrufe mit derselben tenantbezogenen Idempotenzkennung liefern dieselbe bestätigte Buchung und erzeugen kein zweites Ereignis.
- Die Terminagenda kombiniert lokale Buchungen und Provider-Ereignisse. Provider, Kalender-ID und externe Ereignis-ID verhindern eine doppelte Anzeige.

## Realtime-Audio

Die konkrete Fehlerursache war der SDK-Listener

```typescript
const audioStopped = () => this.setAssistantSpeaking(false);
```

Der Listener reagierte auf das interne SDK-Ereignis `audio_stopped`, obwohl die hörbare WebRTC-Wiedergabe noch lief. `setAssistantSpeaking(false)` gab dabei das Mikrofon frei und protokollierte früher zusätzlich ein künstliches `output_audio_buffer.stopped`. Der echte Data-Channel-Stopp traf erst mehrere Sekunden später ein.

Die Wiedergabesteuerung verarbeitet deshalb nur noch unveränderte Raw-Ereignisse aus `transport_event`:

- `output_audio_buffer.started`: Status `playing`, Assistent spricht, Mikrofon deaktiviert.
- `response.output_audio.done`: Nur die Audioerzeugung ist abgeschlossen; Wiedergabe und Mikrofon bleiben unverändert.
- `response.done`: Nur Response-Status und Generierungsende werden gespeichert; bei `completed` läuft die Wiedergabe weiter.
- `output_audio_buffer.stopped`: Status `completed`, Assistent spricht nicht mehr, Mikrofon wieder aktiviert.
- `output_audio_buffer.cleared`, `conversation.item.truncated` oder `response.done` mit nachgewiesenem Abbruch: Status `interrupted`.

Jeder aktive Ablauf speichert `responseId`, Response-Status sowie die getrennten Nachweise für Generierungs- und Wiedergabeende. SDK-Ereignisse steuern weder Sprechstatus noch Mikrofon. Interne Ereignisse tragen `rawEventType: null`; echte OpenAI-Ereignisse tragen `eventSource: "openai_data_channel"` und behalten ihren Raw-Typ.

Ein erfolgreicher, durch den automatisierten Sequenztest geprüfter Ereignisausschnitt sieht so aus (Zeitstempel gekürzt):

```json
{"rawEventType":"output_audio_buffer.started","internalEventName":"assistant_playback_started","responseId":"r1","responseStatus":null,"eventSource":"openai_data_channel","assistantSpeakingBefore":false,"assistantSpeakingAfter":true,"microphoneEnabledBefore":true,"microphoneEnabledAfter":false}
{"rawEventType":"response.output_audio.done","internalEventName":"assistant_audio_generation_completed","responseId":"r1","responseStatus":null,"eventSource":"openai_data_channel","assistantSpeakingBefore":true,"assistantSpeakingAfter":true,"microphoneEnabledBefore":false,"microphoneEnabledAfter":false}
{"rawEventType":"response.done","internalEventName":"assistant_response_generation_completed","responseId":"r1","responseStatus":"completed","eventSource":"openai_data_channel","assistantSpeakingBefore":true,"assistantSpeakingAfter":true,"microphoneEnabledBefore":false,"microphoneEnabledAfter":false}
{"rawEventType":"output_audio_buffer.stopped","internalEventName":"assistant_playback_completed","responseId":"r1","responseStatus":"completed","eventSource":"openai_data_channel","assistantSpeakingBefore":true,"assistantSpeakingAfter":false,"microphoneEnabledBefore":false,"microphoneEnabledAfter":true}
```

`interrupt_response` ist in der wirksamen Browserkonfiguration fest deaktiviert. Das Remote-Audioelement bleibt während der gesamten Seite dauerhaft im DOM. Die Frontendtests prüfen zusätzlich zehn aufeinanderfolgende vollständige Antworten, künstliche Stopps, echte Abbrüche und doppelte Listener.

## Lokaler manueller Test

1. Öffne `http://localhost:5173/leistungen`.
2. Lege die Leistung **Damenhaarschnitt** mit **60 Minuten**, einer Beschreibung und Status **Aktiv** an.
3. Öffne `http://localhost:5173/kalender` und den Tab **Terminarten**.
4. Wähle **Damenhaarschnitt** als Leistung. Prüfe, dass Name und Dauer nur angezeigt und nicht erneut eingegeben werden.
5. Setze **Puffer vorher** und **Puffer nachher** jeweils auf **10 Minuten**.
6. Wähle **Vor Ort**, trage den Ort ein, aktiviere die Terminart und speichere sie.
7. Prüfe im Tab **Kalenderauswahl**, dass genau ein beschreibbarer Zielkalender und mindestens ein Verfügbarkeitskalender ausgewählt sind.
8. Öffne `http://localhost:5173/testgespraech`, starte das Testgespräch und erlaube das Mikrofon.
9. Bitte um einen Damenhaarschnitt und nenne einen Termin innerhalb der konfigurierten Geschäftszeiten.
10. Nenne deinen Namen. Bestätige die zusammengefassten Daten erst auf die ausdrückliche Rückfrage mit **Ja**.
11. Achte bei mehreren Antworten darauf, dass jede Antwort vollständig endet und das Mikrofon erst danach wieder freigegeben wird.
12. Öffne `http://localhost:5173/termine`. Prüfe Leistung, Kunde, 60 Minuten, Format, Ort, Puffer und `synced`.
13. Öffne den verbundenen externen Kalender. Prüfe Titel, Kundenname, Leistung, Zeit, Ort und interne Termin-ID.
14. Wiederhole die Bestätigung nicht als neuen Auftrag. Ein wiederholter identischer Tool-Aufruf darf keinen zweiten Termin erzeugen.

Der lokale Stack läuft auf Frontend-Port `5173` und Backend-Port `8001`. Vor einem späteren Upload erneut Tests ausführen; diese Umsetzung führt selbst keinen Push und kein Deployment durch.

# Terminagent-Refactoring

## Ausgangsarchitektur

Die Anwendung verwendet einen browserbasierten `RealtimeAgent` mit `RealtimeSession` und WebRTC. Das Backend erstellt kurzlebige OpenAI-Client-Secrets, kompiliert die wirksame Systemanweisung und stellt mandantenbezogene Kalenderwerkzeuge bereit. Google und Microsoft implementieren denselben Kalenderadapter. Verfügbarkeit, signierte Slots, lokale Buchungspersistenz, externe Ereigniserstellung und Idempotenz liegen bereits im Backend.

Beibehalten werden die Realtime- und WebRTC-Schicht, der echte Providerzugriff, OAuth und Token-Erneuerung, Leistungen, Terminarten, Kalenderauswahl, lokale Buchungen, Terminagenda, der Audio-Buffer-Fix und die vorhandenen Oberflächen.

Refaktoriert werden die bisher modellgetriebene Ablaufsteuerung, die wiederholte vollständige Providerabfrage pro Verfügbarkeitswunsch, die getrennten Tool-Handler und das unvollständige Laufzeit-/Latenzprotokoll.

## Zielverantwortungen

1. **Realtime-Gesprächsschicht:** Audio, Transkription, WebRTC, SDK-Tools und vollständige Wiedergabe.
2. **Gesprächsorchestrator:** serverseitiger Buchungskontext, validierte Zustandswechsel, fehlende Angaben und Fehlerzustände.
3. **Terminlogik:** Leistung, Terminart, Dauer, Puffer, Geschäftszeiten, Snapshot-Prüfung und Alternativen.
4. **Kalenderadapter:** aktuelle Free-Busy-Abfragen, Zielkalender, Provider-Timeouts, Token und Ereigniserstellung.
5. **Persistenz:** Gesprächssitzung, Snapshot, Buchungsstatus, Idempotenz, externe Ereignis-ID und Synchronisationsstatus.

Die Umsetzung ist additiv. Sie ersetzt keine echte Kalenderfunktion durch Beispieldaten oder Simulationen.

## Ablauf und Zustände

`BookingConversation` ist die serverseitige Quelle für Leistung, Terminart, gewünschte und ausgewählte Zeit, Zeitzone, Kundendaten, Bestätigungsversion sowie interne und externe Buchungs-ID. Jeder Übergang wird gegen eine feste Übergangstabelle geprüft. Ein übersprungener Schritt liefert `invalid_booking_state_transition`.

Der Sitzungsstart stößt `/calendar/conversation/bootstrap` ohne Warten auf die Begrüßung an. Der Snapshot umfasst standardmäßig 14 Tage, läuft nach 120 Sekunden ab und enthält aus externen Kalendern ausschließlich belegte Start-/Endintervalle. Liegt eine Anfrage außerhalb des Horizonts oder ist der Snapshot abgelaufen, erfolgt eine gezielte Aktualisierung. Diese Prüfung ist vorläufig. Erst `finalize_appointment_booking` prüft den exakten Slot erneut gegen Anbieter und lokale Buchungen.

Die endgültige Buchung verwendet eine serverseitig aus Mandant, Sitzung, Leistung, Terminart, Startzeit, Kundendaten und Bestätigungsversion abgeleitete Idempotenzkennung. Erfolg darf nur bei lokalem Status `confirmed` und vorhandener externer Ereignis-ID ausgesprochen werden. Danach wird das belegte Intervall sofort in den Sitzungssnapshot übernommen.

## Realtime-Fortsetzung und Audio

Alle fünf Werkzeuge laufen durch `RealtimeToolExecutor`: `list_bookable_services`, `resolve_service`, `check_appointment_availability`, `find_alternative_slots` und `finalize_appointment_booking`. Doppelte Tool-Call-IDs werden im Client zusammengeführt. Die Fortsetzung wird ausschließlich automatisch durch das installierte OpenAI Agents SDK ausgelöst; die Anwendung sendet nach einem Tool-Ergebnis kein zusätzliches `response.create`.

Die Laufzeit kennt `idle`, `generation_running`, `tool_running`, `tool_result_ready`, `continuation_starting`, `continuation_running` und `playback_running`. Im strikten Sprecherwechsel bleibt das Mikrofon während Generierung, Werkzeuglauf, Fortsetzung und Wiedergabe gesperrt. Freigegeben wird es erst nach dem echten Realtime-Ereignis `output_audio_buffer.stopped`. Das frühere Schutzverhalten gegen künstliche oder vorzeitige Audio-Endereignisse bleibt erhalten.

## Betrieb und Datenschutz

Providerabfragen haben ein konfigurierbares Timeout (`CALENDAR_PROVIDER_TIMEOUT_SECONDS`). Snapshot-Horizont und TTL sind über `AVAILABILITY_SNAPSHOT_HORIZON_DAYS` und `AVAILABILITY_SNAPSHOT_TTL_SECONDS` konfigurierbar. Strukturierte Ereignisse enthalten Sitzungs-, Turn-, Tool-Call-, Zustands-, Antwort-, Dauer-, Erfolgs- und Fehlerfelder, aber keine Inhalte fremder Kalendereinträge.

## Verifikation

Automatisiert geprüft werden Zustandsübergänge, ungültige Sprünge, Zeitzonen und Sommerzeit, datensparsame Snapshotintervalle, vollständige Buchung mit echter Provider-Simulation und externe Ereignis-ID, Konflikte, Idempotenz, zwölf Tool-Fortsetzungspfade sowie zwanzig aufeinanderfolgende Audioantworten bis zum echten Buffer-Stopp.

Eine menschliche Hörprüfung der Stimmen `marin` und `cedar`, reale Lastmessungen gegen produktive Google-/Microsoft-Konten und ein echtes Telefonnetzgespräch sind bewusst nicht als automatisiert bestanden markiert. Dafür ist vor einer Produktionseinführung eine manuelle Abnahme mit den jeweiligen Zugangsdaten und Audiogeräten erforderlich.

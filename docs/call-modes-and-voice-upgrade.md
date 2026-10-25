# Betriebsmodi, Gesprächsdokumentation und Voice-Prüfung

Stand: 25. Oktober 2026. Feature Branch `codex/call-modes-documentation-voice`.

## Bestandsaufnahme vor der Änderung

- FastAPI/SQLAlchemy mit Alembic, bestehende mandantenbezogene Konfiguration und Authentifizierung einschließlich aktivem Unternehmenskontext und PostgreSQL-RLS.
- Tatsächlicher Repositorystandard: `gpt-realtime-2.1`, Stimme `marin`. Die effektive Serverkonfiguration kann diese Werte überschreiben; Produktionsvariablen wurden für diesen Auftrag nicht ausgelesen. Twilio Media Streams übertragen G.711 µ-law, mono, 8 kHz über einen serverseitigen WebSocket. Der Browser-Test nutzt weiterhin die vorhandene Realtime-Integration.
- `agent_configurations` enthält bereits Begrüßungen, Stimme, Sprache, Gesprächsstil, VAD, Grenzen und versionierte Änderungen; Wissen, Fähigkeiten und Kalenderkonfiguration bestehen separat.
- `call_sessions` enthält bereits CallSid, Mandant, Telefonnummernroute, Anrufer, Zeitangaben, Transkript als JSON, Zusammenfassung und Analysezustände. Zuvor wurde das Telefontranskript erst beim Abschluss gespeichert; die Analyse wurde im WebSocket-Handler erwartet.
- `CallsPage`, `CallDetailPage`, `AgentSettingsPage`, Terminansichten, `tool_executions`, `booking_conversations` und `calendar_bookings` werden weiterverwendet. Keine neue Anruf- oder Auswertungstabelle, keine neue Bibliothek und keine zweite Anwendung.

## Implementierung

### Betriebsmodi und feste Laufzeit

`agent` bleibt der Standard für Bestandsunternehmen. `answering_machine` verwendet eine eigene editierbare Nachrichtenbegrüßung und einen deutschen Prompt zur knappen Nachrichtenaufnahme. In diesem Modus werden keine Werkzeuge angeboten. Der Media-Bridge prüft die eingefrorene Werkzeugliste; Dispatcher, ToolAudit und Gesprächsorchestrierung verweigern zusätzlich Aktionen. Name, Telefonnummer und Rückrufwunsch werden nur bei Bedarf erfragt; Termine und garantierte Rückrufe werden nicht versprochen.

Neue Telefonanrufe speichern Modus, Modell und vollständiges Runtime-Manifest beim Provisionieren. Der Media-Handshake stellt dieses Manifest wieder her und berechnet es nicht aus inzwischen geänderten Einstellungen. Browserdiagnosen behalten ihre bisherige reduzierte Manifestdarstellung. Bereits vor der Migration provisionierte Telefonstreams besitzen möglicherweise keinen vollständigen Schnappschuss; beim Deployment müssen laufende Gespräche deshalb beendet oder kontrolliert auslaufen, bevor der Backendprozess ersetzt wird.

### Dokumentation

Die vorhandene JSON-Transkriptspalte wird fortlaufend in kurzen Transaktionen geschrieben. Segmente besitzen stabile Provider-Itemkennungen, Sprecher, Eventkennung, Empfangszeit, optional bekannte Audiozeitpunkte und Vorgänger sowie `provisional`, `confirmed` oder `interrupted`. Bestätigter Text ersetzt Vorläufiges; verspätete Deltas überschreiben keine Bestätigung. Doppelte Eventkennungen werden im Transport abgefangen, gleiche Segmentinhalte erhöhen die Revision nicht erneut. Bekannte Vorgänger werden vor ihren Nachfolgern einsortiert. Unbekannte Reihenfolgen werden nicht als genaue Zeitmessung ausgegeben.

Nach einem Twilio-Stop wird bis zu 750 ms auf abschließende Events gewartet. Auch ASGI-Abbruchpfade schließen den vorhandenen Anrufdatensatz ab. Ein Prozessverlust lässt bereits bestätigte Daten in der Datenbank; die Hintergrundverarbeitung kennzeichnet verwaiste Gespräche nach maximaler Sitzungsdauer plus zwei Minuten. Provisorische Segmente bleiben sichtbar und werden nicht als bestätigte Inhalte analysiert. Abgebrochene Assistentenausgaben werden als möglicherweise nicht vollständig gehört markiert. Ein fehlendes oder nur teilweise bestätigtes Transkript erhält keine Vollständigkeitsbehauptung.

Die Historie zeigt auch laufende und fehlgeschlagene zugeordnete Anrufe. Nicht authentische beziehungsweise keinem Unternehmen zuordenbare Twilio-Anfragen werden weiterhin verworfen und erhalten keinen erfundenen Mandanten.
Auch Fehler beim Aufbau der Laufzeit werden als fehlgeschlagene zugeordnete Anrufe erfasst. Bei erfolgreicher Einrichtung werden Anruf und vollständiges Manifest zusammen gespeichert; doppelte Webhooks verwenden den bestehenden Schnappschuss.

### Nachanalyse

Ein Lebenszyklus-Task im bestehenden Backend verarbeitet persistente Zustände in `call_sessions`; der Telefon-WebSocket wartet nicht auf OpenAI. Der Task öffnet pro Unternehmen einen eigenen RLS-gebundenen Datenbankkontext. Es gibt keine zusätzliche Queue-Infrastruktur.

Unter PostgreSQL ermittelt die eng begrenzte Funktion `call_processing_tenant_ids()` zunächst nur Telefonie-Mandantenkennungen ohne Benutzerkontext. Der Task bindet danach jeden Inhaltszugriff ausdrücklich an das jeweilige Unternehmen. Die Funktion wird durch Migration 0017 eingerichtet. Verarbeitung erhält eine eigene zweiminütige Lease-Frist; auch Wiederaufnahmen nach Prozessverlust enden nach drei beanspruchten Versuchen.

Das bestehende günstige Textmodell `gpt-5.4-nano` bleibt konfigurierbar. Strict JSON Schema und Pydantic validieren Zusammenfassung, Hauptanliegen, ausdrücklich genannte Kontaktdaten, Rückrufbedarf und Terminwunsch. Unbekannte Felder bleiben null. Das Modell erhält keine Aktionswerkzeuge und speichert die Responses-Anfrage nicht (`store=false`). Die tatsächlichen Aktionen und Buchungszustände werden separat aus Werkzeug- und Kalenderdatensätzen geladen; ein Terminwunsch ersetzt keine bestätigte Buchung.

Ein atomarer Claim, ein Verarbeitungstoken und die Transkriptrevision schützen vor paralleler Verarbeitung und veralteten Ergebnissen. Neue späte Segmente stellen die Auswertung erneut zurück. Ergebnisse nach Inhaltslöschung werden verworfen. Timeouts, Transportfehler, HTTP 429 und 5xx werden mit begrenztem Backoff automatisch bis zu drei Versuchen wiederholt. Permanente Fehler verbleiben sichtbar; eine manuelle Wiederholung reiht die Verarbeitung neu ein. Nach zwei Minuten abgelaufene Verarbeitung wird wieder aufgenommen. Jede Runtime-Instanz kann den Task ausführen; Claims verhindern doppelte Ergebnisübernahmen.

## Datenbankänderung 0017

Additive Spalten an vorhandenen Tabellen und eine PostgreSQL-Routingfunktion für die Hintergrundverarbeitung:

- `agent_configurations`: `operating_mode` (Bestand: `agent`), `message_greeting`, `call_retention_days` (30, administrativ 1–365 Tage).
- `call_sessions`: `operating_mode`, `voice_model`, `transcript_revision`, `documentation_status`, `analysis_details`, `analysis_next_attempt_at`, `analysis_lease_token`, `content_deleted_at`.

Historische Anrufmodi und Modelle bleiben unbekannt. Vorhandene Transkripte, Termine und Firmen werden nicht konvertiert oder gelöscht. Es entstehen keine neuen Tabellen und keine Änderungen am Kontenschema. Die Migration wird nur über den bestehenden Deploymentprozess angewandt, nicht durch App-Startup.

## GPT-Live: belegte Unterschiede und Testgrenze

Die offiziellen Seiten wurden am 25. Oktober 2026 abgerufen:

| Thema | Realtime | GPT-Live 1 |
| --- | --- | --- |
| Sitzung | `/v1/realtime?model=...`, `session.created`, `session.update` | `/v1/live/sessions` ohne Query, `session.start`, dann `session.started` |
| Twilio-Audio | PCMU 8 kHz | `audio.format={type:audio/pcmu,rate:8000}` für beide Richtungen; kein Codecwechsel im Gespräch |
| Audioevents | `input_audio_buffer.append`, `response.output_audio.delta` | `session.input_audio.append`, `session.output_audio.delta` |
| Sprecherwechsel | VAD, Response- und Truncate-Logik | kontinuierlich vollduplex, paralleles Hören/Sprechen; keine unveränderte Übernahme der Realtime-VAD-/Truncate-Logik |
| Transkript | Itembezogene Deltas und abgeschlossene Transkription | getrennte Input-/Output-Fragmente mit Zeitpunkten; Anzeige, Playback und Aufgabenstatus separat behandeln |
| Werkzeuge | direkte Realtime-Funktionsaufrufe | eigenständige Backenddelegation; vorhandene Berechtigungs-, Versions- und Bestätigungsprüfungen müssen erhalten bleiben |
| Deutsche Stimmen | vorhandene Stimmenauswahl | zusätzliche natürliche Stimmen `nuri`, `juni`, `flitz`; Verfügbarkeit ist kein Qualitätsnachweis |
| Kosten | nutzungsabhängige Text-/Audiotokens | laut offiziellem Changelog 0,05 USD/Minute, sekundengenau; Backendmodell und Werkzeuge zusätzlich |
| Abschluss | Transport- und Responseabschluss | `session.close`, bis `session.closed` weiter empfangen; finale Dauer nur dann bestätigt |

Quellen: [Realtime](https://developers.openai.com/api/docs/guides/realtime), [GPT-Live](https://developers.openai.com/api/docs/guides/live), [Migration](https://developers.openai.com/api/docs/guides/live-migration), [WebSocket und Audioformate](https://developers.openai.com/api/docs/guides/voice-websockets?api=live), [Sitzungen und Stimmen](https://developers.openai.com/api/docs/guides/live-conversations), [Modell](https://developers.openai.com/api/docs/models/gpt-live-1), [Changelog](https://developers.openai.com/api/docs/changelog).

`voice_providers.py` trennt die Protokolle. Produktion wählt weiterhin Realtime. Live ist ausschließlich durch ausdrückliche Auswahl im lokalen Evaluationsprogramm zugänglich und dort auf Nachrichtenaufnahme ohne externe Aktionen begrenzt. Ein versehentlich bei Realtime eingetragener `gpt-live`-Modellname wird zurückgewiesen. Für vollständige Live-Kalenderdelegation fehlt noch die akustische und operationale Abnahme; sie wird nicht als verfügbar angeboten.

`python -m app.voice_evaluation --provider live --voice nuri --synthetic-pcmu <synthetische-datei.ulaw> --connect` startet einen ausdrücklich kostenpflichtigen Test direkt vom Entwicklungsrechner, ohne Telefonnummer, Unternehmensdatenbank oder Produktionsrouting. Für denselben Audioeingang mit `--provider realtime` vergleichen. Ohne `--connect` wird keine Verbindung aufgebaut. Das Programm sendet maximal 60 Sekunden Roh-PCMU mit 20-ms-Takt plus drei Sekunden Stille; es speichert weder Audio noch Gesprächsinhalte. Es gibt Modell, Stimme, Audio-/Transkriptzählung, erste Ausgabe seit Sitzungsstart und Abschlussnutzung aus. Die erste Ausgabezeit ist keine p95-Antwortlatenz auf eine konkrete Frage. Für Hörqualität und echte Unterbrechungen muss ein kontrollierter Duplex-Hörtest zusätzlich durchgeführt werden.

Reproduzierbare Abnahmeszenarien, jeweils mindestens fünf Wiederholungen pro Modell/Stimme mit identischem synthetischem Eingang: deutsche Umlaute und Namen; Telefonnummer mit Korrektur; „Donnerstag, nein Freitag“; Rückrufwunsch; kein Rückrufwunsch; Sprechpause; überlappende Sprache; Unterbrechung während Ausgabe; Abbruch; gesperrter Buchungswunsch. Für Kalenderdelegation zusätzlich korrigierter Slot während laufendem Tool, ausdrückliche Bestätigung, doppelte Events und verlorene Buchungsantwort. Gesprochene Bestätigung, tatsächlichen Datenbankzustand und gehörte Ausgabe getrennt bewerten.

Messgrößen: Aufgabenerfolg, falsche Zusagen/Buchungen (müssen null sein), Transkriptvollständigkeit, Aussprache, mediane/p95-Zeit zur hilfreichen Antwort, Abbrüche, bestätigte Finalisierung und Gesamtkosten. Live-Kosten: bestätigte Sekunden / 60 × 0,05 USD plus Backend; Realtime mit tatsächlich gemeldeten Audiotokens und den zum Testzeitpunkt gültigen Preisen berechnen. Keine aus Stille oder Audiobytes geschätzten Tokenkosten als Messung ausgeben.

In diesem Auftrag wurden keine kostenpflichtigen Audioverbindungen und keine echten Telefonanrufe ausgeführt. Die automatisierten deutschen Tests prüfen Protokollverträge, Fragmente, sichere Sperren und Rückkehr zum bisherigen Provider. Ein Vorteil bei Latenz, Aussprache, Gesprächsqualität oder Gesamtkosten ist deshalb **nicht nachgewiesen**. Realtime bleibt Standard; kein Live-Rollout und damit kein erforderlicher Produktionsrollback.

## Datenschutz und Betrieb

Vor Öffnung des Audiostreams spielt Twilio einen festen Hinweis auf KI, Texttranskription und Auswertung ab. Ein Auflegen während dieses Hinweises sendet noch kein Gesprächsaudio an OpenAI. Der Hinweis ist keine nachgewiesene Einwilligung und ersetzt nicht die firmenspezifische Information über Verantwortliche, Zwecke, Rechtsgrundlage, Empfänger, Speicherdauer und Betroffenenrechte. Wenn Einwilligung die gewählte Rechtsgrundlage ist, muss vor dem Rollout ein rechtlich abgestimmter expliziter Zustimmungsprozess ergänzt beziehungsweise das genehmigte Verfahren verwendet werden. Keine automatische Annahme, dass bloßes Weitertelefonieren genügt.

[DSGVO Art. 5, 6, 13, 17 und 28](https://eur-lex.europa.eu/legal-content/DE/TXT/?uri=CELEX:32016R0679) verlangen unter anderem Zweckbindung, Speicherbegrenzung, Rechtsgrundlage, Information und geeignete Auftragsverarbeitung. [§ 201 StGB](https://www.gesetze-im-internet.de/stgb/__201.html) ist bei Audioaufzeichnungen gesondert zu prüfen. Es erfolgt hier keine abschließende Rechtsfreigabe für einzelne Unternehmen.

Standardmäßig werden keine vollständigen Audiodateien aufgenommen. Neue Telefoninhalte werden automatisch nach der konfigurierten Frist gelöscht. Administratoren können Inhalte abgeschlossener Gespräche mit eigener Bestätigung löschen; das wird ohne Gesprächsinhalte auditiert. Transkript, Zusammenfassung, extrahierte Kontakte, Anliegen und Aufgaben werden entfernt, die CallSid und minimale technische Metadaten bleiben zur Integrität und Fehlerdiagnose erhalten. Unabhängige Termin-/Kundendaten und Backups unterliegen eigenen Zwecken und Löschregeln; sie werden von dieser Funktion nicht gelöscht. Eine vollständige Betroffenenlöschung umfasst gegebenenfalls auch diese Datensätze und Anbieter. Die Aufbewahrung technischer Metadaten muss betrieblich festgelegt werden.

Zugriffe verwenden weiterhin authentifizierte aktive Unternehmenskontexte und RLS. Inhaltslöschung erfordert Unternehmensadministrator oder autorisierten Plattformadministrator; andere Unternehmensnutzer erhalten keine Löschrechte. API-Antworten mit Gesprächsinhalten dürfen nicht öffentlich gecacht werden. Gewöhnliche neue Verarbeitungslogs enthalten nur feste Ereignis-/Fehlercodes; freie Providerfehlermeldungen werden aus der Telefonieprotokollierung entfernt. TLS, eingeschränkte DB-Rollen, Zugriffsschutz und getrennte Backupberechtigungen bleiben bestehende Betriebsanforderungen. Der Auftrag führt keine Server-, Schlüssel- oder Volumeänderung aus.

## Vor einem Produktivdeployment

1. Lokalen Diff und Migration prüfen; vollständige Tests und isolierte PostgreSQL-RLS-Abnahme durchführen. Keine Aussage über Datenbank-RLS allein aus SQLite-Tests ableiten.
2. Firmenspezifische Datenschutzhinweise, Rechtsgrundlage, Widerspruchs-/Zustimmungsverfahren und Löschregeln inklusive Backups abstimmen. Historische Anrufe mit `documentation_status=legacy` werden nicht automatisch gelöscht; für diese Bestandsdaten ist ein gesondert freigegebener Löschprozess erforderlich. Die automatische Frist gilt für neu dokumentierte Anrufe.
3. Auf einem kontrollierten Testanschluss Begrüßung, Nachrichtenaufnahme, Unterbrechungen, Stop-/Statuscallbacks, Netzabbruch und unveränderte Kalenderbuchung abnehmen. Keine produktiven Kalender beschreiben.
4. Push und Draft-PR nur nach ausdrücklicher Autorisierung; vor Veröffentlichung `gh auth status`. Merge separat freigeben.
5. Deployment nur nach ausdrücklicher Freigabe aus sauberem lokalem `main`, exakt `origin/main`, mit `./ops/deploy.ps1` gemäß `docs/deployment.md`. PostgreSQL-Backup und Migration 0017 im bestehenden Ablauf, dieselben Ports und dasselbe Datenvolume. Laufende Telefonate kontrolliert auslaufen lassen.

Eine Live-Umstellung erfordert zusätzlich die oben genannten Messungen und einen eigenen überprüften Integrationsschritt für vollständige Delegation. Der bestehende Realtimepfad bleibt jederzeit auswählbar; es gibt keine automatische Migration auf Live.

## Geänderte Dateien und Verifikation

Die Änderung betrifft die vorhandenen Modelle und Schemas (`entities.py`, `agent_config.py`, `api.py`, `calls.py`), Konfigurations-/Prompt-/Runtime-Dienste, `call_lifecycle.py`, `call_analysis.py`, die Tool-Sperren in Dispatcher/Orchestrierung/Audit sowie die Anruf- und Twilio-API und den FastAPI-Lebenszyklus. Neu sind ausschließlich die additive Migration `0017_call_modes_documentation.py`, `call_processing.py`, `voice_providers.py` und das lokale Evaluationsprogramm `voice_evaluation.py`.

Im Frontend wurden die vorhandenen Seiten `AgentSettingsPage.tsx`, `CallsPage.tsx`, `CallDetailPage.tsx`, API-Client, Typen und bestehende responsive Styles erweitert. Backendtests liegen in `test_call_modes.py` sowie den ergänzten Telefonie-, Historien- und Migrationstests; Frontendtests ergänzen die bestehenden Einstellungs- und Historienprüfungen.

Prüfnachweise und synthetische Screenshots: [Verifikationsbericht](verification/call-modes/README.md). Die visuellen Prüfungen decken Desktop und echte iframe-Ansichtsbreiten von 390 Pixeln für Einstellungen, Historie und Details ab. Dabei wurden Formulargitter, Metadatenumbrüche, Tabellenbreite und mobile Anrufkarten nachgebessert. Die Screenshots enthalten ausschließlich eigens erzeugte synthetische Daten.

Die zusätzliche umfassende technische QA mit Fehlerkorrekturen, tatsächlicher PostgreSQL-Prozesskette, Abhängigkeitsprüfung und offenen Abnahmegrenzen ist im [aktuellen Abnahmebericht](verification/call-modes/acceptance-qa-2026-10-25.md) dokumentiert. Der dort beschriebene vorbestehende ORM-/Migrationsdrift ist gesondert zu behandeln.

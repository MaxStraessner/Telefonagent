# Abnahme der Realtime-Kernstabilisierung vom 24. Juli 2026

## Ergebnis

Der Realtime-Gesprächskern wurde auf dem lokalen Branch
`fix/realtime-conversation-core` stabilisiert. Ausgangscommit und Rückkehrpunkt
ist `6b9cd80560a4cfa1ea7ea826b7687d94397e91b6`.

Es gab keinen Push, kein Deployment, keinen produktiven OAuth-Vorgang, keine
echte Kalenderbuchung und keinen automatisierten OpenAI-Realtime-Aufruf.

Vor Beginn bereits vorhandene, uncommittete Änderungen an Authentifizierung,
Ersteinrichtung und Kontenverwaltung wurden erhalten. Sie sind im
Ausgangsdokument `realtime-core-baseline.md` getrennt aufgeführt.

## Ursache des Fehlers

Der frühere Fehler `realtime_continuation_failed` war kein belastbarer Beweis
für eine fehlgeschlagene Tool-Fortsetzung beim Provider.

Die Anwendung besaß zusätzlich zum Agents-SDK einen eigenen `TurnCoordinator`.
Nach einer Toolausgabe wartete dieser mit einem viersekündigen
Fortsetzungs-Timer auf ein bestimmtes `response.created`-Ereignis, startete
anschließend eine Recovery-Antwort und beendete die Sitzung nach einem weiteren
Timeout. Gleichzeitig erledigt `@openai/agents-realtime` 0.13.5 bereits selbst:

1. `function_call_output` mit der ursprünglichen `call_id` senden,
2. die Folgeantwort über den SDK-internen `ResponseCreateSequencer` anfordern,
3. erst danach `agent_tool_end` an die Anwendung melden.

Damit konkurrierten zwei technische Gesprächssteuerungen. Ereignisreihenfolge,
Transportlatenz und der lokale Timer konnten sich überholen. Die Oberfläche
konnte deshalb einen Fortsetzungsfehler melden oder die Sitzung schließen,
obwohl der Toolaufruf erfolgreich war und der SDK die Folgeantwort bereits
sequenziert hatte.

Eine zweite Doppelung lag in der Sessionkonfiguration: Das Backend spiegelte
Prompt, Tools, Stimme und VAD bereits beim Minten des Client-Secrets; der
Browser-SDK setzte dieselben Werte anschließend erneut. Eine eigene
Applied-Configuration-Projektion versuchte die beiden Repräsentationen wieder
zu vergleichen. Das erzeugte zusätzliche Drift- und Fehlerpfade ohne eine
zweite verlässliche Wahrheitsquelle.

## Entfernte Komplexität

Entfernt wurden:

- `TurnCoordinator` einschließlich Fortsetzungs-, Recovery- und Retry-Timer,
- anwendungsseitige Tool-`response.create`-Aufrufe,
- `realtime_continuation_failed` und die pauschale Recovery-Klassifizierung,
- die Applied-Configuration-Normalisierung,
- die Endpunkte `/applied-configuration` und `/runtime-diff`,
- doppelte Sessionkonfiguration in der Client-Secret-Anfrage,
- Tests, die die entfernte Doppelsteuerung als Sollverhalten festschrieben.

Historische Datenbankspalten und die Migration mit dem damaligen
`sdk_automatic`-Standard bleiben für bestehende Datenbanken erhalten. Der
aktive Runtime-Pfad verwendet diese Altsteuerung nicht mehr.

## Endgültige Zuständigkeiten

| Vorgang | Autoritative Instanz |
| --- | --- |
| Tenant, Prompt, Tools, Stimme, VAD | Backend-Runtime-Manifest |
| Übertragung der Sessionkonfiguration | Agents-SDK im Browser |
| Normale Antwort nach Spracheingabe | Server-VAD mit `create_response=true` |
| Einmalige Begrüßung | SDK-Transport-Sequencer |
| Toolausführung im Browser | `RealtimeToolExecutor` |
| Tenantgebundene Tooloperation | FastAPI-Backend |
| `function_call_output` und Tool-Folgeantwort | Agents-SDK und dessen `ResponseCreateSequencer` |
| Tool-Deduplizierung | Frontend und Backend anhand der Tool-Call-ID |
| Fachliche Buchungsidempotenz | Booking-/Provider-Schicht |
| Audioende | echtes `output_audio_buffer.stopped` |
| Providerfehler | einmalige, redigierte Klassifizierung |

Der Backend-Request zum Client-Secret-Endpunkt enthält nur die kurze
Secret-Laufzeit. Das Secret bleibt kurzlebig; der dauerhafte OpenAI-Key bleibt
im Backend.

## Tool-, Audio- und Fehlerverhalten

Erfolg, leeres Verfügbarkeitsergebnis und kontrollierter Toolfehler werden als
strukturierte Toolausgabe an den SDK zurückgegeben. Die Anwendung löst danach
keine zusätzliche Antwort aus.

Wiederholte Tool-Call-IDs liefern im Frontend dieselbe laufende oder
abgeschlossene Ausführung. Das Backend lehnt eine zweite Ausführung vor dem
Providerzugriff mit `409 duplicate_tool_call` ab. Vorhandene fachliche
Idempotenz schützt Buchungen zusätzlich auch bei unterschiedlichen technischen
Call-IDs.

Bei erlaubter Unterbrechung bleibt das Mikrofon während Generierung und
Wiedergabe offen. Ist Unterbrechung deaktiviert, wird es bis zum tatsächlichen
Wiedergabeende gesperrt. Ein laufendes Kalenderwerkzeug sperrt das Mikrofon
nicht pauschal.

Unvollständige Responses werden mit ihrem tatsächlichen Grund diagnostiziert,
aber nicht automatisch wiederholt. Abgelehntes `response.create`, sonstige
Providerfehler, Transportverlust, fehlende `session.updated`-Bestätigung und
Bootstrap-Widerspruch besitzen getrennte Fehlercodes. Doppelt über Datenkanal
und SDK eintreffende Providerfehler werden nur einmal gemeldet.

Diagnosen enthalten technische IDs und Zustände, aber keine Passwörter,
Secrets, CSRF-Werte, Toolargumente, Transkripte, Audio- oder
Providerfehlermeldungen.

## Nachweis gegen SDK und offizielle Dokumentation

Installiert und geprüft wurden `@openai/agents` und
`@openai/agents-realtime` in Version 0.13.5.

Der neue Vertragstest verwendet die tatsächlich installierten Klassen
`RealtimeSession`, `RealtimeAgent` und `tool`. Er belegt für einen Function
Call:

- genau eine lokale Toolausführung,
- genau eine Toolausgabe mit unveränderter `call_id`,
- `startResponse=true`,
- genau eine durch den SDK sequenzierte Folgeantwort.

Die Zielarchitektur entspricht:

- [Function-Call-Ergebnis bereitstellen](https://developers.openai.com/api/docs/guides/realtime-conversations#provide-the-results-of-a-function-call-to-the-model)
- [Realtime Server VAD](https://developers.openai.com/api/docs/guides/realtime-vad#server-vad)
- [Realtime `response.create`](https://developers.openai.com/api/reference/resources/realtime/client-events#response.create)
- [Client Secret erstellen](https://developers.openai.com/api/reference/resources/realtime/subresources/client_secrets/methods/create)

## Automatisierte Abnahme

| Prüfung | Ergebnis |
| --- | --- |
| Backend-Gesamtsuite ohne externe PostgreSQL-URLs | 156 bestanden, 6 RLS-Tests erwartungsgemäß übersprungen |
| PostgreSQL-17-RLS- und Constraint-Suite | 6 von 6 bestanden |
| Frontend-Gesamtsuite | 85 von 85 bestanden |
| Vollständige deterministische Tool-Happy-Paths | 20 von 20 bestanden |
| Realtime- und SDK-Vertragstests nach Fixture-Korrektur | 33 von 33 bestanden |
| Ruff | bestanden |
| ESLint ohne Warnungen | bestanden |
| TypeScript- und Vite-Produktions-Build | bestanden |
| `git diff --check` | bestanden |

Die Frontendtests decken alle Kalenderwerkzeuge bei Erfolg und kontrolliertem
Fehler, keine verfügbaren Slots, Providerfehler, abgelehntes
`response.create`, doppelten und verzögerten Tool-Call, eine neue Nutzerrunde
während des Tools, Server-VAD, Unterbrechung, Playback-Ende, Stummschaltung und
Cleanup ab.

Die Backendtests decken tenantgebundene Toolausführung, technische und
fachliche Idempotenz, Kalenderintegration, Buchungsbestätigung und Realtime-
Bootstrap ab. Die PostgreSQL-Tests belegen RLS-Lese- und Schreibschutz sowie
zusammengesetzte Fremdschlüssel mit einer eigenen Runtime-Rolle.

Alle automatisierten Provider sind Fakes. Die Tests erzeugen keine echten
Kalendertermine.

## Lokale Docker-Abnahme

Der vorhandene Compose-Stack `telefonagent` wurde ohne Volume-Löschung neu
gebaut. Migration 0012 lief erfolgreich. Danach waren:

- PostgreSQL 17 gesund,
- das Backend auf Port 8001 gesund und mit verbundener Datenbank,
- das Frontend auf Port 5173 erreichbar,
- die Loginseite nach der Sessionprüfung sichtbar,
- die bereits abgeschlossene Ersteinrichtung auch bei direktem Aufruf
  geschlossen.

Backend- und Migrationslogs enthielten beim Start keine Exception oder
Fehlermeldung.

## Bewusst offene manuelle Prüfung

Ein echter WebRTC-Sprachdialog wurde nicht automatisiert gestartet. Er benötigt
Mikrofonfreigabe, Modellzugriff und erzeugt externe OpenAI-Nutzung. Eine echte
Kalenderbuchung würde zusätzlich den verbundenen Provider verändern.

Für die manuelle Abnahme:

1. `http://localhost:5173` öffnen und anmelden.
2. Die Seite für das Testgespräch öffnen und das Mikrofon freigeben.
3. Die einmalige Begrüßung abwarten.
4. Terminart, Datum und Uhrzeit nennen.
5. Prüfen, dass der Agent nach der Verfügbarkeitsprüfung ohne Neustart
   weiterspricht.
6. Eine Alternative auswählen, Kontaktdaten nennen und die Zusammenfassung mit
   „Ja bitte“ bestätigen.
7. Prüfen, dass eine Buchungsbestätigung nur nach erfolgreicher
   Providerbuchung ausgesprochen wird.
8. Während einer Antwort sprechen und die konfigurierte Unterbrechung prüfen.
9. Das Gespräch beenden und kontrollieren, dass der Mikrofonindikator erlischt.

Für diesen Test sollten ein dedizierter Testkalender und ein kontrollierbares
Zeitfenster verwendet werden.

## Verbleibende Risiken

- Akustik, reale WebRTC-Latenz und kontospezifische Providerantworten sind nur
  in einem bewusst gestarteten Live-Test prüfbar.
- Der Produktions-Build meldet einen großen Haupt-Chunk von rund 958 kB. Das
  beeinflusst den Realtime-Steuerungsfehler nicht, sollte aber später durch
  Code-Splitting optimiert werden.
- Historische Auditdokumente beschreiben weiterhin den damaligen Coordinator.
  Sie sind als historischer Stand gekennzeichnet; dieses Dokument ist für den
  aktuellen Realtime-Kern maßgeblich.

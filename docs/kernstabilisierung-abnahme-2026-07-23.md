# Abnahme der Kernstabilisierung vom 23. Juli 2026

## Ergebnis

Die Kernstabilisierung des KI-Telefonagenten ist auf dem lokalen Branch
`feature/realtime-config-drift` umgesetzt. `main` bleibt auf `e803fbc` der
unveränderte Rückkehrpunkt. Es gab keinen Push, kein Deployment und keinen
echten Kalenderschreibzugriff.

Die Umsetzung umfasst:

- ein kanonisches Runtime-Manifest mit Schema-Version, Digests,
  Konfigurationsständen, Prompt, OpenAI-Sessionparametern, VAD,
  Unterbrechungsverhalten, Recovery-Policy und backenddefinierten Toolschemas;
- einen gemeinsamen Session-Bootstrap sowie eine redigierte Soll-/Ist-Prüfung
  der tatsächlich angewendeten Realtime-Konfiguration;
- einen zentralen TurnCoordinator für Response-, Tool-, Recovery-, Mikrofon-,
  Audio- und Playbackzustände;
- höchstens einen idempotenten Fortsetzungsversuch je Turn und einen
  kontrollierten Abschluss statt endloser Stille;
- `Europe/Berlin` als kanonische Tenant-Zeitzone und einen deterministischen
  Resolver für natürliche deutsche Datumsangaben einschließlich
  Buchungshorizont, Jahreswechsel und DST-Sonderfällen;
- eine serverseitig gebundene Buchungszustandsmaschine mit signierten Slot-IDs,
  erneuter Verfügbarkeitsprüfung, Bestätigungsversion und
  Zusammenfassungs-Digest;
- kontextbezogene Klassifikation von Bestätigung, Ablehnung, Änderung und
  unklarer Antwort ohne persistente Speicherung der ursprünglichen Äußerung.

## Getrennte Checkpoints

| Commit | Checkpoint |
| --- | --- |
| `925ea42` | Baseline und Charakterisierung |
| `25b839d` | Runtime-Manifest und Konfigurationsdrift |
| `4638822` | Realtime-, Tool-, Audio- und Mikrofonkoordination |
| `c32fb92` | Deutsche Datumsauflösung und kanonische Zeitzone |
| `67e222a` | Buchungszustand und kontextbezogene Zustimmung |
| `7f33014` | abschließende Ruff-Normalisierung vorhandener Migrationen |

## Automatisierte Abnahme

| Prüfung | Ergebnis |
| --- | --- |
| Backendtests | 139 bestanden |
| Frontendtests | 85 bestanden in 8 Testdateien |
| TypeScript- und Vite-Produktionsbuild | bestanden |
| ESLint mit null erlaubten Warnungen | bestanden |
| Ruff | bestanden |
| `pip check` | keine beschädigten Abhängigkeiten |
| Compose-Konfiguration | gültig |
| Backend- und Frontend-Images | frisch gebaut |
| PostgreSQL-Migration | `0009 (head)` |
| Backend-Healthcheck | `healthy`, Datenbank verbunden |
| Containerlogs | keine Anwendungsfehler beim Start oder bei der Prüfung |

Der Vite-Build meldet weiterhin einen nicht blockierenden Hinweis auf ein
JavaScript-Bundle über 500 kB. Code-Splitting bleibt eine spätere
Frontend-Optimierung und ist kein Teil dieses Stabilisierungsschritts.

## Lokale Laufzeitprüfung

Die Container wurden aus dem aktuellen Feature-Branch neu gebaut und neu
erstellt. Das Frontend läuft auf `http://localhost:5173`, das Backend wegen der
lokalen Portkonfiguration auf `http://localhost:8001`.

Read-only geprüft wurden:

- Dashboard und Plattformstatus;
- Datenbank-, Realtime- und Kalender-Konfigurationsstatus;
- KI-Konfigurationsoberfläche mit gespeicherter Version 14;
- Kalender-Verfügbarkeitsoberfläche mit nicht editierbarer, vom Tenant
  abgeleiteter Zeitzone `Europe/Berlin`;
- Testgesprächsseite und kontrollierter Session-Cleanup.

Ein OpenAI-API-Key ist konfiguriert. Der automatisierte Browser blieb jedoch am
nativen Mikrofon-Berechtigungsdialog stehen. Die begonnene lokale Testsitzung
wurde deshalb kontrolliert beendet. Ein echter WebRTC-Sprachdialog,
menschlicher Hörtest für neutrales Standarddeutsch und die manuelle
Transkriptprüfung bleiben als letzter lokaler Bedienungstest offen. Dafür ist
die Testgesprächsseite geöffnet. Die automatisierten Transporttests verwenden
einen deterministischen Fake-OpenAI-Transport; der vollständige Terminablauf
ist mit dem Fake-Kalenderprovider abgedeckt.

## Manuelle Abschlussprüfung

1. Auf `http://localhost:5173/testgespraech` ein neues Testgespräch starten und
   den Mikrofonzugriff erlauben.
2. Nach der Begrüßung prüfen, dass die Stimme, Sprache und der konfigurierte
   Gesprächsstil hörbar angewendet werden.
3. Beispielsweise sagen: „Ich möchte einen Haarschnitt nächsten Freitag am
   Nachmittag.“
4. Nach der Verfügbarkeitsprüfung prüfen, dass der Agent ohne Stille genau
   einmal weiterspricht und das Jahr nur nennt, wenn es zur Eindeutigkeit
   erforderlich ist.
5. Vor einer endgültigen Buchungsbestätigung abbrechen, solange kein echter
   Kalenderschreibzugriff gewünscht ist.

## Ausdrücklich zurückgestellt

- neue Authentifizierung und Login-Oberfläche;
- CSRF, Rollen und Tenant-Auswahl;
- PostgreSQL-Ausschlussconstraint;
- Reconciliation-Outbox, Worker und administrative Retry-Funktionen;
- umfassende Abhängigkeitsaktualisierungen;
- Deployment und echte Kalenderbuchungen.


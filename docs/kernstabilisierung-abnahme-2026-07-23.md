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
| `11c6b3b` | fail-closed Toolprojektionen, Digeststufen und Connection-Generationen |

## Automatisierte Abnahme

| Prüfung | Ergebnis |
| --- | --- |
| Backendtests | 143 bestanden |
| Frontendtests | 90 bestanden in 9 Testdateien |
| TypeScript- und Vite-Produktionsbuild | bestanden |
| ESLint mit null erlaubten Warnungen | bestanden |
| Ruff | bestanden |
| `pip check` | keine beschädigten Abhängigkeiten |
| Compose-Konfiguration | gültig |
| Backend- und Frontend-Images | frisch gebaut |
| PostgreSQL-Migration | `0009 (head)` |
| Backend-Healthcheck | `healthy`, Datenbank verbunden |
| Containerlogs | keine Anwendungsfehler beim Start oder bei der Prüfung |
| `npm ci` und Agents-Paketbaum | reproduzierbar; alle Agents-Pakete `0.13.5`, `@openai/agents-core` dedupliziert |
| Read-only Smoke-Tool | `list_bookable_services` erfolgreich, 2 Leistungen gelesen, kein Finalize-Aufruf |

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

Bei der ersten manuellen Realtime-Prüfung wurde eine echte SDK-Kompatibilitäts-
abweichung im Tool-Digest sichtbar: `@openai/agents` 0.13.5 transformiert die
Parameter im Strict-Modus vor dem Wire-Versand. Der vollständige Root Cause,
die drei Digeststufen und die fail-closed Regeln sind im Abschnitt
„Nachtraeglicher Fix“ dokumentiert. `strict: true` bleibt Bestandteil des
kanonischen Vertrags und wird nur lokal validiert; die übertragbare Wire-
Projektion enthält es nicht. Unbekannte Transformationen werden nicht
normalisiert.

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

## Nachtraeglicher Fix: fail-closed Toolprojektionen und Connection-Generationen

Die Ursache des urspruenglichen `realtime_configuration_mismatch` war die
SDK-Projektion von `@openai/agents` 0.13.5: optionale Schemafelder werden im
Strict-Modus als `anyOf: [urspruengliches Schema, {type: null}]` modelliert,
Objektschemata erhalten eine vollstaendige `required`-Liste und
`additionalProperties: false`; `default: null` wird entfernt. Das Backend hatte
zuvor den unveraenderten Vertrag gehasht.

Der Fix fuehrt drei getrennte Projektionen:

- `canonical_tools_digest`: vollstaendiger lokaler Vertrag mit `strict: true`;
  jedes Tool ohne `strict` oder mit `strict: false` bricht den Start ab.
- `outbound_wire_tools_digest`: exakt `type`, `name`, `description` und
  `parameters`; bekannte Strict-Schema-Transformationen werden angewendet.
  `strict`, `deferLoading`, Guardrails, Timeoutwerte, `providerData` und weitere
  explizit bekannte SDK-interne Felder werden nicht uebertragen.
- `acknowledged_tools_digest`: dieselben vier Wire-Felder aus `session.updated`.
  `strict` wird dort nicht erwartet. Unbekannte Providerfelder oder unbekannte
  Schemaaenderungen bleiben fail closed.

Verglichen wird nur `outbound_wire_tools_digest` gegen
`acknowledged_tools_digest`; der kanonische Digest wird zusaetzlich lokal auf
`strict: true` geprueft. Die Werkzeugreihenfolge wird nicht eigenmaechtig
sortiert, weil dafuer keine belegte SDK-Transformation vorliegt.

Jeder Verbindungsaufbau bindet einen unveraenderlichen Manifest-Snapshot an eine
lokale Revision und `connectionGenerationId`. Ein ausstehender initialer Ack
gehoert genau zu dieser Generation. Das erste passende `session.updated` wird
semantisch geprueft und erst danach als lokale Revision `applied` markiert;
Ereignisse geschlossener oder aelterer Verbindungen koennen keine Begruessung,
Mikrofonfreigabe oder Agentenoperation ausloesen. Providerseitige
Revisionskennungen werden nicht behauptet. Interne Diagnosen enthalten die drei
Digests, Revisions- und Generationenzuordnung sowie die Transformationsstufe,
ohne Token oder Rohtranskripte.

Die direkte Dependency `@openai/agents` ist exakt auf `0.13.5` gepinnt. Der
nachgewiesene Baum enthaelt `@openai/agents`, `@openai/agents-core`,
`@openai/agents-openai` und `@openai/agents-realtime` jeweils in `0.13.5`,
ohne gemischte oder doppelte Versionen.

Der API-basierte Read-only-Smoke-Test hat Healthcheck, Datenbankverbindung,
Runtime-Manifest und `list_bookable_services` mit zwei gelesenen Leistungen
nachgewiesen. Der echte WebRTC-Smoke-Test blieb am nativen Mikrofon-Dialog des
automatisierten Browsers stehen; er wurde kontrolliert beendet. Deshalb sind
Ack-/Generation-Nachweis im echten Browser und der menschliche Hoertest noch
offen, waehrend Fake-Transport, Fake-Kalenderprovider und der vollstaendige
automatisierte Pfad bestanden sind.

## Fix: `realtime_continuation_failed`

Der Fehler trat nach einer Kalenderprüfung auf, wenn die automatische SDK-
Fortsetzung nicht als passende zweite Response erkannt wurde oder der Provider
für diese Fortsetzung ein Fehlerereignis lieferte. Beide Fälle wurden zuvor in
der Oberfläche identisch als `realtime_continuation_failed` dargestellt. Der
sichtbare Code ist weiterhin absichtlich sicher und enthält keine Provider-
Rohdaten; die interne Diagnose unterscheidet jetzt die terminalen Gründe.

Die installierte SDK-Sequenz 0.13.5 ist:

`function_call_output` → SDK-Response-Sequencer → `response.created` →
`response.done`; `agent_tool_end` wird nach dem automatischen Request emittiert.
Der Coordinator akzeptiert deshalb auch eine bereits vorher eingetroffene
Folge-Response, wartet erst nach `agent_tool_end` auf den Ack und verwendet bei
fehlendem Ack höchstens einen weiteren Request über denselben SDK-Sequencer.

Umgesetzt wurden:

- Response-IDs werden aus den bekannten SDK-/Providerformen typgesichert
  gelesen; eine Folge-Response ohne ID beendet die Runde fail closed.
- Doppelte `agent_tool_end`-Events während der Übergabe starten keinen zweiten
  Recovery-Timer.
- Alte oder fremde `response.done`-Events bleiben durch die bestehende
  Response-ID-Prüfung wirkungslos.
- Providerfehler werden als redigiertes `realtime_provider_error`-Ereignis mit
  Typ, Code, Parameter, Response-ID und Turn-Zustand erfasst.
- Der terminale `turn_failed`-Eintrag enthält Turn-ID, Tool-Call-ID,
  ursprüngliche und Folge-Response-ID, Recovery-Anzahl und Fehlergrund. Es
  werden keine Tokens, Transkripte oder Kalenderinhalte aufgenommen.

Die neuen Tests bilden eine erfolgreiche automatische Kalender-Fortsetzung,
fehlende und doppelte Ereignisse, genau einen Recovery-Versuch, Providerfehler,
verspätete Responses und kontrollierten Cleanup ab. Die Frontend-Abnahme
umfasst nun 95 Tests in 9 Testdateien; alle bestehen. Verbleibende Grenze ist
der echte Browser-Mikrofontest: Der automatisierte Browser kann den nativen
Berechtigungsdialog nicht selbst bestätigen. Der echte Kalender-Schreibpfad
bleibt weiterhin ausgeschlossen.

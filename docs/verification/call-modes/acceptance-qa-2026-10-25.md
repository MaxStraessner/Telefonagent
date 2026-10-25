# Technischer Abnahmebericht – Betriebsmodi, Dokumentation und Voice

Datum: 25. Oktober 2026. Branch: `codex/call-modes-documentation-voice`.
Prüfbasis: Implementierungscommit `85949cf`, gegenüber `main` (`42686fe`), einschließlich der in dieser QA vorgenommenen Korrekturen.

## 1. Ergebnis und Abnahmegrenze

Die lokale technische Regression besteht nach Behebung der gefundenen Fehler. Die Mandantentrennung wurde mit echtem PostgreSQL 17 und einer Runtime-Rolle ohne Superuser- oder BYPASSRLS-Rechte geprüft. Die vollständige Webhook-/WebSocket-/Speicher-/Auswertungskette funktioniert in beiden Betriebsmodi mit simulierten Anbieterantworten.

**Dies ist keine Freigabe für einen Produktivrollout.** Reale Telefonie, tatsächliche OpenAI-Antwortqualität, ein genehmigtes Datenschutzverfahren und die unten genannten Betriebsprüfungen bleiben offen. Ein zusätzlicher Alembic-Metadatencheck meldet bereits auf `main` bestehende Abweichungen; dieser Check ist ausdrücklich nicht grün.

Es wurden ausschließlich synthetische Daten und lokale Testressourcen verwendet. Keine echten Anrufe, kostenpflichtigen KI-Aufrufe, produktiven Kalenderaktionen, Veröffentlichung, Merge, Deployment oder Änderungen am Produktivserver.

## 2. Gefundene und behobene Fehler

| Priorität | Befund / mögliche Auswirkung | Korrektur und Nachweis |
| --- | --- | --- |
| Hoch | Der Hintergrundprozess findet unter tatsächlichem PostgreSQL-RLS ohne Benutzerkontext keine Unternehmen. Automatische Auswertung und Aufbewahrungsfristen würden ausbleiben. | Eng begrenzte `SECURITY DEFINER`-Funktion liefert nur Telefonie-Mandantenkennungen. Inhaltszugriffe erfolgen anschließend in einem eigenen gebundenen RLS-Kontext. Tatsächlicher Worker-Zyklus mit Runtime-Rolle geprüft. |
| Hoch | Veraltete ORM-Objekte können einen abgeschlossenen Anruf wieder verbinden oder gelöschte Inhalte beim Abschluss zurückschreiben. | Gesperrte Datensätze werden frisch geladen; Inhaltslöschung bleibt terminal. Regressionen mit zwei Datenbanksitzungen und PostgreSQL. |
| Hoch | Eine Inhaltslöschung kann eine inzwischen fertiggestellte Analyse übersehen, wenn Felder im alten Objekt noch leer waren. | Schreibende Anruf-API-Endpunkte lesen unter Zeilensperre neu ein. Regression löscht ausdrücklich eine erst nach dem ursprünglichen Laden gespeicherte Zusammenfassung und Kontakte. |
| Hoch | Verspätete alte ASR-Bestätigungen können die jüngere Ablehnung durch ein früheres „Ja“ ersetzen. Eine alte Zustimmung bleibt außerdem während einer neuen Äußerung verfügbar. | Aktuelles Eingabe-Item verfolgen; bei neuer Sprache Zustimmung sofort verwerfen. Alte Bestätigungen überschreiben die aktuelle Äußerung nicht. Kalenderbestätigung ohne aktuellen bestätigten Text bleibt gesperrt. |
| Hoch | Eine konkurrierende zweite Media-Verbindung kann den noch aktiven ursprünglichen Anruf als fehlgeschlagen abschließen. | Abschluss nur durch den Stream-Eigentümer beziehungsweise einen gescheiterten provisionierten Aufbau. Zweite Verbindung wird in beiden PostgreSQL-Prozessketten abgewiesen, ursprünglicher Anruf bleibt verbunden. |
| Hoch | Buchungssperren sollten auch bei direktem Aufruf des zentralen Kalenderdienstes gelten. | Kalenderdienst validiert Mandant und Gesprächssitzung und sperrt `answering_machine` vor Slot-/Anbieterzugriff. Unabhängige Kalenderverwaltung bleibt möglich. |
| Mittel | Hundert noch nicht fällige Wiederholungen können fällige Arbeit verdrängen. | Fälligkeit vor dem Limit filtern, abgeschlossene Arbeit priorisieren, Kandidaten erneut sperren und prüfen. Regression mit 100 blockierenden Datensätzen. |
| Mittel | Prozessabbrüche können unbegrenzt erneut beansprucht werden; Änderungen am Posteingang verschieben die bisherige Recovery-Zeit. | Eigene Lease-Frist über `analysis_next_attempt_at`, Wiederaufnahme nach zwei Minuten, höchstens drei beanspruchte Versuche; sichtbarer Fehler bei Erschöpfung. |
| Mittel | Ein Fehler in einem Unternehmen bricht den gesamten Worker-Zyklus ab; verwaiste Gespräche verwenden möglicherweise neue statt eingefrorener Laufzeitlimits. | Fehler pro Unternehmen isolieren; Sitzungsdauer aus dem gespeicherten Manifest verwenden. |
| Mittel | Abbruch während des Stream-Wartens hinterlässt laufende Empfangs-/Sendetasks. | Alle Stream-Tasks im `finally` abbrechen und abwarten. Cancellation-Regression prüft beide Empfänger. |
| Mittel | Verspätete Metadaten und finale Segmente korrigieren die gespeicherte Reihenfolge beziehungsweise Vollständigkeit nicht zuverlässig. | Bekannte Segmente bei Metadatenereignissen erneut sichern; bestätigten Text gegen alte Deltas schützen; Vollständigkeit nach später Bestätigung neu berechnen. |
| Mittel | Deaktivierte Unterbrechungen werden im Media-Bridge nicht vollständig beachtet. | `interrupt_response=false` verhindert das lokale Löschen der Audioausgabe. |
| Mittel | Überlange Segmente werden still abgeschnitten. | Längenbegrenzung bleibt bestehen, erzeugt aber sichtbar `transcript_segment_truncated`; keine unbegründete Vollständigkeitsbehauptung. |
| Mittel | Details bleiben während der asynchronen Auswertung unverändert; gelöschte oder noch nicht ausgewertete Inhalte werden irreführend bezeichnet. | Details alle fünf Sekunden mit höchstens einer offenen Abfrage aktualisieren; Aufräumen beim Seitenwechsel; eindeutige Lösch-/Laufzeit-/Auswertungsanzeigen. |
| Hoch / kritisch laut Paketmeldungen | Bekannte Schwachstellen in vorhandenen Python- und npm-Abhängigkeiten. | Paketupdates, vollständige Regression und beide Docker-Builds. Abschließende Feed-Abfragen ohne bekannte Funde; Umfang und Grenzen siehe Abschnitt 5. |
| Mittel | Docker-Kontexte schließen lokale Backups, Testdatenbanken und mögliche `.env`-Dateien nicht ausdrücklich aus. | Beide `.dockerignore` ergänzt; Backend schließt zusätzlich SQLite-Dateien und Ruff-Cache aus. Finale Images aus eingeschränktem Kontext neu gebaut. |

Die ersten gezielten adversarialen Regressionen reproduzierten sechs Fehler vor den Korrekturen. Danach wurden die betroffenen Tests und mehrfach die vollständige Regression ausgeführt.

## 3. Automatisierte Prüfergebnisse

| Prüfung | Ergebnis |
| --- | --- |
| Vollständiges Backend auf Windows / Python 3.14 | **271 bestanden**, keine übersprungenen Tests |
| Vollständiges Backend im finalen Linux-/Python-3.13-Image | **271 bestanden**, keine übersprungenen Tests, ebenfalls mit PostgreSQL/RLS |
| Darin tatsächliche PostgreSQL-/RLS-Integration | **18 bestanden** |
| Frontend / Vitest 5.0.5 | **112 bestanden**, 12 Testdateien |
| Ruff für `backend/app` und `backend/tests` | Bestanden |
| ESLint | Bestanden, keine Warnungen erlaubt |
| TypeScript und Vite-Produktionsbuild | Bestanden |
| Backend-Docker-Build / Python 3.13 unter Linux | Bestanden |
| Frontend-Docker-Build / Node 22 Alpine | Bestanden, einschließlich `npm ci` |
| `pip check` | Keine inkompatiblen installierten Anforderungen |
| `docker-compose config --quiet` | Bestanden |
| `git diff --check` | Bestanden |
| `npm audit --json`, einschließlich Entwicklungsabhängigkeiten | **0 bekannte Schwachstellen** |
| PyPI-Abgleich der 60 installierten lokalen Python-Distributionen | **0 aktive bekannte Meldungen**, keine Abfragefehler |
| PyPI-Abgleich der 59 Distributionen im finalen Backend-Image | **0 aktive bekannte Meldungen**, keine Abfragefehler |
| `alembic check` / ORM-Metadatenvergleich | **Nicht bestanden: 51 bestehende Unterschiede**, identisch auf `main`, keine branchspezifischen Unterschiede nach Ausklammern neuer 0017-Spalten |

Hinweise ohne Testfehler: Starlette meldet die bestehende TestClient-/httpx-Deprecation. Vite meldet ein Hauptbundle von rund 1,02 MB unkomprimiert beziehungsweise 299 KB gzip sowie entfernte, ungünstig platzierte PURE-Kommentare aus Zod. Diese Hinweise verhindern Tests und Build nicht.

### Erfolgreich abgedeckte Funktionen

- Unternehmen können Modus, Nachrichtenbegrüßung und Aufbewahrungsfrist speichern. Bestehender Vollagent bleibt Standard.
- Ein laufender Anruf behält Modus, Tools, Modell und Laufzeitmanifest trotz zwischenzeitlicher Änderung der Unternehmenseinstellungen und doppeltem Twilio-Webhook.
- Signatur-/Stream-Token-Prüfung, Nummernzuordnung, Lifecycle, doppelte Anfragen, Verbindungsabbruch und Aufräumen der Stream-Tasks.
- Fortlaufende Transkriptsicherung vor Gesprächsende; vorläufige/bestätigte Segmente, Sprecher, verspätete Metadaten, Reihenfolge und Deltas, Abbruch- und Fehlerkennzeichnung.
- Persistente automatische Auswertung außerhalb des Media-Handlers; striktes JSON-Schema, Pydantic-Validierung, `store=false`, begrenzte Wiederholungen, Lease-Recovery und Schutz vor veralteten Ergebnissen.
- Änderungen der Transkriptrevision verwerfen eine bereits laufende alte Auswertung. Inhaltslöschung verhindert die Wiederbelebung durch späte Analyse oder Abschluss.
- Anrufhistorie und Detailansicht einschließlich asynchroner Aktualisierung, Rückrufbedarf, Kontakten, Dokumentationsstatus und tatsächlichen Aktions-/Terminverknüpfungen.
- Vorhandene Authentifizierungs-, Rollen-, CSRF-, Kalender-, Bestätigungs-, Idempotenz- und Werkzeugtests bestehen weiterhin.
- Nachrichtenmodus: leere Werkzeugliste im tatsächlichen Sitzungsaufbau; erzwungene Tool-Events werden abgewiesen. Zusätzliche Sperren in Dispatcher, Orchestrierung, Audit und zentraler Buchung. Keine Kalenderanbieter-I/O und keine Buchung im geprüften Prozess.
- Vollagent: vorhandene Werkzeuge bleiben verfügbar; lokaler Katalogaufruf erfolgreich und in der Historie nachweisbar. Kalenderbuchungen werden zusätzlich durch die bestehende Kalenderregression mit simuliertem Anbieter geprüft.
- Löschberechtigungen, Aufbewahrungsfrist und Ausnahme historischer `legacy`-Datensätze.
- Getrennte Live-Protokolltests und Evaluationspfad; kein automatischer Produktionswechsel zu GPT Live.

## 4. Tatsächliches PostgreSQL, Migration und Prozesskette

Die isolierte PostgreSQL-17-Instanz lief ausschließlich auf `127.0.0.1:25439`, Daten im RAM-Dateisystem, ohne persistentes Volume. Runtime-Rolle: `rolsuper=false`, `rolbypassrls=false`. Für `call_sessions` und `agent_configurations` wurden `ENABLE ROW LEVEL SECURITY` und `FORCE ROW LEVEL SECURITY` bestätigt; Migrationstand `0017`.

Geprüft wurden ungebundener Kontext, Unternehmen A/B, Plattformzugriff mit und ohne expliziten Unternehmenskontext, Anmelde-/Einladungs-/Routingresolver, verweigerte fremde Schreibzugriffe und die neue Worker-Ermittlung. Ein fremder Anruf bleibt über die API 404; auch direkte SQL-Zugriffe mit Runtime-Rolle sehen fremde Inhalte nicht. Der Resolver liefert ausschließlich UUIDs, keine Gesprächsinhalte. Die zugrunde liegende SQL-Funktion hat festen Suchpfad und qualifizierten Tabellenzugriff; die Runtime-Rolle bleibt RLS-gebunden.

Die Migration wurde auf SQLite und PostgreSQL aufgebaut. Zusätzlich wurde **nur in dieser wegwerfbaren Testdatenbank** 0017 → 0016 → 0017 ausgeführt: vorhandene 0016-Anrufdaten und Transkripte bleiben gleich, die RLS-/FORCE-Flags bleiben erhalten, die neue Resolverfunktion wird wieder angelegt. Das ist kein verlustfreier Rückbau neuer 0017-Daten: ein Downgrade entfernt definitionsgemäß die neuen Spalten. Kein Produktionsdowngrade wurde durchgeführt oder empfohlen.

Für beide Modi wurde folgende Kette tatsächlich durch die Anwendung ausgeführt:

1. Synthetisches Unternehmen mit Konfiguration, Telefonnummernroute und Kalenderfähigkeit in PostgreSQL provisionieren.
2. Gültig signierten Twilio-Voice-Webhook über HTTP aufrufen; Informationsansage und TwiML-Stream-Token prüfen.
3. Unternehmensmodus ändern und Webhook wiederholen; ursprüngliches Manifest muss erhalten bleiben.
4. Signierten Media-WebSocket öffnen, gültiges Start-/Audioereignis senden und Sitzungsaufbau prüfen.
5. Simulierte OpenAI-Transkript-, Audio- und Tool-Events über den tatsächlichen Bridge verarbeiten; bestätigten Text **während des laufenden Anrufs** mit der RLS-Rolle aus PostgreSQL lesen.
6. Konkurrierenden Stream abweisen und Unversehrtheit des ursprünglichen Streams prüfen; Anruf regulär stoppen und Abschluss speichern.
7. Tatsächlichen Hintergrundzyklus ohne Benutzerkontext starten; gültige simulierte Responses-Ausgabe automatisch übernehmen.
8. Tatsächlich anmelden; Historie und Details mit Zusammenfassung, Rückrufbedarf, Modell, Modus und Aktionen abrufen; fremden Anruf abweisen.

Nur die externe OpenAI-WebSocket-/Responses-I/O und Kalenderanbieter-I/O sind simuliert. HTTP, WebSocket-Handler, SQLAlchemy, PostgreSQL, RLS, Worker, Authentifizierung und Anruf-API sind real. Die Frontenddarstellung wird ergänzend durch die Komponententests geprüft; dies ist kein echter Twilio/OpenAI-Netzwerktest und kein durchgehender Browser-Telefonietest.

### Vorbestehende Schemaabweichung

Alembic meldet unter anderem fehlende ORM-Abbildungen zusammengesetzter Fremdschlüssel/Indizes, Typbreiten und abweichende Unique-Definitionen aus älteren Migrationen. Ein lesender Vergleich mit dem über `git show main:backend/app/models/entities.py` geladenen Basismodell bestätigt dieselben 51 Unterschiede. Die neue Migration fügt diese Unterschiede nicht hinzu.

**Keine automatisch generierte „Korrekturmigration“ anwenden:** Sie würde teilweise vorhandene Schutzconstraints entfernen. Das Modell-/Migrationsalignment benötigt eine gesonderte Änderung mit Constraint- und RLS-Regression. Die expliziten Upgrade-/Datenerhalt-/RLS-Tests für 0017 sind bestanden; die allgemeine Autogenerierung ist derzeit kein verlässlicher Freigabecheck.

Die Resolverfunktion ist Bestandteil der noch unveröffentlichten 0017. Falls eine frühere Fassung dieser Branch-Migration bereits auf einer anderen, aufzubewahrenden Datenbank angewandt wurde, reicht `upgrade head` dort nicht zur Nachinstallation der Funktion. Vor Verwendung muss dies geprüft und gegebenenfalls eine additive Folgemigration erstellt werden. Solche Datenbanken nicht zum Zweck der Nachinstallation downgraden.

## 5. Sicherheitsprüfung und Paketänderungen

Geprüft wurden Mandanten-/Rollenbindung, Webhook- und Streamauthentifizierung, technische Aktionssperren, konkurrierende Zustandsübergänge, Transkript-/Analysevalidierung, Löschung, Protokollierung und der Docker-Build-Kontext.

Aktualisiert wurden unter anderem `cryptography` 50.0.0, `python-multipart` 0.0.31, `aiohttp` 3.14.4, `certifi` 2026.10.19, `multidict` 6.9.1, `PyJWT` 2.15.0 und `urllib3` 2.8.0. Der Backend-Build installiert pip 26.2. Frontend: Vite 7.3.7, Vitest 5.0.5 und kompatible transitive Lockfile-Updates, darunter MCP-SDK, Zod und proxy-addr. Das Agents-SDK bleibt 0.13.5.

Der Python-Abgleich fragt für jede tatsächlich installierte Distribution in der lokalen Umgebung und im finalen Backend-Image den öffentlichen versionsbezogenen PyPI-JSON-Datensatz ab und berücksichtigt nicht zurückgezogene Schwachstellenmeldungen. Er ist **kein pip-audit-Lauf**, keine vollständige CVE-Datenbank und keine Prüfung des Container-Betriebssystems. npm wurde mit dem Registry-Audit-Endpunkt inklusive Entwicklungsabhängigkeiten geprüft. Null Meldungen bedeutet keine Garantie für Schwachstellenfreiheit. Es wurden nur öffentliche Paketnamen und Versionen übertragen.

Quellen zur Reproduktion: [npm audit](https://docs.npmjs.com/cli/commands/npm-audit), [PyPI JSON API](https://docs.pypi.org/api/json/), [Vitest-Migration](https://vitest.dev/guide/migration/). Die bereits vorhandenen Verschlüsselungs-, Authentifizierungs-, Kalender- und SDK-Regressionen bestehen nach den Updates.

## 6. Verbleibende Risiken und echte Telefonieabnahme

| Offen | Erforderliche Prüfung / Grenze |
| --- | --- |
| Echte Twilio-/OpenAI-Kompatibilität | Kontrollierte Testnummer, gültige Providerzugänge, echte Signaturen hinter Reverse Proxy, Media-/Statuscallbacks, Auflegen während Hinweis, Verbindungsaufbau und Abschluss prüfen. |
| Deutsche Sprachqualität | Namen, Umlaute, Telefonnummern mit Korrektur, Rückrufwunsch, Verneinung, Pausen, überlappende Sprache, Unterbrechungen an/aus, Gesprächsabschluss; gehörte Ausgabe und gespeicherten Text getrennt vergleichen. |
| Buchungsbestätigung im Vollagenten | Eigenen Testkalender verwenden: ausdrückliches Ja, spätes Nein, Slotkorrektur, doppelte Tool-Events, Timeout nach Buchung, Idempotenz und tatsächlichen Kalendereintrag prüfen. Keine produktiven Kalender beschreiben. |
| Aktionssperre im Nachrichtenmodus | Reale gesprochene Buchungswünsche und Prompt-Injection versuchen; keine externen Aktionen, kein Kalenderzugriff, kein falsches Buchungsversprechen. |
| Reale KI-Auswertung | Zusammenfassungen mit festem deutschen Referenzsatz prüfen: belegte Kontakte, unbekannte Angaben, kein erfundener Termin, Rückruf ja/nein, Teiltranskripte. Strukturelle Schemaerfüllung beweist keine sachliche Richtigkeit. |
| Unterbrechung/Providerfehler | Netzverlust in beide Richtungen, WebSocket-Abbruch, Prozessneustart nach gesicherten Segmenten, verspätete Finalevents, 429/5xx und sichtbare Wiederaufnahme in einem kontrollierten Testsystem. |
| Last und lange Historien | Gleichzeitige Telefonate, viele Unternehmen, Worker-Durchlaufzeit, DB-Locks, Speicher und UI-Ladezeit messen. Worker und unpaginierte Historie sind funktional geprüft, nicht unter Produktionslast zertifiziert. |
| Bestehender Schema-Drift | Die 51 älteren ORM-/Migrationsunterschiede separat auflösen; bis dahin keine ungeprüfte Autogenerierung. |
| Datenschutz und Betrieb | Firmenspezifische Rechtsgrundlage, Information/Zustimmung, Auftragsverarbeitung, Aufbewahrung technischer Metadaten und historische Daten/Backups abstimmen. Kein rechtlicher Freigabenachweis in dieser QA. |
| Produktionsmigration und Wiederherstellung | Vor separat freigegebenem Deployment Datenstand, Backup/Restore-Verfahren, Laufzeitrolle/Funktionsrechte und aktive Gespräche im vorgesehenen Betriebsverfahren prüfen. Diese lokale QA testet keine Produktionskopie oder Wiederherstellung. |
| GPT Live | Bleibt reine Evaluation für Nachrichtenaufnahme. Kein belegter Vorteil bei Latenz, Qualität oder Kosten; kein vollständiger Live-Kalenderbetrieb freigegeben. Bisheriger Realtimeprovider bleibt Standard. |

## 7. Reproduktion und Änderungsumfang

Aus `backend`, mit den beiden ausdrücklich auf eine isolierte PostgreSQL-17-Testdatenbank gerichteten Umgebungsvariablen `TEST_POSTGRES_MIGRATOR_URL` und `TEST_POSTGRES_RLS_URL`:

```powershell
.venv\Scripts\python.exe -m ruff check app tests
.venv\Scripts\python.exe -m pytest -q tests -p no:cacheprovider --basetemp=../.local-backups/qa-reproduction --tb=short
.venv\Scripts\python.exe -m pip check
```

Die Datenbank muss vorab mit der Migratorrolle bis `head` migriert sein. Das vorhandene PostgreSQL-Fixture erwartet den Testdatenbanknamen `telefonagent_auth_test` und legt die eingeschränkte Rolle an. Diese Tests sind für synthetische Wegwerfdatenbanken bestimmt. Auf diesem Windows-Rechner braucht TestClient Zugriff auf lokale Loopback-Sockets.

Aus `frontend`: `npm.cmd run lint`, `npm.cmd test -- --run`, `npm.cmd run build`, `npm.cmd audit --json`.
Aus dem Repository: `docker-compose config --quiet`, `git diff --check`; beide Dockerfiles wurden mit ausschließlich lokalen QA-Tags gebaut.

Neue Regressionen liegen in `backend/tests/test_call_quality.py`, den erweiterten `backend/tests/integration/test_postgres_rls.py` und `frontend/tests/CallHistory.test.tsx`. Die Vitest-Mocktypen wurden für Version 5 angepasst. Korrekturen betreffen die vorhandenen Anruf-/Twilio-/Kalenderdienste und Anrufansichten, Migration 0017, Paketdateien und Dockerkontexte. Keine Änderung am Kontenschema, Deploymentpfad oder produktiven Datenvolume.

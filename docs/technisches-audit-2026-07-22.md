# Technisches Audit des Telefonagenten

Auditdatum: 22.07.2026  
Geprüfter Quellstand: `08b22366642ec9ee337103b19d414ae12b4e4502` auf `main`  
Auditmodus: read-only; einzige Repository-Änderung ist dieser Bericht  
Laufzeitumgebung: lokales Docker Compose, Stand der laufenden Container separat ausgewiesen

## A. Management Summary

### Gesamteinstufung

Der Telefonagent ist ein funktionsfähiger modularer Monolith mit React-Frontend, FastAPI-Backend, PostgreSQL und Provideradaptern für Google Calendar und Microsoft Graph. Der Browser baut die Realtime-Verbindung direkt zu OpenAI auf; das Backend stellt Client-Secrets, Konfiguration und Tool-Endpunkte bereit. Die Architektur ist grundsätzlich erhaltenswert, aber für einen produktiven Betrieb noch nicht freigabefähig.

Die wichtigste Einschränkung für den aktuellen Test ist operativ: Docker war gesund, lief aber nicht auf dem geprüften HEAD. Die aktuellen Images wurden erfolgreich gebaut, die laufenden Container jedoch bewusst nicht neu gestartet. Ein Test der laufenden Oberfläche prüft daher zunächst den älteren Containerstand, nicht automatisch den aktuellen Commit.

### Priorisierte Kernergebnisse

| Priorität | Befund | Bewertung |
|---|---|---|
| Kritisch als Produktionsblocker | Identität und Tenant werden aus statischer Umgebungskonfiguration abgeleitet, nicht aus einer Request-Authentifizierung. | `K-01` |
| Hoch | Ein nicht verfügbarer Wunschtermin führt zwar zu Alternativen, die Auswahl einer Alternative wird aber nicht zuverlässig in den Buchungszustand übernommen. | `H-01` |
| Hoch | Tool-Fortsetzung besitzt keinen Timeout- oder Recovery-Wächter; mehrere parallele Tool-Aufrufe können falsch oder gar nicht zugeordnet werden. | `H-02` |
| Hoch | Der Lock schützt nur identische Zeitintervalle; überlappende Buchungen können unter Parallelität beide die Prüfung passieren. | `H-03` |
| Hoch | Laufende Container sind vom aktuellen Quellstand entkoppelt. | `H-04` |
| Hoch | Die Bestätigungslogik akzeptiert nur eine enge Liste exakter Texte, obwohl der Prompt semantische Zustimmung vorsieht. | `H-05` |
| Mittel | Sessions bleiben in der Datenbank aktiv, obwohl sie beendet wurden; Realtime- und Gesprächsdiagnose ist dadurch unvollständig. | `M-01` |
| Mittel | Tool- und Audiozustände werden mehrfach und teilweise aus kurzlebigen Browserereignissen abgeleitet. | `M-02` |

Die bestehenden Tests sind ein gutes Regressionsnetz für die bereits modellierten Fälle: 112 Backendtests und 68 Frontendtests bestanden. Sie bilden jedoch nicht die kritischen Zustandskombinationen ab, insbesondere keine vollständige alternative Buchung, keinen verlorenen Tool-Fortsetzungsresponse, keine doppelte Tool-Ausführung und keine echte Tenant-Isolation über Requests.

### Entscheidung

Ein vollständiger Neubau ist nicht erforderlich. Beibehalten werden sollten Datenmodell, Tenant-Filter, verschlüsselte OAuth-Token, Provideradapter, Prompt-Compiler und die aktuelle Trennung zwischen Browser-Realtime-Transport und Backend-Tools. Refaktoriert werden müssen vor allem die Realtime-Orchestrierung, der Buchungszustandsautomat, die Authentifizierungsgrenze und die Betriebsdiagnose. Der nächste einzelne Umsetzungsschritt sollte ein deterministischer End-to-End-Test für `slot_unavailable → alternative Auswahl → erneute Prüfung → Bestätigung → genau eine Buchung` sein; der dazu fehlende Zustandsübergang muss explizit implementiert werden.

## B. Architekturkarte und Inventar

### Architekturkarte

```text
Browser / React
  ├─ REST: Konfiguration, Promptvorschau, Kalenderstatus, Tools
  ├─ WebRTC: OpenAI Realtime mit kurzlebigem Client-Secret
  ├─ Mikrofon, VAD, Audio-Playback und Transkriptzustand
  └─ lokale UI-Zustände für Session, Tool-Widget und Buchungsstatus
          │
          ▼
FastAPI / Backend
  ├─ Tenant-/User-Kontext aus lokaler Umgebungsadapter-Konfiguration
  ├─ Agent-Konfiguration und Prompt-Compiler
  ├─ fünf Realtime-Tools
  ├─ BookingOrchestrator und Bestätigungsprüfung
  ├─ Google- und Microsoft-Provideradapter
  └─ SQLAlchemy-Repositories und Audit-/Sessionpersistenz
       │              │                       │
       ▼              ▼                       ▼
 PostgreSQL       Google Calendar        Microsoft Graph
       │
       └────────────── OpenAI Realtime über Browser-WebRTC ──────────────┘
```

Es ist kein SIP-, Telefonie- oder PSTN-Transport im geprüften Repository enthalten. Der Begriff Telefonagent beschreibt hier den Sprachagenten im Browser; eine echte Telefonanlage wäre eine zusätzliche Integrationsschicht.

### Vollständiges Komponenteninventar

| Bereich | Geprüfte Hauptpfade | Aufgabe |
|---|---|---|
| Frontend | `frontend/src/app`, `features`, `pages`, `shared` | Routing, Einstellungen, Testdialog, Audio und Realtime-UI |
| Realtime | `frontend/src/features/realtime/realtimeClient.ts`, `useRealtimeVoice.ts`, `playback.ts`, `events.ts`, `toolExecution.ts` | Sessionaufbau, Events, Audio, Tool-Fortsetzung, Statusableitung |
| Backend API | `backend/app/api/v1` | Health, Plattform, Tenant, Agent, Kalender, Services, Termine und Promptvorschau |
| Agent | `backend/app/services/agent_runtime.py`, Prompt-Compiler und Realtime-Konfiguration | Versionierte Agenteneinstellungen, Stimme, Tempo, VAD, Tools |
| Buchung | `backend/app/services/booking_orchestrator.py`, `booking_confirmation.py`, `calendar_booking.py` | Validierung, Verfügbarkeit, externe Erstellung, lokale Persistenz, Idempotenz |
| Provider | Google- und Microsoft-Integrationen unter `backend/app` | OAuth, FreeBusy/calendarView, Event-Erstellung und Disconnect-Flows |
| Persistenz | Modelle und Repositories unter `backend/app/models` und `backend/app/repositories` | Tenant-Daten, Sessions, Snapshots, ToolAudit, Buchungen |
| Migrationen | `0001_platform_foundation.py` bis `0006_snapshot_bootstrap_metadata.py` | lineare Schemaentwicklung bis Alembic-Head 0006 |
| Betrieb | `docker-compose.yml`, Dockerfiles, `.env.example`, Paket-Lockfiles | lokale Container, Laufzeitkonfiguration und reproduzierbare Abhängigkeiten |
| Tests | `backend/tests` und `frontend/src/**/*.test.*` | API-, Service-, Provider-, Realtime- und UI-Regressionstests |

Insgesamt wurden 139 versionierte Dateien inventarisiert. Der Backend-Code umfasst ungefähr 8.111 Zeilen, der Frontend-Code ungefähr 8.971 Zeilen. Die größten Wartungsschwerpunkte sind `backend/app/api/v1/calendar.py` mit über 800 Zeilen und `frontend/src/features/realtime/realtimeClient.ts` mit knapp 500 Zeilen.

### Ausgangsbasis und Laufzeit

- Lokaler Branch: `main`, fünf Commits vor `origin/main`.
- Arbeitsbaum vor dem Audit: sauber.
- Host: Node 24.11.1, npm 11.17.0, Python 3.14.6 in der lokalen virtuellen Umgebung, Docker 29.6.1, Compose 5.3.
- Backend-Container: Python 3.13.14.
- Alembic-Datenbankstand: Revision 0006.
- Laufende Container meldeten sich gesund; sie liefen jedoch mit älteren Image-IDs als die nach HEAD gebauten Images. Die aktuellen Images wurden erfolgreich gebaut, aber nicht gestartet.
- Compose bindet Backend und Frontend lokal auf `0.0.0.0:8001` und `0.0.0.0:5173`. Das ist für lokale Tests bequem, vergrößert aber bei fehlender Firewall/Auth die Angriffsfläche.

Geheimnisse, E-Mail-Adressen, Telefonnummern, Kalenderinhalte, Tokens und Gesprächsinhalte wurden in diesem Bericht nicht übernommen.

## C. Kritischer End-to-End-Gesprächsablauf

### Ablauf vom Sitzungsstart bis Cleanup

| Schritt | Übergang | Zustandsquelle und relevante Dateien | Fehler-/Race-Risiko |
|---:|---|---|---|
| 1 | UI öffnet Agententest | Agentenseiten und `useRealtimeVoice.ts` | UI kann veraltete Konfiguration anzeigen, wenn Runtime- und UI-Abfrage auseinanderlaufen |
| 2 | Runtime-Konfiguration laden | `agent_runtime.py`, Agent-API | Konfigurationsversion muss zu Secret und Client passen |
| 3 | Client-Secret anfordern | Realtime-API, `services/realtime.py` | Identität basiert aktuell auf statischem Kontext; Secret darf nicht ohne echte Auth-Grenze ausgestellt werden |
| 4 | Browser startet WebRTC | `realtimeClient.ts`, lokaler `@openai/agents`-Code 0.13.5 | Client kann Sessionparameter überschreiben; App vergleicht Werte, aber OpenAI erhält dennoch eine clientseitige Konfiguration |
| 5 | Audio/VAD beginnt | `playback.ts`, Audio- und Sessionhandler | Mikrofon-, Playback- und Response-Zustand sind getrennt abgeleitet |
| 6 | Benutzer nennt Leistung/Termin | Realtime-Agent und Tool-Schema | Modell kann ankündigen, ohne im selben Turn ein Tool aufzurufen |
| 7 | Tool wird aufgerufen | `toolExecution.ts`, Backend-Tool-Endpunkte | Tool-Argumente werden serverseitig geprüft; Reihenfolge und Wiederholung müssen zusätzlich orchestriert werden |
| 8 | Ergebnis lokal gespeichert | `ToolAudit`, Conversation-/Snapshot-Persistenz | `continuation_response_id` wird nicht zuverlässig nachgetragen |
| 9 | Tool-Ergebnis an OpenAI senden | SDK `openaiRealtimeBase.js` | SDK sendet Output und `response.create`; App wartet passiv und hat keinen Timeout |
| 10 | Folgeantwort abspielen | `realtimeClient.ts`, `playback.ts` | Verlorene oder falsch zugeordnete Folgeantwort kann den Gesprächszustand blockieren |
| 11 | Verfügbarkeit prüfen | `calendar.py`, Provideradapter | Snapshot ist nur Vorprüfung; finale Prüfung erfolgt vor Persistenz |
| 12 | Alternative anbieten | `conversation_find_alternatives` | Alternativen werden geliefert, aber nicht in `selected_slot`/Zustandsübergang übernommen |
| 13 | Buchung bestätigen | `booking_confirmation.py`, `booking_orchestrator.py` | Nur exakte Bestätigungsstrings; nach `slot_unavailable` schlägt Finalize ab |
| 14 | Extern erstellen und lokal persistieren | `calendar_booking.py` | Lock schützt identische Intervalle, nicht alle Überlappungen; Reconciliation bleibt manuell |
| 15 | Session beenden | UI-Cleanup und Sessionpersistenz | Kein sicherer Abschlussübergang; Datenbank zeigt beendete Gespräche weiter als `active` |

### Realtime-Ereignis- und Zustandsmodell

Die installierte Agents-Version 0.13.5 verarbeitet ein normales Tool-Ergebnis als `conversation.item.create` mit `function_call_output` und löst danach `response.create` aus. Das entspricht der offiziellen OpenAI-Realtime-Semantik. Der kritische Fehler liegt daher nicht in einer fehlenden SDK-Grundfunktion, sondern in der fehlenden App-seitigen Liveness- und Zuordnungslogik um diese Funktion herum.

| Zustand | Audio | Mikrofon | Tool | Booking | Gültigkeit |
|---|---|---|---|---|---|
| `idle` | aus | bereit | keiner | unverändert | gültig |
| `listening` | aus | aktiv | keiner | unverändert | gültig |
| `responding` | aktiv | gesperrt/Unterbrechung nach Konfiguration | optional | unverändert | gültig |
| `tool_running` | meist aus | gesperrt | läuft | wartet | gültig |
| `continuation_starting` | aus oder Restaudio | gesperrt | Ergebnis gesendet | wartet | gültig, aber ohne Timeout gefährlich |
| `slot_available` | UI-abhängig | abhängig von Response | keiner | bereit für Bestätigung | gültig |
| `slot_unavailable` | UI-abhängig | abhängig von Response | keiner | Alternative erforderlich | gültig, aber Finalize-Pfad unvollständig |
| `ended` + Playback `playing` | inkonsistent | aus | keiner | unverändert | ungültige Kombination |
| `tool_running` + `ended` | aus | aus | Ergebnis kann verspätet eintreffen | unklar | ungültige Kombination |
| `finalizing` ohne erneute Verfügbarkeitsprüfung | beliebig | gesperrt | keiner | Schreibvorgang | ungültig |

Das Frontend hält eine begrenzte Ereignis-FIFO von 40 Einträgen. Audio- und Transkript-Deltas verdrängen darin wichtige Tool- und Response-Ereignisse. Das Tool-Widget kann deshalb null Tool-Aufrufe anzeigen, obwohl das Backend fünf Aufrufe protokolliert hat. Für Diagnosezwecke ist diese Ereignisliste keine verlässliche Quelle.

### Fünf Tools und Buchung

| Tool | Validierung | Ausführung/Ergebnis | Fortsetzung | Idempotenz/Risiko |
|---|---|---|---|---|
| `list_bookable_services` | Tenant und Filter serverseitig | Services aus Repository | Ergebnis an Modell | read-only; bei Wiederholung unkritisch |
| `resolve_service` | Service-ID, Tenant, Aktivität | eindeutige Leistung auflösen | Ergebnis an Modell | read-only; keine Doppelwirkung |
| `check_exact_availability` | signierter Slot, Zeitzone, Dauer, Mitarbeiter | Snapshot/Provider plus lokale Prüfung | Ergebnis an Modell | read-only; Snapshot kann veralten |
| `find_alternatives` | Datum/Zeitfenster, Dauer, Tenant | freie Alternativen aus Snapshot/Provider | Ergebnis an Modell | read-only; liefert aber keinen Auswahlübergang |
| `finalize_booking` | explizite Bestätigung, Slot/Service/Customer | externe Event-Erstellung, lokale Buchung | Ergebnis an Modell | Idempotenz vorhanden, aber Intervall-Lock unvollständig |

Die Providersemantik ist grundsätzlich passend umgesetzt: Google FreeBusy wird für Verfügbarkeit verwendet und Google Events insert für die Erstellung; Microsoft nutzt `calendarView` für den Zeitraum und Event-Erstellung mit Transaktions-/Idempotenzmerkmalen. Der Bericht bewertet diese Zuordnung gegen die offiziellen Providerdokumentationen; die Bewertung der App-spezifischen Übergänge ist eine Schlussfolgerung aus dem lokalen Code.

## D. Ursachenanalyse der sechs bekannten Fehlergruppen

### D.1 Fehlende Folgeantwort nach Tool-Aufruf

Es gibt zwei unterschiedliche Fehlerbilder, die auseinandergehalten werden müssen:

1. Das Modell kündigt einen nächsten Schritt an, erzeugt aber in diesem Turn keinen Tool-Call. Das ist ein Orchestrierungs-/Promptverhalten und nicht automatisch ein Transportfehler.
2. Nach einem echten Tool-Call wird das Ergebnis gesendet, aber eine Folgeantwort kann ausfallen oder falsch zugeordnet werden. Dafür existiert in `toolExecution.ts` kein Timeout und keine sichere Wiederherstellung.

Die alte laufende Containerinstanz zeigte beide Gesprächsformen: Nach einer zusätzlichen Benutzeräußerung wurden Tools und Folgeantworten mehrfach erfolgreich ausgelöst; die Buchungsbestätigung scheiterte später deterministisch an der Zustandslogik. Die Live-Beobachtung ist wegen des Image-Drifts kein Beweis für den HEAD, passt aber zu den statischen Risiken.

### D.2 `incomplete` und Audioende

Im vorhandenen Dialog wurde `response.done` mit Status `incomplete` beobachtet. Der genaue `status_details`-Grund wurde nicht dauerhaft korreliert gespeichert. Die UI hat zudem eine widersprüchliche Kombination gezeigt: Gespräch beendet, aber Playback weiterhin `playing`, obwohl ein echtes `output_audio_buffer.stopped` vorlag.

Die Ursache ist daher zweigeteilt: Die Diagnoseinformation wird nicht ausreichend persistiert; die UI behandelt Response-Ende und Audio-Buffer-Ende als getrennte Zustände ohne zentralen Abschlussübergang. Ob der konkrete `incomplete`-Grund Output-Limit, Unterbrechung, Transport oder Providerverhalten war, bleibt als offene Hypothese gekennzeichnet.

### D.3 Aussprache/Akzent

Der Prompt fordert Deutsch und eine standarddeutsche Aussprache. Die UI weist selbst darauf hin, dass Aussprache nur angenähert werden kann; Code kann einen menschlichen Höreindruck nicht beweisen. Im Audit wurde daher kein unbelegter Akzent-Befund aus dem Quellcode abgeleitet. Eine belastbare Ursachenanalyse braucht einen kontrollierten Hörvergleich derselben Sätze mit der verwendeten Stimme, unterschiedlichen Geschwindigkeiten und mindestens einer Alternativstimme.

### D.4 Agenteneinstellungen

Modell, Stimme, Geschwindigkeit und Konfigurationsversion werden von UI, API, Datenmodell, Prompt-Compiler und Realtime-Payload durchgereicht. Die parallele Abfrage von Runtime-Konfiguration und Client-Secret wird anschließend auf Tenant, Modell, Stimme, Geschwindigkeit und Version verglichen; das ist ein guter Schutz gegen Konfigurationsrennen.

Nicht konsistent sind jedoch Unterbrechung und Stille: Die UI bietet einen Interrupt-Schalter, während `agent_runtime.py` und `realtimeClient.ts` `interrupt_response=False` hart codieren; `interrupt()` ist wirkungslos. `silence_duration_ms` wird gespeichert, aber nicht direkt verwendet, sondern durch die Zuordnung von `turn_eagerness` ersetzt. Der aktuelle Live-Stand hatte den Schalter aus, daher ist die Abweichung im konkreten Lauf nicht aktiv geworden.

### D.5 Doppelte oder widersprüchliche Zustände

Die Eventlistener selbst werden überwiegend sauber registriert und entfernt. Das Problem ist die Mehrfachhaltung: SDK-Session, `activeResponse`, Tool-Executor, React-Status, Playback-Status, Backend-Session und ToolAudit führen jeweils Teilinformationen. `agent_tool_end`, `response.created`, `response.done` und Audioende verändern unterschiedliche Teilzustände. Bei verspäteten oder mehrfachen Events gibt es keine zentrale, sequenzielle Zustandsmaschine mit Versions-/Turn-ID-Prüfung.

### D.6 Audio-, Mikrofon- und Tool-Konflikte

Die Absicht der Mikrofon-Gating-Logik ist korrekt: Während Antwort und Tool-Fortsetzung soll nicht gesprochen werden. Wenn eine Folgeantwort aber verloren geht, bleibt der Zustand `continuation_starting` bestehen und das Mikrofon wird nicht wieder freigegeben. Playback kann parallel bereits beendet oder als beendet markiert sein. Das ist ein Deadlockrisiko, kein genereller Beweis für einen permanenten Audio-Transportfehler.

## E. Befunde nach Schweregrad

### Kritisch

#### K-01 — Keine requestgebundene Authentifizierung und Autorisierung

- Datei/Funktion: `backend/app/api/dependencies.py`, User-/Tenant-Kontext; mehrere APIs unter `backend/app/api/v1`.
- Ursache: `ACTIVE_TENANT_SLUG` und `ACTIVE_USER_EMAIL` bestimmen den Kontext statisch aus der Umgebung. Cookies, Bearer-Token oder eine andere Requestidentität werden nicht geprüft.
- Auswirkung: In der aktuellen lokalen Entwicklungsannahme ist dies ein bewusst einfacher Adapter. Bei erreichbarem Port kann ein nicht authentifizierter Aufrufer jedoch Tenant-Daten lesen, Realtime-Secrets erhalten und Buchungs-/Konfigurationspfade auslösen. Das ist für Produktion ein kritischer Blocker und bei öffentlicher Exposition ein potenzielles Cross-Tenant-/Fremdbuchungsrisiko.
- Evidenz: README bezeichnet den Adapter als lokal und fordert für Produktion einen Ersatz; Compose bindet auf `0.0.0.0`; Schutz erfolgt intern über den statisch gewählten Kontext.
- Korrekturrichtung: Authentifizierungs-Middleware bzw. Gateway, requestgebundene User- und Tenantauflösung, serverseitige Rollenprüfung, Tests für Tenant-Isolation und Secret-Ausgabe; lokale Adapterkonfiguration nur explizit im Development-Profil.
- Änderungsrisiko: hoch. Jede API, jeder Tool-Call und die OAuth-Callback-Flows müssen auf den neuen Kontext umgestellt werden. Bis dahin keine öffentliche Bereitstellung.

### Hoch

#### H-01 — Alternative Termine werden nicht in den Buchungszustand übernommen

- Datei/Funktion: `backend/app/api/v1/calendar.py`, `conversation_check_availability`, `conversation_find_alternatives`; `backend/app/services/booking_orchestrator.py`.
- Ursache: Bei nicht verfügbarem Wunschtermin wechselt der Check auf `slot_unavailable` und liefert Alternativen. `conversation_find_alternatives` gibt Slots zurück, setzt aber keinen ausgewählten Slot. `finalize_booking` akzeptiert den Zustand `slot_unavailable` nicht als buchungsbereit.
- Auswirkung: Der Agent kann freie Alternativen nennen, aber eine darauffolgende Bestätigung endet mit `confirmation_required` bzw. ohne Buchung. Der zentrale Nutzungsfall ist damit nicht deterministisch abgeschlossen.
- Evidenz: aktueller HEAD-Code; in der laufenden älteren Instanz wurden zwei Finalize-Versuche mit `confirmation_required` protokolliert. Die Live-Reproduktion ist wegen des Image-Drifts als unterstützende, nicht als HEAD-Beweis zu lesen.
- Korrekturrichtung: expliziten Übergang `alternative_selected` bzw. `select_and_recheck_slot` einführen; den ausgewählten Slot serverseitig signieren, erneut provider- und lokal prüfen und erst danach `slot_available` setzen. Bei Konflikt zurück zu Alternativen.
- Änderungsrisiko: mittel bis hoch. Zustände, Prompt, Tool-Schema, lokale Persistenz und zwölf kritische Fortsetzungspfade müssen gemeinsam angepasst werden.

#### H-02 — Tool-Fortsetzung ohne Liveness-Wächter und unsichere Zuordnung

- Datei/Funktion: `frontend/src/features/realtime/toolExecution.ts`, `attachContinuationResponse`; `frontend/src/features/realtime/realtimeClient.ts`.
- Ursache: Nach `agent_tool_end` wird auf `response.created` gewartet. Es gibt weder Timeout noch erneutes `response.create`/sichtbare Fehlerbehandlung. Die Zuordnung nimmt den jüngsten wartenden Tool-Kontext; mehrere Ergebnisse können ältere Kontexte verwaisen lassen.
- Auswirkung: Mikrofon und Session können in `continuation_starting` hängen. Folgeantworten können fehlen, einem falschen Tool zugeordnet werden oder eine laufende Runde überholen.
- Evidenz: installierter SDK-Code 0.13.5 sendet Output plus `response.create`; die App selbst erfasst `continuation_response_id` in der Datenbank nicht zuverlässig. Im DB-Aggregat waren alle jüngsten Continuation-IDs `NULL`.
- Korrekturrichtung: eine Turn-ID pro Tool-Call, explizite Sequenz `output_sent → response_requested → response_created → response_done`, Timeout mit begrenztem Recoverypfad, Abbruch bei Sessionende und Tests für verspätete/duplizierte Events.
- Änderungsrisiko: hoch. Realtime-Transport, UI-Zustände und Backend-Audit müssen auf denselben Korrelationsschlüssel umgestellt werden.

#### H-03 — Lock schützt nicht alle überlappenden Buchungen

- Datei/Funktion: `backend/app/services/calendar_booking.py`, advisory-lock- und Verfügbarkeitslogik.
- Ursache: Der Lock-Schlüssel wird aus Tenant, Start und Ende des konkreten Intervalls gebildet. Überlappende, aber unterschiedliche Intervalle erhalten unterschiedliche Locks. Die Datenbank besitzt keine Range-Exclusion-Constraint.
- Auswirkung: Zwei parallele Requests mit überlappenden Zeiten oder unterschiedlichen Dauer-/Pufferwerten können beide die Vorprüfung passieren und doppelte bzw. kollidierende Termine erzeugen.
- Evidenz: statische Lock-Berechnung und Reihenfolge der externen/lokalen Prüfung; gleiche Idempotency-Keys sind besser geschützt, beliebige Überlappungen jedoch nicht.
- Korrekturrichtung: atomare Datenbankprüfung und -sperre über Tenant/Resource/Time-Range, PostgreSQL-Exclusion-Constraint oder serialisierte Slot-Tabelle; externe Providerantwort als zusätzlicher, nicht alleiniger Schutz.
- Änderungsrisiko: hoch. Migration, bestehende Buchungen, Timezone-/DST-Semantik und Providerfehler müssen berücksichtigt werden.

#### H-04 — Laufende Docker-Instanz ist nicht der aktuelle Stand

- Datei/Funktion: Docker Compose und Containerbetrieb.
- Ursache: Laufende Container wurden vor dem aktuellen HEAD gebaut. Nach dem Audit wurden neue HEAD-Images erfolgreich erstellt, aber die Container nicht neu gestartet.
- Auswirkung: Browser- und API-Tests gegen die laufenden Ports verifizieren ältere Software. `docker compose images` konnte den verschwundenen alten Imageverweis nicht mehr vollständig auflösen.
- Evidenz: laufende Container-IDs und Erstellungszeiten lagen vor den neu erzeugten HEAD-Images; neue Backend- und Frontend-Images wurden mit späterer Erstellungszeit gebaut.
- Korrekturrichtung: vor jedem Test `docker compose build` und anschließend kontrollierter Neustart; Image-Digest/Commit im Health-/Status-Endpunkt ausweisen. Vor Produktionsbetrieb immutable Tags oder Digests verwenden.
- Änderungsrisiko: niedrig im lokalen Betrieb, mittel in Produktion. Neustart kann laufende Sessions beenden; deshalb zuerst Testfenster und Datenbankstatus prüfen.

#### H-05 — Semantische Bestätigung und exakte Whitelist widersprechen sich

- Datei/Funktion: `backend/app/services/booking_confirmation.py`; Agent-Prompt und `latestUserUtterance`-Übergabe.
- Ursache: Der Prompt erwartet sinngemäße Zustimmung, die Implementierung akzeptiert jedoch nur eine enge exakte Liste. Natürliche Varianten wie eine höfliche Zustimmung oder „bitte buchen“ können abgelehnt werden.
- Auswirkung: Gespräch bricht trotz eindeutiger menschlicher Zustimmung ab. Umgekehrt darf keine unsichere, implizite Zustimmung zur Buchung führen.
- Evidenz: Quellcode und Tests decken nur exakte Varianten ab; die Eingabe ist mindestens ein Zeichen lang, was bei fehlendem letzten User-Utterance-Text zusätzlich zu einem Validierungsfehler führen kann.
- Korrekturrichtung: serverseitige Intentklassifikation mit konservativem Bestätigungsmodell, normalisierte Varianten plus explizite Rückfrage bei Unsicherheit; Originalwortlaut und Entscheidung auditieren.
- Änderungsrisiko: mittel. Falsch-positive Buchungen sind sicherheits- und vertrauensrelevant; umfangreiche deutsche Dialogtests sind erforderlich.

### Mittel

#### M-01 — Gesprächssessions werden nicht sauber beendet

- Datei/Funktion: `backend/app/services/realtime.py`, Session-Erstellung und Cleanup.
- Ursache: Sessions werden als `active` angelegt; ein sicherer Übergang auf `ended` mit `ended_at` ist im geprüften Ablauf nicht vorhanden. Bootstrap-Aufgaben laufen fire-and-forget.
- Auswirkung: Datenbank, UI und tatsächlicher Gesprächszustand divergieren. Bei vielen Gesprächen entstehen falsche aktive Sessions und potenziell verspätete Schreibvorgänge.
- Evidenz: 20 Sessions, alle `active`, keine gesetzte `ended_at`; jüngste beendete Browserläufe enthielten weiterhin `continuation_starting`.
- Korrekturrichtung: idempotenten `close_session`-Pfad auf Client- und Server-Seite, Abbruchmarke für asynchrone Aufgaben, Timeout-/TTL-Cleanup und Reconciliation-Report.
- Änderungsrisiko: mittel. Bestehende Diagnose- und Retentionabfragen müssen den neuen Status berücksichtigen.

#### M-02 — Observability kann den realen Ablauf nicht zuverlässig rekonstruieren

- Datei/Funktion: `backend/app/services/tool_audit.py`, `useRealtimeVoice.ts`, Event-/Tool-Widgets.
- Ursache: `result_sent_at` und `continuation_triggered_at` werden praktisch am selben Backend-Zeitpunkt gesetzt; `continuation_response_id` wird nicht nachgetragen. Im Browser verdrängt die 40-Event-FIFO durch Audio-/Transkript-Deltas die Toolereignisse.
- Auswirkung: UI zeigte null Tool-Aufrufe bei fünf Backend-Aufrufen. Ein `incomplete`-Response kann nicht sicher mit Session, Tool, Folgeantwort und Audioende korreliert werden.
- Evidenz: DB-Aggregat mit 14 Toolausführungen und durchgehend fehlender Continuation-ID; vorhandener Dialog mit widersprüchlicher UI-/Backend-Sicht.
- Korrekturrichtung: serverseitige Event-Tabelle oder strukturierter Session-Timeline-Stream mit Session-/Turn-/Response-/Tool-IDs; Browser nur als Darstellung. Transkript- und Audio-Deltas separat aggregieren.
- Änderungsrisiko: mittel. Mehr Daten und Retentionregeln erhöhen Betriebsaufwand und Datenschutzanforderungen.

#### M-03 — Playback-, Response- und Sessionabschluss können widersprechen

- Datei/Funktion: `frontend/src/features/realtime/playback.ts`, `realtimeClient.ts`.
- Ursache: `response.done`, `output_audio_buffer.stopped`, `end()` und Fehlerstatus aktualisieren nicht einen gemeinsamen Abschlusszustand. Ein unvollständiger Response kann nach echtem Audioende weiter als `playing` erscheinen.
- Auswirkung: Falsche Anzeige, gesperrtes Mikrofon, unklare Nutzeraktion und erschwerte Fehlersuche.
- Evidenz: vorhandener Lauf zeigte gleichzeitig „Gespräch beendet“ und „Wiedergabe läuft“; Code behandelt Audio- und Responseabschluss separat.
- Korrekturrichtung: zentraler Zustandsautomat mit Abschlusspriorität, eindeutigen Übergängen und Property-/Sequenztests für normales Audioende, künstliches Ende, Interrupt, `incomplete` und Session-Cleanup.
- Änderungsrisiko: mittel. UI-Verhalten ändert sich sichtbar; Accessibility- und Regressionstests nötig.

#### M-04 — Unterbrechungs- und Stilleinstellungen sind nicht wirksam

- Datei/Funktion: `frontend/src/pages/AgentSettingsPage.tsx`, `backend/app/services/agent_runtime.py`, `realtimeClient.ts`.
- Ursache: Interrupt ist im Client hart deaktiviert; gespeicherte `silence_duration_ms` wird nicht direkt in den Runtime-Payload übernommen.
- Auswirkung: Konfigurationsänderungen erzeugen keinen erwarteten Laufzeiteffekt. Das ist besonders problematisch, wenn Betreiber das Verhalten anhand der UI beurteilen.
- Evidenz: UI-Schalter und Runtime-Hardcodierung; der aktuell geprüfte Wert war ausgeschaltet, wodurch kein aktiver Widerspruch im konkreten Lauf entstand.
- Korrekturrichtung: jede Einstellung durch einen Contract-Test vom Persistenzmodell bis zum Realtime-Payload verfolgen; nicht unterstützte Optionen ausblenden oder implementieren.
- Änderungsrisiko: mittel. VAD-/Interrupt-Änderungen beeinflussen Gesprächsqualität und Tool-Unterbrechungen.

#### M-05 — Reconciliation ist vorgesehen, aber nicht betrieben

- Datei/Funktion: `backend/app/services/calendar_booking.py`, Feld `needs_reconciliation`.
- Ursache: Nach externer Erstellung und anschließendem lokalem Fehler wird ein Reconciliation-Marker gesetzt. Ein Worker, Admin-Workflow oder automatischer Abgleich ist nicht vorhanden.
- Auswirkung: Ein externer Termin kann ohne lokale Buchung bleiben; bei späterer Wiederholung drohen Doppeltermine oder manuelle Suche.
- Evidenz: Marker und Statusmodell vorhanden, aber kein aktiver Verarbeitungsweg. Aktueller Aggregatewert war null; das beweist nur, dass der Zustand im geprüften Datenbestand nicht vorlag.
- Korrekturrichtung: durable Outbox/Inbox oder Reconciliation-Job mit Provider-ID, Idempotency-Key, Retry-Limit, Dead-Letter-Zustand und sicherer Adminansicht.
- Änderungsrisiko: hoch. Externe Kalenderzustände sind nicht vollständig transaktional mit PostgreSQL.

#### M-06 — Fähigkeiten der Agentenansicht widersprechen dem Testdialog

- Datei/Funktion: `AgentSettingsPage.tsx`, Agent-Katalog und `/capabilities`/Tool-Konfiguration.
- Ursache: Eine UI-Ansicht liefert hart codiert keine Fähigkeiten, während der Testdialog fünf aktive Tools zeigt. Zusätzlich beschreibt „Aufgaben und Grenzen“ keine Aktionen/Buchung.
- Auswirkung: Betreiber können nicht zuverlässig erkennen, was der Agent tatsächlich tun darf. Das erhöht Fehlkonfiguration und Testfehler.
- Korrekturrichtung: eine versionierte Capability-Quelle aus Runtime-Konfiguration; dieselbe Quelle für Anzeige, Prompt, Tool-Whitelist und Testdialog verwenden.
- Änderungsrisiko: niedrig bis mittel. Vor allem API-/UI-Vertrag und Snapshot-Tests betroffen.

#### M-07 — Transitive npm-Sicherheitswarnung

- Datei/Funktion: `frontend/package-lock.json`, transitive Kette über `@mcp`/`@openai/agents-core` und `@hono/node-server`.
- Ursache: `npm audit` meldet sechs moderate Findings für eine Windows-Pfadtraversal-Schwachstelle in der transitive verwendeten Node-Serverkomponente. npm schlägt als automatische Lösung einen inkompatiblen Downgrade des Agents-Pakets vor.
- Auswirkung: Kein nachgewiesener Exploit im Browser-Produktionspfad; der betroffene Node-Adapter wird dort nicht als statischer Server verwendet. Die Warnung bleibt dennoch ungeklärt und muss vor einem Update-/Servereinsatz bewertet werden.
- Evidenz: lockfilebasierter `npm audit`; direkte Abhängigkeiten waren installiert und konsistent.
- Korrekturrichtung: Upstream-Fix bzw. kompatible Agents-Version abwarten/prüfen, transitive Kette nachverfolgen und audit im CI dokumentieren. Nicht blind den vorgeschlagenen Downgrade übernehmen.
- Änderungsrisiko: mittel bis hoch, weil Agents-Version und Realtime-Verhalten gekoppelt sind.

### Niedrig

#### L-01 — Ruff-Importordnung ist nicht sauber

- Datei/Funktion: `backend/alembic/versions/0005_conversation_orchestration.py` und `0006_snapshot_bootstrap_metadata.py`.
- Ursache: Ruff meldet zwei `I001`-Importsortierungsfehler.
- Auswirkung: Keine erkannte Laufzeitstörung, aber Qualitäts-Gate kann fehlschlagen.
- Evidenz: Ruff-Lauf mit genau zwei Findings; Pytest und `pip check` bestanden.
- Korrekturrichtung: Imports formatieren/sortieren und Ruff als verpflichtenden CI-Schritt ausführen.
- Änderungsrisiko: niedrig.

#### L-02 — Frontend-Bundle überschreitet Vite-Hinweisgröße

- Datei/Funktion: Build-Ausgabe, Frontend-Bundle.
- Ursache: Hauptbundle ungefähr 941 kB, gzip ungefähr 278 kB; Vite meldet die Standardgrenze von 500 kB.
- Auswirkung: Längere Erstladung und schlechtere mobile Sprachtest-Erfahrung möglich.
- Evidenz: TypeScript-/Vite-Build erfolgreich mit 368 Modulen und Größenwarnung.
- Korrekturrichtung: Route-/Feature-Splitting, lazy loading der Einstellungsseiten, Bundle-Analyse und getrennte Realtime-Ladepfade.
- Änderungsrisiko: niedrig bis mittel.

#### L-03 — Veraltete direkte Abhängigkeiten und TestClient-Warnung

- Datei/Funktion: `frontend/package.json`/Lockfile; Backend-TestClient-Nutzung.
- Ursache: Mehrere direkte Pakete haben neuere Major-/Minor-Versionen; Pytest meldet die veraltete Starlette-/httpx-TestClient-Nutzung.
- Auswirkung: Kein aktueller Testfehler, aber künftige Updates können größere Brüche verursachen.
- Evidenz: `npm outdated`; Pytest mit einer Deprecation-Warnung; `pip check` ohne kaputte Anforderungen.
- Korrekturrichtung: Updates separat planen, Changelogs prüfen und TestClient-Aufruf an die installierte Frameworkversion anpassen. Keine automatische Aktualisierung im Audit.
- Änderungsrisiko: mittel bei Framework-/Agents-Major-Upgrades.

## F. Technische Schulden und Sicherheitsbefunde

### Technische Schulden

- `calendar.py` bündelt viele API-, Provider- und Gesprächsoperationen; die Datei ist mit über 800 Zeilen ein hoher Änderungsrisikopunkt.
- `realtimeClient.ts` verbindet Transport, Eventnormalisierung, Toolfortsetzung, Audio und UI-Zustände. Eine Aufteilung in Transportadapter, Turn-Koordinator und View-Model würde Race-Analysen erleichtern.
- Tool-Schemas existieren sowohl im Backend als auch als Frontend-Zod-Definitionen. Ohne generierte gemeinsame Quelle drohen Drift bei Argumenten und Ergebnissen.
- Statuswerte sind in mehreren Bereichen als Strings modelliert. Für zentrale Zustände fehlen zum Teil Datenbank-Checks und formal dokumentierte Übergangstabellen.
- Es gibt keine dauerhafte, korrelierte Realtime-Timeline. Logs enthalten absichtlich nicht alle Gesprächsinhalte, was datenschutzfreundlich ist, aber mit strukturierten, redigierten IDs ergänzt werden sollte.
- Alembic-Migrationen sind linear bis 0006 und die Datenbank war am Head. Lösch-/Cascade-Politik und Reconciliation sind nicht durchgängig als Lebenszyklusmodell umgesetzt.

### Positive Sicherheitsbefunde

- Tenant-Filter sind in den geprüften Repositories breit verwendet.
- OAuth-State wird nicht im Klartext verglichen, PKCE ist vorhanden, Einmalverwendung wird berücksichtigt.
- OAuth-Tokens werden mit Fernet verschlüsselt gespeichert.
- Realtime-Tools validieren Tenant, Service, Zeiten, Zeitzonen und signierte Slots serverseitig.
- Prompt-Compiler enthält Plattformregeln gegen direkte Prompt-Manipulation; das ist eine Schutzschicht, ersetzt aber keine serverseitige Autorisierung.
- Standardlogs speichern nicht pauschal rohe Realtime-Ereignisse oder vollständige Transkripte.

### Sicherheitsrisiken

Der zentrale Sicherheitsbefund bleibt die fehlende echte Authentifizierungsgrenze (`K-01`). CORS, Tenant-Filter und Promptregeln sind keine Ersatzmaßnahmen für die Identität des Requests. Kalenderereignisse enthalten absichtlich kundenbezogene Daten und müssen deshalb mit klarer Retention, Zugriffskontrolle und redigierten Logs betrieben werden. Eine aktivierte Datenbank-RLS-Schicht wurde nicht gefunden; Tenant-Isolation beruht auf Anwendungscode und Tests.

## G. Nachgewiesene Testlücken

Die vorhandenen Testzahlen sind belastbar für die implementierten Fälle, aber nicht für den gesamten Gesprächsautomat. Es wurde keine Coverage-Instrumentierung installiert und daher keine unbelegte Prozentzahl berechnet.

| Kritischer Pfad | Status im Audit | Benötigter Test |
|---|---|---|
| zwölf Tool-Fortsetzungspfade | nicht vollständig abgebildet | parametrische Sequenztests pro Tool plus `response.created/done` |
| verlorener `response.created` | nicht abgedeckt | Timeout, Recovery und Nutzerfeedback |
| doppelte Tool-Calls | nicht abgedeckt | deduplizierte Tool-/Idempotency-Tests |
| mehrere Tools in einer Antwort | nicht abgedeckt | unabhängige Turn-/Tool-ID-Zuordnung |
| echtes vs. künstliches Audioende | nicht vollständig abgedeckt | Zustandsmatrix mit Playback und Sessionabschluss |
| `incomplete` | Symptom beobachtet, Ursache offen | status_details-Korrelation und Wiederanlauf |
| Alternative Auswahl | nicht als vollständiger E2E-Pfad | Auswahl, signierter Slot, Recheck, Bestätigung, genau eine Buchung |
| Slotkonflikt unter Parallelität | nicht abgedeckt | konkurrierende Requests überlappender Intervalle |
| Provider-Timeout/Teilfehler | nicht vollständig abgedeckt | Retry, Reconciliation, Dead-Letter |
| Session-Cleanup | Datenbankbefund zeigt Lücke | Clientschluss, Transportfehler, Server-TTL, verspätetes Event |
| Tenant-Isolation | intern teilweise getestet | requestgebundene Auth, fremde IDs, Secret-Ausgabe |
| Konfigurationsversionierung | Basisschutz vorhanden | Änderung zwischen Runtime-Abfrage und Secret/Sessionstart |

## H. Verifikationsergebnisse

### Automatische Prüfungen

| Prüfung | Ergebnis |
|---|---|
| Backend Pytest | 112 bestanden, 1 Warnung, ca. 3,25 s |
| Frontend Vitest | 68 bestanden in 7 Dateien |
| ESLint | sauber |
| TypeScript + Vite Build | erfolgreich; 368 Module; Bundle-Hinweis wegen Größe |
| Ruff | fehlgeschlagen: 2 Importordnungsfehler (`I001`) |
| `pip check` | keine defekten Anforderungen |
| npm Dependency Tree | direkt installierte Abhängigkeiten konsistent |
| npm audit | 6 moderate transitive Findings; keine high/critical Meldung |
| Docker Backend Build | erfolgreich auf aktuellem HEAD |
| Docker Frontend Build | erfolgreich auf aktuellem HEAD |
| Alembic | Datenbank am Revision-Head 0006 |

### Read-only-Laufzeitprüfung

Geprüft wurden Health, Plattform, Runtime-Konfiguration, Promptvorschau, Providerstatus, Kalenderverbindung und lesende Agenda-/FreeBusy-Pfade. Ein Google-FreeBusy-Lesezugriff war erfolgreich. Es wurden kein Finalize-Endpunkt ausgelöst, kein Kalenderereignis erstellt, kein Disconnect, keine OAuth-Neuanmeldung und keine Konfigurationsänderung durchgeführt.

Der bestehende Browser-Testdialog zeigte Modell `gpt-realtime-2.1`, Stimme `marin`, Konfigurationsversion 11 und fünf aktive Tools. Die Beobachtung stammt aus der laufenden älteren Containerinstanz. Der sichtbare Dialog enthielt 33 Nachrichten; am Ende lag ein `response.done` mit `incomplete` vor. Die Darstellung zeigte danach gleichzeitig Gesprächsende und laufende Wiedergabe. Backendseitig waren fünf Toolaufrufe sichtbar, die Browseranzeige zeigte wegen der FIFO-Verdrängung null.

Aggregierte Datenbankprüfung:

- 20 Call-Sessions, alle Status `active`, keine gesetzte `ended_at`.
- 14 Toolausführungen.
- 4 lokale Kalenderbuchungen.
- 0 Datensätze mit `needs_reconciliation`.
- Jüngste Toolfortsetzungen ohne gespeicherte `continuation_response_id`.

Die Zahlen enthalten keine Personen- oder Kalenderdetails. Menschliche Akzentbewertung wurde nicht aus Code oder Transkript abgeleitet.

## I. Priorisierter Refactoringplan in sieben Phasen

| Phase | Inhalt | Abhängigkeiten | Risiko | Messbare Akzeptanzkriterien |
|---:|---|---|---|---|
| 1 | Buchungszustand explizit machen | keine; zuerst beginnen | hoch | Alternative Auswahl führt reproduzierbar über Recheck zu genau einer Buchung; Konflikt bleibt ohne Buchung; alle E2E-Pfade grün |
| 2 | Realtime-Turn-Koordinator | Phase 1-Vertrag für Booking-Status | hoch | Jede Tool-ID hat genau einen Output und höchstens eine Folgeantwort; Timeout setzt Session nicht dauerhaft fest; verspätete Events werden ignoriert oder sauber beendet |
| 3 | Zentralen Audio-/Sessionautomaten | Phase 2 | mittel | `ended` kann nie mit Playback `playing` verbleiben; Mikrofon wird nach Erfolg, Fehler, Timeout und echtem Audioende deterministisch freigegeben |
| 4 | Requestgebundene Auth und Tenantgrenze | API-Inventar aus Phase 1–3 | sehr hoch | Unauthentifizierte Requests erhalten 401/403; zwei Tenants können weder Daten noch Secrets noch Tools kreuzen; Rollenfälle sind automatisiert getestet |
| 5 | Buchungskonsistenz und Reconciliation | Phase 1, Datenbankmigration | hoch | Überlappende Parallelbuchungen werden atomar verhindert; externe/lokale Teilfehler landen in einem retrybaren, sichtbaren Reconciliation-Zustand |
| 6 | Observability und Betriebsvertrag | Phase 2–5 | mittel | Session-/Turn-/Response-/Tool-ID bilden eine vollständige Timeline; `incomplete` enthält redigierten Grund; Health weist Commit/Image aus |
| 7 | UI-/Dependency-/Performance-Härtung | vorherige Verträge stabil | mittel | Fähigkeiten und Einstellungen kommen aus einer Quelle; Ruff/ESLint warnungsfrei; Bundle-Warnung bewertet oder reduziert; npm-Auditentscheidung dokumentiert |

### Empfohlene Reihenfolge

Die konkrete nächste Änderung ist Phase 1: ein serverseitiger Zustand für die Auswahl und erneute Prüfung eines Alternativslots mit einem fehlenden E2E-Test als Regressionstest. Danach sollte Phase 2 folgen. Authentifizierung ist zwar der Produktionsblocker mit der höchsten Sicherheitsrelevanz, sollte aber als eigene Freigabesperre vor jeder externen Bereitstellung umgesetzt werden.

### Freigabekriterien vor produktiver Nutzung

Produktionsfreigabe erst nach requestgebundener Authentifizierung, nachgewiesener Tenant-Isolation, atomarer Überlappungssperre, vollständigem Alternativpfad, Session-Cleanup und einem beobachtbaren Realtime-Fortsetzungsautomaten. Zusätzlich muss der laufende Image-Digest nachweislich zum freigegebenen Commit passen.

## J. Quellen, Annahmen und abschließende Empfehlung

### Offizielle externe Quellen

Die folgenden Quellen wurden für versions- bzw. providerbezogene Aussagen herangezogen. Aussagen zur konkreten App-Logik sind Schlussfolgerungen aus dem lokalen Quellcode und den lokalen Laufzeitdaten.

- [OpenAI Realtime: Function-Call-Ergebnisse und Folgeantwort](https://developers.openai.com/api/docs/guides/realtime-conversations#provide-the-results-of-a-function-call-to-the-model)
- [OpenAI Realtime: Audio-Events und `response.done`](https://developers.openai.com/api/docs/guides/realtime-conversations#client-and-server-events-for-audio-in-webrtc)
- [OpenAI Realtime WebRTC und kurzlebige Client-Secrets](https://developers.openai.com/api/docs/guides/realtime-webrtc)
- [OpenAI Voice Agents und `RealtimeSession`](https://developers.openai.com/api/docs/guides/voice-agents)
- [OpenAI API-Spezifikation: Realtime Client Secrets](https://platform.openai.com/docs/api-reference/realtime-sessions/create-realtime-client-secret)
- [Google Calendar FreeBusy](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query)
- [Google Calendar Events insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert)
- [Google OAuth Web-Server-Flow](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Google OAuth-Sicherheitsbest Practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices)
- [Microsoft Graph calendarView](https://learn.microsoft.com/en-us/graph/api/calendar-list-calendarview?view=graph-rest-1.0)
- [Microsoft Graph Events erstellen](https://learn.microsoft.com/en-us/graph/api/calendar-post-events?view=graph-rest-1.0)
- [Microsoft OAuth Authorization-Code-/PKCE-Flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)

### Annahmen und Grenzen

- Der Audit ist auf den lokalen Stand und die lokal laufende Docker-Umgebung begrenzt.
- Die laufenden Container waren nicht der aktuelle HEAD. Runtime-Befunde sind deshalb ausdrücklich als ältere Laufzeitbeobachtung markiert.
- Es wurden keine Abhängigkeiten installiert oder aktualisiert; `npm audit` und vorhandene Paketmanagerinformationen wurden read-only ausgewertet.
- Keine Coverage-Prozentzahl wird behauptet, weil keine Coverage-Instrumentierung installiert war.
- Keine fremden Kalenderinhalte, Gesprächsinhalte oder personenbezogenen Werte werden berichtet.
- Die Aussage zum Akzent ist offen: Dafür ist ein kontrollierter menschlicher Hörtest notwendig.
- Providerzugriffe waren read-only; es wurde kein Termin gebucht.

### Abschließende Empfehlung

Das Projekt sollte weiterentwickelt, nicht neu gebaut werden. Die stabilen Teile sind tragfähig: Datenmodell und Migrationen bis 0006, Tenant-Filter, verschlüsselte OAuth-Verarbeitung, Providertrennung, serverseitige Toolvalidierung und versionierte Runtime-Konfiguration. Der Realtime- und Bookingkern braucht jedoch eine explizite Zustandsmaschine mit korrelierten IDs, Zeitüberschreitungen und deterministischen Übergängen. Die lokale Entwicklungsidentität muss vor jeder externen Nutzung durch echte Authentifizierung ersetzt werden.

Der unmittelbare Testschritt nach Aktualisierung der Container ist: zuerst den alternativen Terminpfad testen und vor einer Bestätigung stoppen, sofern der Test nicht ausdrücklich in einer isolierten Testdatenumgebung mit erlaubter Buchung stattfindet. Für den aktuellen Auditumfang wurden keine Buchungsänderungen ausgelöst.

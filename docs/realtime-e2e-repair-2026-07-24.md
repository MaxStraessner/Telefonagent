# Ergebnis der End-to-End-Untersuchung

Stand: 24. Juli 2026

## 1. Ausgangsfehler

Ausgangsbasis:

| Feld | Wert |
| --- | --- |
| Branch | `fix/realtime-conversation-core` |
| Commit/Rückkehrpunkt | `6b9cd80560a4cfa1ea7ea826b7687d94397e91b6` |
| Working Tree | bereits vor Beginn uncommittet; vorhandene Auth-, Setup-, Account- und Realtime-Arbeit wurde erhalten |
| Startbefehl | `docker-compose up --build -d` |
| Frontend | `http://localhost:5173` |
| Backend | `http://localhost:8001` |
| Provider | OpenAI Realtime über WebRTC |
| Modell | `gpt-realtime-2.1` |

Vor der Änderung wurde außerhalb des Repositorys ein verifizierter
Wiederherstellungspunkt angelegt. Er enthält Branch, Commit, Status, Patch,
unversionierte Dateien, SHA-256-Prüfsummen und geprüfte PostgreSQL-Dumps beider
lokalen Datenbanken. Es wurde weder committed noch gepusht oder deployt.

Der aktuelle Fehler war reproduzierbar:

1. `POST /api/v1/realtime/session-bootstrap` erzeugte erfolgreich eine
   tenantgebundene Sitzung und ein kurzlebiges Client-Secret.
2. OpenAI bestätigte die Secret-Ausstellung mit HTTP 200.
3. Der anschließende SDP-Request des Browser-SDK an
   `/v1/realtime/calls` scheiterte mit HTTP 400.
4. Die frühere Oberfläche reduzierte die technische Ursache auf
   `Realtime Provider Request failed`.
5. Historische und fehlgeschlagene Browserversuche blieben zuvor als aktiv
   gespeichert und erschwerten die Unterscheidung zwischen aktuellem Fehler
   und Altzustand.

Der belegte technische Providertext lautete:

```text
Realtime call request failed with status 400:
You must provide a model parameter, for example
wss://api.openai.com/v1/realtime?model=gpt-realtime-1.5
```

## 2. Vollständiger Prozessablauf

### Start, Aufbau und Gespräch

| Schritt | Datei/Funktion | Eingabe | Ausgabe/Status | Fehler und Diagnose |
| --- | --- | --- | --- | --- |
| Klick | `frontend/src/features/realtime/useRealtimeVoice.ts`, `start()` | Nutzeraktion | `call_attempt_id`, UI `requesting_microphone` | Doppelstart wird über `startingRef`, Client- und Cleanup-Referenz abgewehrt; `call_start_requested`, `local_session_state_checked` |
| Lokale Prüfung | `start()` | React-Refs, Browserfähigkeit, gespeicherte Attempt-ID | neuer Generation-Counter und `AbortController` | unsicherer Kontext, fehlende Browser-API, alter Reload-Attempt; konkrete Fehlercodes |
| Mikrofon | `navigator.mediaDevices.getUserMedia()` in `start()` | Audio mit Echo- und Rauschunterdrückung | `MediaStream`, UI `connecting` | Berechtigung verweigert oder Gerät fehlt; Phase `microphone` |
| Backendrequest | `frontend/src/api/client.ts`, `realtimeSessionBootstrap()` | `call_attempt_id`, Sessioncookie, CSRF | Manifest und Secret | HTTP-Status und API-Fehlercode bleiben intern erhalten |
| Endpoint | `backend/app/api/v1/router.py`, `realtime_session_bootstrap()` | Request, DB, Settings | `RealtimeSessionBootstrapResponse` | FastAPI-Validierung; tenantneutrale HTTP-Antwort |
| Auth/Tenant | `backend/app/api/dependencies.py`, `get_authenticated_session()`, `get_tenant_context()`, `get_user_context()` | Sessioncookie | aktiver Benutzer, Tenant und Mitgliedschaft | `401`/`403`; kein Development-Tenant-Fallback |
| Startzustand | `backend/app/services/realtime.py`, `_create_starting_attempt()` | Tenant und Attempt-ID | DB-Status `starting` | Unique-Constraint verhindert doppelte Attempt-ID; `409` |
| Altzustände | `abandon_stale_attempts()` | Tenant, Maximaldauer | alte nichtterminale Attempts werden `abandoned` | `stale_realtime_attempts_abandoned` |
| KI-Einstellungen | `backend/app/services/agent_runtime.py`, `build_runtime_config()` | Tenant, DB, Settings | Agent-Konfigurationsbundle | ungültige oder fehlende Tenantkonfiguration |
| Prompt/Manifest | `build_runtime_config()` und `_record_runtime()` | Prompt, Stimme, VAD, Modell | Digest, Manifest-Snapshot, `sdk_pending` | keine Gesprächsinhalte oder Secrets im Snapshot |
| Tools | `build_runtime_config()` | tenantgebundene Fähigkeiten | Toolnamen und JSON-Schemas im Manifest | ungültige Schemas stoppen den Bootstrap |
| Provider-Token | `backend/app/services/realtime.py`, `_request_client_secret()` | Backend-Key, HMAC-Safety-ID, Attempt-ID | 60-Sekunden-Secret, Provider-Session- und Request-ID | 8 Sekunden Timeout; HTTP-, DNS-, TLS- und Responsevalidierung; `provider_request_*` |
| Providerantwort | `create_session_bootstrap()` | Secret-Grant | DB `provisioned`, Bootstrap HTTP 200 | eine späte Antwort nach Abbruch wird verworfen |
| Browserkonfiguration | `frontend/src/features/realtime/realtimeClient.ts`, `BrowserRealtimeClient.connect()` | Manifest, Secret, Stream, Audioelement | `RealtimeAgent`, `RealtimeSession`, Kalender-Tools | Secret-Ablauf und Manifest-Mismatch werden getrennt behandelt |
| Signaling | `BrowserRealtimeClient.connect()` | Ephemeral Secret, SDP, Modell | WebRTC Peer Connection | Endpoint enthält jetzt zwingend `?model=gpt-realtime-2.1`; `signaling_started/succeeded/failed` |
| Session-Ack | `handleTransportEvent()` | `session.updated` | Konfiguration gilt als angewendet | eigener Ack-Timeout; keine Begrüßung vor Bestätigung |
| Backend-Ack | `realtimeAttemptConnected()` und `mark_attempt_connected()` | Attempt-ID | DB `connected`, `connected_at` | tenantgebunden und idempotent |
| Audio/Mikrofon | `bindEvents()`, `handleTransportEvent()`, `updateMicrophone()` | SDK- und Transportereignisse | UI `connected`, Audio aktiv, Mikrofon nach Playback wieder offen | Transportverlust, Audiogerätefehler, Playback- und Responsezustand getrennt |
| Gesprächsbeginn | `transport.requestResponse()` | tenantgebundene Begrüßung | genau eine Begrüßung | normale Turns durch Server-VAD; Toolfortsetzung nur durch SDK-Sequencer |

### Exakter Providervertrag

| Teil | Vertrag |
| --- | --- |
| Client-Secret | `POST https://api.openai.com/v1/realtime/client_secrets` |
| Authentisierung | Backend-Key ausschließlich serverseitig als Bearer; pseudonyme `OpenAI-Safety-Identifier`; `X-Client-Request-Id = call_attempt_id` |
| Content-Type/Body | `application/json`; nur `expires_after` mit 60 Sekunden |
| Timeout/Retry | 8 Sekunden gesamt, 3 Sekunden Connect; kein stiller automatischer Retry |
| Secretantwort | `ek_…`, Ablauf, Provider-Session-ID; Secret wird nicht gespeichert oder geloggt |
| WebRTC-Signaling | `POST https://api.openai.com/v1/realtime/calls?model=gpt-realtime-2.1`, SDP und Ephemeral Bearer durch `@openai/agents-realtime` 0.13.5 |
| Sessionwerte | Prompt/Instructions, Modell, `marin`, Geschwindigkeit, Audioformate, Server-/Semantic-VAD, Transkription, Tools und maximale Outputtokens aus dem tenantgebundenen Manifest |
| Fehler | Phase, Code, HTTP-Status, Provider-Request-ID, Retrybarkeit und redigierte technische Nachricht |

### Ende, Abbruch und Wiederherstellung

| Ereignis | Prozesskette und terminaler Zustand |
| --- | --- |
| Normal beenden | `stop()` → `call_end_requested` → `closeResources()` → SDK/Transport/Audio/Tracks einmal schließen → `POST .../finish` → DB `ended` → `session_cleanup_completed` → lokale Referenzen zurücksetzen |
| Während Aufbau abbrechen | Generation erhöhen und `AbortController.abort()` → späte Antwort ignorieren → lokale Ressourcen schließen → DB `cancelled`; ein Finish vor dem Bootstrap erzeugt einen terminalen Tombstone |
| Providerfehler | strukturierter `RealtimeClientError` → `fail()` → UI-sichere Nachricht → DB `failed` mit Phase/Code → deterministisches Cleanup |
| Verbindung verlieren | SDK `connection_change=disconnected` → `realtime_connection_lost` → gemeinsamer Fehler-/Cleanup-Pfad → DB `failed`/`abandoned` |
| Browser neu laden | `pagehide`/Unmount → Keepalive-Finish `abandoned`; Attempt-ID liegt ausschließlich in `sessionStorage`; beim nächsten Start wird ein verbliebener Attempt nochmals idempotent abgeschlossen |
| Frontend schließen | derselbe `pagehide`- und Cleanup-Pfad; kein Secret in Storage |
| Backend neu starten | die bestehende WebRTC-Verbindung Browser↔Provider bleibt technisch nutzbar; nach Backendgesundheit ist das Finish idempotent möglich; alte nichtterminale DB-Zustände werden spätestens bei neuem Bootstrap reconciled |
| Erneut starten | wartet auf ein laufendes Cleanup, verwendet eine neue Attempt-ID und Generation; alte asynchrone Antworten können die neue Generation nicht überschreiben |

Der kanonische persistierte Lifecycle ist:

```text
starting → provisioned → connected → ended
                             ├──────→ cancelled
                             ├──────→ failed
                             └──────→ abandoned
```

React hält nur den aktuellen UI- und Ressourcenstatus. PostgreSQL hält den
terminalen Auditstatus. Der Provider besitzt die Medienverbindung. Eine Sitzung
gilt erst nach `session.updated` und dem Backend-Connected-Ack als verbunden.

## 3. Nachgewiesene Hauptursache

### Primärursache

`@openai/agents-realtime` 0.13.5 übernahm zwar das Modell in die
Sessionkonfiguration, ergänzte den Modellparameter aber nicht automatisch am
standardmäßigen WebRTC-Call-Endpunkt. Die Anwendung übergab keine eigene URL.
Dadurch ging der reale SDP-Request an:

```text
https://api.openai.com/v1/realtime/calls
```

OpenAI verlangte für diesen Request jedoch einen Modellparameter und antwortete
mit HTTP 400. Der erfolgreiche Client-Secret-Request davor bewies, dass Key,
Authentisierung, Tenantauflösung und Secret-Erzeugung nicht die Ursache waren.

Nach Übergabe von:

```text
https://api.openai.com/v1/realtime/calls?model=gpt-realtime-2.1
```

lief derselbe reale Ablauf bis `session.updated`, `call_connected`, Begrüßung,
Playback-Ende und Cleanup erfolgreich durch.

### Technischer Beleg

- Vor Fix: Client-Secret HTTP 200, danach Signaling HTTP 400 mit der eindeutigen
  Providerforderung nach `model`.
- Nach Fix: derselbe Provider-Key, Tenant, Manifest-Digest, Modell und dieselbe
  SDK-Version; Signaling erfolgreich.
- Der gezielte Frontendtest prüft die explizite URL.
- Der erfolgreiche Attempt `f538c16f-58ed-4276-873f-c2c52487345c` ging
  `starting → provisioned → connected → ended`.

### Warum frühere Reparaturen nicht ausreichten

Frühere Arbeiten beseitigten eine echte, aber andere Race Condition in der
Antwortsteuerung: Der damalige eigene `TurnCoordinator` konkurrierte mit dem
`ResponseCreateSequencer` des SDK. Das erklärte abgelehnte oder doppelte
`response.create`-Vorgänge, änderte aber nicht den heutigen SDP-Endpunkt.
Außerdem verbarg die pauschale Fehlermeldung den HTTP-400-Text.

### Nicht betroffene Bereiche

Kalenderbuchung, Accountsystem, Initial Setup, OAuth und fachliche
Terminlogik waren nicht Ursache des Signaling-Fehlers. Sie wurden für diesen
Fix nicht fachlich umgebaut.

## 4. Sekundärursachen und Folgefehler

1. In PostgreSQL standen 57 historische Browser-CallSessions weiterhin auf
   einem nichtterminalen Zustand. Migration `0013` markierte sie nachvollziehbar
   als `abandoned` mit `legacy_session_state_reconciled`.
2. Neben dem kanonischen Compose-Stack lief ein alter
   `telefonagent-calendar`-Stack mit alten Ports und Images. Er wurde nur
   gestoppt, nicht gelöscht; sein Volume blieb erhalten.
3. `APP_BASE_URL` zeigte im kanonischen `.env` noch auf den Legacy-Port 15173
   und wurde auf Backend-Port 8001 korrigiert.
4. Ein bereits geöffneter Browser-Tab hielt zunächst das alte Frontend-Bundle.
   Dieses sendete noch keine `call_attempt_id` und erhielt vom neuen Backend
   korrekt HTTP 422. Ein vollständiger Reload lud den passenden Build.
5. Vor der neuen Fehlerstruktur reduzierte das Frontend Signalingdetails auf
   eine allgemeine Providerfehlermeldung. Das begünstigte blinde Wiederholungen.

## 5. Geänderte Dateien

Die Realtime-Reparatur betrifft:

- `backend/app/models/entities.py`
- `backend/app/models/__init__.py`
- `backend/app/schemas/api.py`
- `backend/app/api/v1/router.py`
- `backend/app/services/realtime.py`
- `backend/app/services/agent_runtime.py`
- `backend/app/services/tool_audit.py`
- `backend/alembic/versions/0013_realtime_call_lifecycle.py`
- `backend/tests/test_realtime.py`
- `backend/tests/test_migrations.py`
- `backend/tests/integration/test_postgres_rls.py`
- `frontend/src/api/client.ts`
- `frontend/src/types/api.ts`
- `frontend/src/features/realtime/realtimeClient.ts`
- `frontend/src/features/realtime/useRealtimeVoice.ts`
- `frontend/src/features/realtime/errors.ts`
- `frontend/src/features/realtime/completionDiagnosis.ts`
- `frontend/src/features/realtime/toolExecution.ts`
- `frontend/src/features/realtime/turnDetection.ts`
- `frontend/tests/realtimeFlow.test.tsx`
- `frontend/tests/realtimeHelpers.test.ts`
- `frontend/tests/toolExecution.test.ts`
- `frontend/tests/agentsSdkToolContinuation.test.ts`
- `docs/realtime-voice.md`
- dieses Dokument

Die entfernten Dateien `turnCoordinator.ts`, `appliedConfiguration.ts` und ihre
Tests gehörten zur bereits nachgewiesenen doppelten SDK-Steuerung. Vorhandene,
fachfremde Auth-, Setup- und Accountänderungen im Working Tree wurden nicht
überschrieben.

## 6. Beschreibung des Fixes

- Der WebRTC-Signaling-Endpunkt wird aus dem validierten Manifestmodell gebaut
  und explizit an `RealtimeSession.connect()` übergeben.
- Jeder Start erhält eine UUID `call_attempt_id`, die Frontend, Backend,
  Client-Secret-Request, Signaling und Cleanup korreliert.
- `CallSession` speichert Provider- und Lifecyclediagnosen ohne Secret,
  Transkript oder personenbezogenen Gesprächsinhalt.
- Start, Connected-Ack und Finish sind tenantgebunden. Fremde IDs bleiben
  unzugänglich.
- Der Zustandsautomat besitzt persistierte terminale Zustände. Fehler,
  Abbruch, Reload und normales Ende hinterlassen keinen aktiven Versuch.
- Generation-ID, `AbortController`, Start-Guard und eine einzige
  `cleanupPromise` verhindern Doppelstart, späte Antworten und mehrfaches
  Schließen.
- SDK-Session, Transport, Listener, Audioelement und Mikrofontracks werden
  deterministisch geschlossen. Bereits vom SDK beendete Tracks werden nicht
  nochmals gestoppt.
- Fehler behalten intern Phase, Code, HTTP-Status, Provider-Request-ID,
  Retrybarkeit und redigierte technische Meldung; die UI zeigt keine
  sensitiven Providerdetails.
- Migration `0013` ergänzt Lifecyclefelder und reconciled Altzustände, ohne
  Fach- oder Kalenderdaten umzuhängen.

## 7. Automatisierte Tests

| Prüfung | Ergebnis |
| --- | --- |
| Backend-Gesamtsuite | 168 bestanden, 6 PostgreSQL-Integrationstests lokal erwartungsgemäß übersprungen |
| PostgreSQL-17-RLS-/Constraint-Suite | 6 von 6 bestanden |
| Frontend-Gesamtsuite | 91 von 91 bestanden |
| Realtime-Frontendtests | 38 von 38 bestanden |
| Ruff | bestanden |
| ESLint ohne Warnungen | bestanden |
| TypeScript-/Vite-Produktionsbuild | bestanden |
| `git diff --check` | bestanden |

Abgedeckt sind erfolgreicher Erststart, 400, 401, Timeout, Signalingfehler,
Abbruch im Bootstrap, Start nach Ende und Fehler, Reload-Recovery, späte
Providerantwort, Doppelstart, genau einmaliges Cleanup, SDK-Toolfortsetzung,
Mikrofonverweigerung und mehrfaches Schließen. Alle Providerzugriffe in
automatisierten Tests sind Fakes; es entstehen keine externen Buchungen.

## 8. Manuelle Tests

| Szenario | Ergebnis Frontend/Provider | Persistierter Zustand |
| --- | --- | --- |
| A: frischer Start | vor Fix reproduzierbar HTTP 400 im SDP-Signaling; nach Fix verbunden und Begrüßung vollständig abgespielt | vor Fix `failed`, nach Fix `ended` |
| B: Ende und sofortiger Neustart | fünf aufeinanderfolgende reale Zyklen verbunden, begrüßt, ohne Alarm beendet und sofort startbereit | alle fünf `ended` |
| C: Abbruch während Aufbau | UI sofort wieder startbereit, kein Providerfehler | `cancelled`, Phase `signaling` |
| D: Browser-Reload | aktive Sitzung `abandoned`; nach Reload authentifiziert und erfolgreicher Neustart | `abandoned` mit `browser_page_hidden`, Neustart `ended` |
| E: Backend-Neustart | direkter WebRTC-Call blieb nutzbar; Finish nach Health-Recovery und neuer Call erfolgreich | beide `ended` |
| F: Providerfehler | realer HTTP-400-Fehler vollständig diagnostiziert; danach erfolgreicher Start | `failed`, danach `ended` |
| G: schneller Doppelklick | beide Klickaktionen wurden verarbeitet, aber genau ein Call entstand; verbunden und beendet | exakt ein neuer Attempt, `ended` |

Nach allen Prüfungen:

- `0` nichtterminale CallSessions,
- Kalenderbuchungen unverändert `6`,
- kanonischer Stack gesund,
- Migration erfolgreich beendet,
- kein Push und kein Deployment.

Nicht als manuell bestanden behauptet werden:

- Eine kurze Spracheingabe mit Agentenantwort: Die lokale
  Lautsprecherausgabe wurde vom physischen Mikrofon nicht zurückgeführt; im
  Transcript entstand kein Nutzerturn.
- Eine echte manuelle Mikrofonverweigerung: Das verwendete Browserprofil hatte
  die Berechtigung bereits erteilt. Der Ablehnungspfad ist automatisiert
  vollständig geprüft.

Damit ist die technische Reparatur belegt; für diese zwei geräteabhängigen
Akzeptanzpunkte ist eine kurze Nutzerinteraktion am Browser erforderlich.

## 9. Verbleibende Risiken

- Akustische Qualität, echte Spracheingabe, Barge-in und die
  Berechtigungsoberfläche sind geräte- und browserprofilabhängig.
- Der Produktionsbuild warnt über einen Haupt-Chunk von rund 966 kB. Das ist
  kein Realtime-Lifecyclefehler, sollte später über Code-Splitting optimiert
  werden.
- Der alte `telefonagent-calendar`-Stack und sein Volume sind absichtlich nur
  gestoppt. Erst nach fachlicher Bestätigung sollten sie gelöscht werden.
- Die Realtime-Zyklen verursachten echte OpenAI-Nutzung. Automatisierte Tests
  bleiben davon getrennt.
- Das Kalender-Conversation-Bootstrap führt eine Free/Busy-Leseprüfung aus,
  erzeugt aber keine Termine. Der Buchungszähler blieb unverändert.

## 10. Exakte lokale Testanleitung

1. Im Projektverzeichnis ausführen:

   ```powershell
   docker-compose up --build -d
   docker-compose ps -a
   ```

2. Prüfen, dass `database`, `backend` und `frontend` laufen und `migrate` mit
   Exitcode 0 beendet ist.
3. `http://localhost:5173/testgespraech` öffnen und anmelden.
4. „Testgespräch starten“ anklicken und Mikrofon erlauben.
5. Auf „Die Verbindung steht“ und die vollständige Begrüßung warten.
6. „Ich möchte einen Termin vereinbaren“ sagen und eine gesprochene Antwort
   abwarten. Für diesen Smoke-Test noch keine Buchung bestätigen.
7. „Gespräch beenden“ anklicken. Der Mikrofonindikator muss erlöschen und
   „Neues Testgespräch“ erscheinen.
8. Schritt 4 bis 7 fünfmal wiederholen.
9. Einmal während „Sprachverbindung wird aufgebaut“ beenden und sofort neu
   starten.
10. Einmal während einer verbundenen Sitzung die Seite vollständig neu laden
    und neu starten.
11. Für den Ablehnungstest die Mikrofonberechtigung in den
    Browsereinstellungen für `localhost:5173` zurücksetzen, neu laden,
    „Blockieren“ wählen und die verständliche Fehlermeldung prüfen.
12. Optional den Backend-Neustart prüfen:

    ```powershell
    docker-compose restart backend
    ```

13. Nichtterminale Datensätze kontrollieren:

    ```powershell
    docker-compose exec -T database psql -U telefonagent -d telefonagent -c "SELECT count(*) FROM call_sessions WHERE status NOT IN ('ended','cancelled','failed','abandoned');"
    ```

    Erwartet ist `0`.

## 11. Empfohlener Commit-Titel

```text
fix: stabilize realtime signaling and call lifecycle
```

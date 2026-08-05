# Telefonagent – Plattformbasis

Telefonagent ist eine lokal ausführbare, mehrmandantenfähige Plattform für sprachbasierte Terminassistenten. Der aktuelle Stand verbindet eine React-Oberfläche, einen echten OpenAI-Realtime-Browseragenten über WebRTC, eine versionierte FastAPI-API und PostgreSQL. Ein Friseursalon dient ausschließlich als Seed-Mandant; Plattformkern und Datenmodell sind nicht auf diese Branche festgelegt.

Die Browser-Sprachfunktion sowie tenantgebundene Google- und Microsoft-Kalenderintegrationen mit echter Verfügbarkeitsprüfung und Buchung sind implementiert. Telefonie ist weiterhin bewusst **nicht** implementiert.

## Architektur und Technologien

- Frontend: React 19, TypeScript, Vite, React Router, OpenAI Agents SDK für TypeScript, WebRTC, Vitest und Testing Library
- Backend: Python 3.13+, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, Pytest
- Datenbank: PostgreSQL 17 mit persistentem Docker-Volume
- Betrieb: Docker Compose mit Health Checks, Migrations- und Seed-Startsequenz

Der Browser spricht ausschließlich mit `/api/v1`. Das Backend ermittelt Benutzer und aktiven Mandanten aus einer serverseitigen, widerrufbaren Sitzung; der Browser kann keine `tenant_id` vorgeben. Tenantfilter und PostgreSQL Row-Level Security erhalten ausschließlich diesen validierten Kontext. Details stehen in [docs/authentication-and-tenant-isolation.md](docs/authentication-and-tenant-isolation.md).

## Voraussetzungen

Empfohlen ist Docker Desktop mit Docker Compose. Für die Entwicklung ohne Container werden Node.js 22+, npm und Python 3.13+ benötigt. Eine separat installierte PostgreSQL-Instanz ist nur bei einem Backend-Start außerhalb von Docker erforderlich.

## Schnellstart mit Docker

Eine lokale `.env` ist optional, da Compose sichere, ausschließlich lokale Entwicklungswerte vorbelegt. Für eigene Werte:

```powershell
Copy-Item .env.example .env
```

Danach:

```powershell
docker compose up --build
```

Beim Start wartet ein einmaliger Migrationsservice auf PostgreSQL und führt `alembic upgrade head` aus. Erst danach startet das Backend; Seed-Daten werden nur mit dem ausdrücklich aktivierten Entwicklungs-Bootstrap idempotent angelegt. Bestehende Volumes werden dabei nicht gelöscht.

Stoppen ohne Datenverlust:

```powershell
docker compose down
```

## Lokale Adressen

- Frontend: http://localhost:5173
- Backend: http://localhost:8000
- OpenAPI-Dokumentation: http://localhost:8000/docs
- Health Endpoint: http://localhost:8000/api/v1/health
- Realtime-Agentenkonfiguration: http://localhost:8000/api/v1/realtime/agent-config
- KI-Konfiguration: http://localhost:5173/ki-konfigurieren
- Kalenderintegration: http://localhost:5173/kalender

## Konfiguration

Alle unterstützten Werte sind in `.env.example` dokumentiert. Wichtig:

- `DATABASE_URL` steuert die SQLAlchemy-Verbindung bei direktem Backend-Start.
- `BACKEND_PORT` und `FRONTEND_PORT` ändern bei Bedarf die veröffentlichten Compose-Ports.
- `AUTH_HMAC_SECRET` pseudonymisiert Login-Buckets und muss in Produktion ein eigener, zufälliger Wert mit mindestens 32 Bytes sein.
- `SESSION_IDLE_MINUTES` und `SESSION_ABSOLUTE_HOURS` begrenzen serverseitige Sitzungen.
- `DEV_BOOTSTRAP_ENABLED` aktiviert Seed-Daten ausschließlich in der Entwicklung; ohne `DEV_BOOTSTRAP_PASSWORD` entsteht bewusst kein nutzbarer Standardlogin.
- `INITIAL_SETUP_TOKEN` aktiviert bei einer noch nicht eingerichteten Installation die einmalige Browser-Ersteinrichtung. Der Wert muss geheim bleiben und wird nach dem ersten Owner dauerhaft nicht mehr akzeptiert.
- `VITE_API_BASE_URL` ist die einzige Frontend-Konfiguration für den API-Pfad und darf keine Geheimnisse enthalten.
- `OPENAI_API_KEY` wird ausschließlich vom Backend gelesen. Ohne Schlüssel startet die Plattform normal und meldet `realtime_voice_configured: false`.
- `OPENAI_REALTIME_MODEL` steuert das Plattformmodell. Stimme, Tempo, VAD und Gesprächsverhalten werden versioniert in „KI konfigurieren“ pro Mandant gespeichert.
- `OPENAI_REALTIME_MAX_SESSION_MINUTES` begrenzt kostenträchtige Tests, standardmäßig auf 10 Minuten.
- `OPENAI_REALTIME_TRANSCRIPTION_ENABLED` aktiviert das flüchtige Live-Transkript.
- `OPENAI_REALTIME_LOG_RAW_EVENTS` bleibt standardmäßig `false`; Rohereignisse können Gesprächsinhalte enthalten.
- `OPENAI_SAFETY_IDENTIFIER_SALT` dient zur pseudonymen, installationsbezogenen Safety-ID-Bildung und sollte installationsspezifisch gesetzt werden.
- `CALENDAR_TOKEN_ENCRYPTION_KEY` verschlüsselt OAuth-Tokens authentifiziert und muss installationsspezifisch, stabil und ausschließlich serverseitig gesetzt werden.
- Google- und Microsoft-Client-Zugangsdaten sowie Redirect-URIs aktivieren den jeweiligen Provider. Ohne vollständige Zugangsdaten bleibt er sichtbar, aber deaktiviert.
- Eine echte `.env` ist per `.gitignore` ausgeschlossen.

## Entwicklung ohne vollständigen Compose-Stack

Frontend installieren, starten, bauen und testen:

```powershell
Set-Location frontend
npm install
npm run dev
npm run build
npm test
```

Backend-Umgebung vorbereiten und testen:

```powershell
Set-Location backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest
```

Für einen direkten Backend-Start muss `DATABASE_URL` auf eine erreichbare PostgreSQL-Datenbank zeigen:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.seed
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Alternativ lassen sich Migration und Seed gezielt in Compose ausführen:

```powershell
docker compose run --rm backend alembic upgrade head
docker compose run --rm backend python -m app.seed
```

## Seed-Daten

`python -m app.seed` ist nur bei `APP_ENV=development` und `DEV_BOOTSTRAP_ENABLED=true` aktiv, verwendet Upsert-ähnliche Existenzprüfungen und kann mehrfach ausgeführt werden. Er legt an:

- Mandant `Salon Haarkunst Test`, Branche `hair_salon`, Zeitzone `Europe/Berlin`
- Assistentin `Lina` mit deutscher Begrüßung
- neutralen primären Standort
- Leistungen Herrenhaarschnitt, Damenhaarschnitt sowie Waschen und Föhnen
- Mitarbeiter Anna und Ben

Die Beispieldaten enthalten keine personenbezogenen Kundendaten.

## Aktueller Funktionsumfang

- vollständig versionierte, mandantenfähige KI-Konfiguration mit serverseitigen Owner-/Admin-Rechten
- strukturierter Prompt-Compiler und zentraler Runtime-Dienst für Test und echte Realtime-Sitzung
- Identität, Begrüßungen, Stimme, Tempo, Aussprache, Stil, Gesprächsführung, Themen, Wissen und sichere Grenzen
- echte serverseitige OpenAI-Stimmprobe sowie Admin-Promptvorschau
- Übersicht mit Plattform- und Integrationsstatus sowie tenant-spezifischen Kennzahlen
- echte Browser-Sprachsitzung über `RealtimeAgent`, `RealtimeSession` und `OpenAIRealtimeWebRTC`
- kurzlebige, mandantengebundene Client-Secrets; der normale OpenAI-Key erreicht den Browser nie
- Testgesprächsseite mit Mikrofonfreigabe, hörbarer Ausgabe, Stummschaltung, automatischer und manueller Unterbrechung sowie vollständigem Cleanup
- flüchtiges Live-Transkript, begrenzte Sitzungsereignisse, VAD-Diagnose und wahrgenommene Antwortlatenzen
- gemeinsame Sprachlogik für Test- und Präsentationsmodus; technische Details bleiben im Präsentationsmodus verborgen
- responsive Terminansicht mit leerem Zustand, Desktop-Tabelle und mobilen Karten
- Ansichten für Leistungen, Mitarbeiter und Unternehmensstammdaten aus der API
- technischer Systembereich ohne Geheimnisse
- persistenter heller/dunkler Modus sowie persistenter Test-/Präsentationsmodus
- einheitliche Lade-, Skeleton-, Fehler- und Wiederholungszustände
- zentraler, abbrechbarer Frontend-Datenzugriff
- versionierte, typisierte API und OpenAPI-Schema
- UUID-basierte, zeitzonenfähige Datenbanktabellen und reproduzierbare Migration
- tenantgebundene Google- und Microsoft-OAuth-Verbindungen mit verschlüsselten Tokens und kontrolliertem Disconnect
- Auswahl mehrerer Belegungskalender und genau eines beschreibbaren Zielkalenders
- zentrale DST-sichere Verfügbarkeitsberechnung mit Geschäftszeiten, Vorlauf, Horizont, Raster und Puffern
- idempotente, unmittelbar erneut geprüfte Provider-Buchungen und kontrollierte Realtime Function Tools

## Bewusst nicht umgesetzt

Nicht vorhanden sind Telefonie/SIP, n8n, ein externer OIDC-Provider, Selbstregistrierung, Passwort-Reset per E-Mail, MFA, Zahlungen und ein automatisiertes produktives Deployment. Benutzer, Rollen und Tenant werden über serverseitige Sitzungen und aktive Mandantenmitgliedschaften aufgelöst. Die Testseite speichert weder Audio noch Transkripte; bestätigte Kalenderbuchungen werden dagegen tenantgebunden protokolliert.

## Realtime-Sprachtest

Architektur, Sicherheitsmodell, manueller Abnahmetest, typische Fehler und Kostenhinweise stehen in [docs/realtime-voice.md](docs/realtime-voice.md). Der Browser fordert das Mikrofon erst nach einem Klick an. Anschließend mintet FastAPI mit dem serverseitigen Standard-Key ein 60 Sekunden gültiges Client-Secret und der Browser verbindet sich direkt per WebRTC mit OpenAI. Der Agents-SDK setzt die eine aktive Sessionkonfiguration und sequenziert Tool-Ergebnis sowie genau eine Folgeantwort; die Anwendung startet keine konkurrierende Recovery-Antwort.

Ein ChatGPT-Abonnement umfasst die OpenAI-API-Nutzung nicht automatisch. Für echte Realtime-Tests sind ein API-Projekt mit Abrechnung und Zugriff auf das konfigurierte Modell und die Stimme erforderlich; dabei entstehen nutzungsabhängige API-Kosten.

## Kalenderintegration

Provider-Einrichtung, OAuth-Callbacks, Berechtigungen, Verschlüsselung, Buchungsregeln und Abnahmetests stehen in [docs/calendar-integrations.md](docs/calendar-integrations.md). Provider-Secrets und OAuth-Tokens bleiben vollständig serverseitig. Ohne eingerichtete OAuth-Verbindung kann der Agent keine Verfügbarkeit zusagen und keinen Termin als gebucht melden.

## Spätere Erweiterungen

Weitere Kalender- und Telefonieanbieter gehören hinter die vorhandenen Backend-Provider-Schnittstellen. Externe Webhooks müssen Tenant-Zuordnung, Signaturprüfung, Idempotenz und kontrollierte Fehlerbehandlung erhalten. Der Plattformkern darf dabei keine branchenspezifischen Annahmen übernehmen.

## Fehlerbehebung

- Port belegt: `BACKEND_PORT`, `FRONTEND_PORT` und `VITE_API_BASE_URL` in `.env` konsistent anpassen. Beispiel: Backend-Port `8001` und API-Basis `http://localhost:8001/api/v1`. Fremde Prozesse oder Container müssen dafür nicht beendet werden.
- Backend bleibt unhealthy: `docker compose logs backend database` prüfen; meist ist die Datenbank noch nicht bereit oder die URL stimmt nicht.
- Frontend erreicht API nicht: `VITE_API_BASE_URL` prüfen und das Frontend neu bauen, da Vite-Werte zur Build-Zeit eingebettet werden.
- Realtime bleibt „Nicht eingerichtet“: `OPENAI_API_KEY` nur in `.env` setzen und den Backend-Container neu erstellen; niemals als `VITE_`-Variable anlegen.
- Client-Secret wird abgelehnt: API-Projekt, Abrechnung, Modellzugriff und Stimme prüfen; Backend-Logs enthalten bewusst nicht den vollständigen Providerfehler.
- Mikrofon wird abgelehnt: Browserfreigabe sowie HTTPS beziehungsweise `localhost` als sicheren Kontext prüfen.
- Seed-Mandant fehlt: `APP_ENV=development`, `DEV_BOOTSTRAP_ENABLED=true` und optional ein starkes `DEV_BOOTSTRAP_PASSWORD` setzen, danach `python -m app.seed` ausführen.
- Kalenderprovider bleibt „Nicht konfiguriert“: vollständige Client-ID, Client-Secret und exakt passende Redirect-URI setzen und Backend neu erstellen.
- OAuth-Callback wird abgelehnt: Redirect-URI beim Provider und in `.env` bytegenau vergleichen; OAuth-State läuft nach kurzer Zeit ab und kann nur einmal verwendet werden.
- Kalendertokens können nicht gelesen werden: `CALENDAR_TOKEN_ENCRYPTION_KEY` prüfen; einen zuvor verwendeten Schlüssel nicht ohne Migration austauschen.
- Veraltetes lokales Image: `docker compose build --no-cache frontend backend` ausführen. Das persistente Datenbank-Volume bleibt dabei erhalten.

# Telefonagent – Plattformbasis

Telefonagent ist eine lokal ausführbare, mehrmandantenfähige Grundlage für zukünftige sprachbasierte Terminassistenten. Der aktuelle Stand verbindet eine eigenständige React-Oberfläche mit einer versionierten FastAPI-API und PostgreSQL. Ein Friseursalon dient ausschließlich als Seed-Mandant; Plattformkern und Datenmodell sind nicht auf diese Branche festgelegt.

Die Sprach-, Telefonie-, Kalender- und Buchungsfunktionen sind bewusst **nicht** implementiert. Die Oberfläche zeigt dafür transparente, nicht interaktive Vorbereitungszustände und baut keine externen Verbindungen auf.

## Architektur und Technologien

- Frontend: React 19, TypeScript, Vite, React Router, zentrale CSS-Variablen, Vitest und Testing Library
- Backend: Python 3.13+, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, Pytest
- Datenbank: PostgreSQL 17 mit persistentem Docker-Volume
- Betrieb: Docker Compose mit Health Checks, Migrations- und Seed-Startsequenz

Der Browser spricht ausschließlich mit `/api/v1`. Das Backend ermittelt den aktiven Mandanten über `ACTIVE_TENANT_SLUG`; der Browser kann keine `tenant_id` vorgeben. Tenant-bezogene Repositoryabfragen erhalten den serverseitig aufgelösten Tenant-Kontext. Details stehen in [docs/architecture.md](docs/architecture.md).

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

Beim Start wartet das Backend auf den PostgreSQL-Health-Check, führt `alembic upgrade head` aus und legt die Seed-Daten idempotent an. Bestehende Volumes werden dabei nicht gelöscht.

Stoppen ohne Datenverlust:

```powershell
docker compose down
```

## Lokale Adressen

- Frontend: http://localhost:5173
- Backend: http://localhost:8000
- OpenAPI-Dokumentation: http://localhost:8000/docs
- Health Endpoint: http://localhost:8000/api/v1/health

## Konfiguration

Alle unterstützten Werte sind in `.env.example` dokumentiert. Wichtig:

- `DATABASE_URL` steuert die SQLAlchemy-Verbindung bei direktem Backend-Start.
- `BACKEND_PORT` und `FRONTEND_PORT` ändern bei Bedarf die veröffentlichten Compose-Ports.
- `ACTIVE_TENANT_SLUG` wird ausschließlich serverseitig ausgewertet.
- `VITE_API_BASE_URL` ist die einzige Frontend-Konfiguration für den API-Pfad und darf keine Geheimnisse enthalten.
- `OPENAI_API_KEY` bleibt leer. Das Backend startet ohne Schlüssel und meldet `realtime_voice_configured: false`.
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

`python -m app.seed` verwendet Upsert-ähnliche Existenzprüfungen und kann mehrfach ausgeführt werden. Er legt an:

- Mandant `Salon Haarkunst Test`, Branche `hair_salon`, Zeitzone `Europe/Berlin`
- Assistentin `Lina` mit deutscher Begrüßung
- neutralen primären Standort
- Leistungen Herrenhaarschnitt, Damenhaarschnitt sowie Waschen und Föhnen
- Mitarbeiter Anna und Ben

Die Beispieldaten enthalten keine personenbezogenen Kundendaten.

## Aktueller Funktionsumfang

- Übersicht mit Plattform- und Integrationsstatus sowie tenant-spezifischen Kennzahlen
- vorbereitete Testgesprächsseite mit typisiertem Zustandsmodell, Test-/Präsentationsmodus und klarer Nicht-konfiguriert-Meldung
- responsive Terminansicht mit leerem Zustand, Desktop-Tabelle und mobilen Karten
- Ansichten für Leistungen, Mitarbeiter und Unternehmensstammdaten aus der API
- technischer Systembereich ohne Geheimnisse
- persistenter heller/dunkler Modus sowie persistenter Test-/Präsentationsmodus
- einheitliche Lade-, Skeleton-, Fehler- und Wiederholungszustände
- zentraler, abbrechbarer Frontend-Datenzugriff
- versionierte, typisierte Read-only-API und OpenAPI-Schema
- UUID-basierte, zeitzonenfähige Datenbanktabellen und reproduzierbare Migration

## Bewusst nicht umgesetzt

Nicht vorhanden sind OpenAI Realtime/Agents SDK, WebRTC, Mikrofon- oder Audiozugriff, Transkripte, Telefonie/SIP/Rufnummern, externe Kalender, Terminberechnung und -mutationen, n8n, Authentifizierung, Registrierung, Zahlungen und produktives Deployment. Insbesondere erzeugt die Testseite keine künstlichen Antworten und speichert weder Audio noch Transkripte.

## Spätere Erweiterungen

Die Realtime-Integration kann an das Zustandsmodell unter `frontend/src/features/conversation/state.ts` und an die vorhandene Testgesprächsseite angeschlossen werden. Eine kurzlebige Client-Berechtigung muss später serverseitig ausgestellt werden; ein dauerhafter API-Schlüssel darf nie in Vite-Variablen gelangen.

Telefonie- und Kalenderanbieter gehören hinter eigene Backend-Service-/Provider-Schnittstellen. Externe Webhooks müssen dann Tenant-Zuordnung, Signaturprüfung, Idempotenz und kontrollierte Fehlerbehandlung erhalten. Der Plattformkern darf dabei keine branchenspezifischen Annahmen übernehmen.

## Fehlerbehebung

- Port belegt: `BACKEND_PORT`, `FRONTEND_PORT` und `VITE_API_BASE_URL` in `.env` konsistent anpassen. Beispiel: Backend-Port `8001` und API-Basis `http://localhost:8001/api/v1`. Fremde Prozesse oder Container müssen dafür nicht beendet werden.
- Backend bleibt unhealthy: `docker compose logs backend database` prüfen; meist ist die Datenbank noch nicht bereit oder die URL stimmt nicht.
- Frontend erreicht API nicht: `VITE_API_BASE_URL` prüfen und das Frontend neu bauen, da Vite-Werte zur Build-Zeit eingebettet werden.
- Seed-Mandant fehlt: Migration ausführen, danach `python -m app.seed`; `ACTIVE_TENANT_SLUG` muss `salon-haarkunst-test` entsprechen.
- Veraltetes lokales Image: `docker compose build --no-cache frontend backend` ausführen. Das persistente Datenbank-Volume bleibt dabei erhalten.

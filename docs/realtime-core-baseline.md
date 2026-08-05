# Ausgangsstand der Realtime-Stabilisierung

Erfasst vor den Änderungen am technischen Gesprächskern.

- Neuer Arbeitsbranch: `fix/realtime-conversation-core`
- Ausgangsbranch: `feature/tenant-authentication`
- Ausgangscommit und Rückkehrpunkt: `6b9cd80560a4cfa1ea7ea826b7687d94397e91b6`
- Letzter Commit: `6b9cd80 feat: add secure tenant authentication`
- Kein Push und kein Deployment im Rahmen dieser Stabilisierung.

## Bereits vorhandene, uncommittete Änderungen

Die folgenden Änderungen stammen aus der zuvor begonnenen Ersteinrichtung und
Kontenverwaltung. Sie werden erhalten und nicht als Teil des Realtime-Problems
verworfen:

- Auth-, Setup- und Provisionierungsänderungen im Backend
- Migration `0012_initial_app_setup`
- Login-, Ersteinrichtungs- und Kontenoberfläche im Frontend
- Anpassungen an Docker Compose, Beispielen und Dokumentation

Der vollständige Dateistand ist jederzeit mit `git status --short` prüfbar.

## Startbefehle

Gesamtsystem mit Docker:

```powershell
docker compose up --build
```

Frontend ohne Docker:

```powershell
cd frontend
npm install
npm run dev
```

Backend ohne Docker:

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

## Test- und Prüfkommandos

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check app tests

cd ..\frontend
npm test
npm run lint
npm run build
```

Automatisierte Tests verwenden Provider-Fakes und dürfen keine echten
Kalendertermine, OAuth-Änderungen oder produktiven Buchungen auslösen.

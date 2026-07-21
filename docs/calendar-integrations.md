# Kalenderintegrationen

Telefonagent bindet Google Calendar und Microsoft 365 über OAuth 2.0 an. Zugangsdaten, Kalenderauswahl, Buchungsregeln, Terminarten und Buchungen sind immer an den serverseitig aufgelösten Mandanten gebunden. Der Browser kann keine `tenant_id` vorgeben und erhält niemals Provider-Tokens.

## Architektur

- `CalendarProvider` normalisiert OAuth, Token-Refresh, Kalenderlisten, Belegungszeiten und Event-Erstellung.
- Google verwendet Calendar API v3; Microsoft verwendet Microsoft Graph mit dem `common`-Mandanten oder dem konfigurierten Entra-Tenant.
- Access- und Refresh-Tokens werden mit Fernet authentifiziert verschlüsselt gespeichert. Der Schlüssel liegt ausschließlich in `CALENDAR_TOKEN_ENCRYPTION_KEY`.
- OAuth-State ist kurzlebig, einmal verwendbar und nur gehasht gespeichert. Microsoft verwendet zusätzlich PKCE.
- Verfügbarkeit wird zentral und providerunabhängig aus Geschäftszeiten, Vorlauf, Horizont, Raster, Puffern, allen gewählten Belegungskalendern und lokalen Buchungen berechnet.
- Nur genau ein ausgewählter Kalender ist Schreibziel. Schreibgeschützte Kalender können für die Belegungsprüfung genutzt werden, aber nie als Ziel.
- Ein Slot ist nur 15 Minuten gültig signiert. Vor dem Schreiben wird die Verfügbarkeit erneut geprüft.
- Buchungen sind durch einen tenantgebundenen Idempotenzschlüssel geschützt. Erfolg wird erst nach bestätigter Provider-Antwort gemeldet.

## Umgebungsvariablen

Einen Schlüssel einmalig erzeugen und dauerhaft sicher hinterlegen:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Erforderlich für jede aktivierte Kalenderintegration:

```dotenv
APP_BASE_URL=https://telefonagent.example.com
CALENDAR_TOKEN_ENCRYPTION_KEY=<fernet-key>

GOOGLE_CALENDAR_CLIENT_ID=<client-id>
GOOGLE_CALENDAR_CLIENT_SECRET=<client-secret>
GOOGLE_CALENDAR_REDIRECT_URI=https://telefonagent.example.com/api/v1/calendar/oauth/google/callback

MICROSOFT_CALENDAR_CLIENT_ID=<client-id>
MICROSOFT_CALENDAR_CLIENT_SECRET=<client-secret>
MICROSOFT_CALENDAR_REDIRECT_URI=https://telefonagent.example.com/api/v1/calendar/oauth/microsoft/callback
MICROSOFT_CALENDAR_TENANT=common
```

Fehlende Provider-Zugangsdaten verhindern den Anwendungsstart nicht. Die Oberfläche zeigt den Provider dann als nicht konfiguriert an. `CALENDAR_TOKEN_ENCRYPTION_KEY` muss gesetzt sein, bevor eine OAuth-Verbindung gespeichert oder verwendet wird. Ein späterer Schlüsselwechsel benötigt eine geplante Token-Migration; der Schlüssel darf nicht spontan ersetzt werden.

## Google Cloud einrichten

1. In Google Cloud ein Projekt wählen oder erstellen und die Google Calendar API aktivieren.
2. Den OAuth-Zustimmungsbildschirm konfigurieren. Bei einer App im Testmodus die verwendeten Konten als Testnutzer hinterlegen.
3. Einen OAuth-Client vom Typ Webanwendung erstellen.
4. Die exakt konfigurierte Redirect-URI eintragen, lokal standardmäßig:

   `http://localhost:8000/api/v1/calendar/oauth/google/callback`

5. Client-ID und Client-Secret serverseitig hinterlegen und Backend neu starten.

Für eine öffentliche Produktion können die angeforderten Calendar-Scopes eine Google-Prüfung der OAuth-App erfordern. Solange die App im Testmodus bleibt, gelten Googles Testnutzer- und Tokenbeschränkungen.

Die Anwendung fordert nur Kalenderzugriff und grundlegende OpenID-Identität an. Ein Disconnect versucht die Google-Autorisierung zu widerrufen und entfernt anschließend die lokale Verbindung beziehungsweise löscht ihre Tokens.

## Microsoft Entra ID einrichten

1. In Microsoft Entra ID eine App Registration erstellen und die unterstützten Kontotypen so wählen, dass die gewünschten Geschäfts-, Organisations- und gegebenenfalls privaten Microsoft-Konten zugelassen sind.
2. Unter Authentication eine Web-Redirect-URI eintragen, lokal standardmäßig:

   `http://localhost:8000/api/v1/calendar/oauth/microsoft/callback`

3. Unter API permissions die delegierten Microsoft-Graph-Berechtigungen `openid`, `profile`, `email`, `offline_access`, `User.Read` und `Calendars.ReadWrite` zulassen. Falls die Organisation es verlangt, Admin Consent erteilen.
4. Ein Client Secret erstellen und zusammen mit der Application (client) ID serverseitig hinterlegen.
5. `MICROSOFT_CALENDAR_TENANT=common` für mehrmandantenfähige Anmeldung verwenden oder eine konkrete Tenant-ID setzen.

Microsoft stellt für delegierte Graph-Tokens keinen anwendungsspezifischen Einzel-Token-Widerruf bereit. Disconnect entfernt deshalb die lokale Verbindung und die verschlüsselten Tokens; organisationsweite Session- oder Consent-Widerrufe erfolgen bei Bedarf im Microsoft-Konto beziehungsweise Entra-Portal.

## Bedienung

1. Als Owner oder Admin `/kalender` öffnen und Google oder Microsoft verbinden.
2. Kalender synchronisieren und mindestens einen Kalender zur Belegungsprüfung auswählen.
3. Genau einen beschreibbaren Kalender als Ziel markieren.
4. Zeitzone, Geschäftszeiten, Vorlauf, Buchungshorizont, Raster und Puffer speichern.
5. Mindestens eine aktive Terminart mit Dauer anlegen.
6. Im Testgespräch eine Terminanfrage stellen. Der Agent darf nur die kontrollierten Backend-Werkzeuge für Terminarten, Slots und Buchungen verwenden.

Eine Terminbuchung erfordert Name und mindestens einen Kontaktweg. Interne Kalendernamen, fremde Termine, Eventtitel oder Teilnehmerdaten werden dem Sprachagenten nicht offengelegt.

## Tests und Betrieb

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check app tests

Set-Location ..\frontend
npm test -- --run
npm run lint
npm run build

Set-Location ..
docker compose up --build -d
docker compose exec -T backend alembic current
```

Nach Änderungen an Provider-Zugangsdaten oder Vite-Variablen müssen die betroffenen Container neu erstellt werden. Geheimnisse gehören nur in die ignorierte `.env` oder in einen Secret Store, nie in Git, Frontend-Variablen, Logs oder Screenshots.

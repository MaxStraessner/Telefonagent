# Authentifizierung und Mandantentrennung

## Sicherheitsmodell

Telefonagent löst Benutzer und Unternehmen ausschließlich aus einer validierten,
serverseitigen Sitzung auf. Das frühere Modell über `ACTIVE_USER_EMAIL` und
`ACTIVE_TENANT_SLUG` ist entfernt. Eine gültige Sitzung setzt einen aktiven
Benutzer, einen aktiven Tenant und eine aktive Mitgliedschaft voraus.

Passwörter werden mit Argon2id (19 MiB, zwei Iterationen, Parallelität 1)
gehasht. Neue Passwörter haben 15 bis 128 Zeichen. Im Browser liegt nur ein
zufälliges 256-Bit-Sitzungstoken als HttpOnly-Cookie; die Datenbank speichert
dessen SHA-256-Hash. Sitzungen laufen standardmäßig nach 30 Minuten Inaktivität
und absolut nach zwölf Stunden ab.

Schreibende Browseranfragen benötigen einen sitzungsgebundenen CSRF-Wert im
Cookie und im Header `X-CSRF-Token`. Zusätzlich werden `Origin`/`Referer` und,
falls vorhanden, `Sec-Fetch-Site` geprüft. Anmeldungen erfordern
`X-Requested-With: Telefonagent` und eine exakt erlaubte Origin.

## Rollen

- `owner` und `admin` dürfen Tenant-Konfigurationen lesen und ändern.
- `employee` darf lesen und Browser-Testgespräche verwenden, aber keine
  Konfiguration ändern.
- `is_platform_admin` ist eine separate Plattformberechtigung und gewährt
  niemals automatisch Zugriff auf Tenant-Daten.

## Sichere Provisionierung

Es gibt keine öffentliche Registrierung. Administrationsbefehle werden im
Backend-Verzeichnis oder im Backend-Container ausgeführt:

```powershell
python -m app.cli provision-tenant --slug beispiel --name "Beispiel GmbH" `
  --industry services --timezone Europe/Berlin --username owner `
  --display-name "Erste Administration" --email owner@example.test

python -m app.cli create-platform-admin --username platform-admin `
  --display-name "Plattform Administration"

python -m app.cli set-password --username owner
python -m app.cli deactivate-user --username owner
python -m app.cli deactivate-tenant --slug beispiel
```

Passwörter werden interaktiv mit `getpass` gelesen. Für Automatisierung darf
nur der Name einer kurzlebigen Umgebungsvariable über `--password-env`
übergeben werden; ein Passwort ist nie Kommandozeilenargument.

`provision-tenant` ist idempotent. Identische Daten erzeugen keine Duplikate,
Widersprüche brechen kontrolliert ab, und ein bestehendes Passwort wird bei
erneuter Provisionierung nicht geändert.

## Einmalige Browser-Ersteinrichtung

Für eine neue Installation kann der Betreiber `INITIAL_SETUP_TOKEN` als geheimen
Wert in `.env` setzen. Solange noch kein aktiver Tenant-Owner mit Passwort
existiert, zeigt die Loginseite eine Ersteinrichtung an. Sie erstellt ein leeres
Unternehmen und dessen Owner, meldet diesen direkt an und wird danach dauerhaft
geschlossen. Der Setup-Code wird weder gespeichert noch protokolliert.

## Entwicklung und Bootstrap

Migrationen laufen in Compose automatisch vor dem Backendstart. Seed-Daten werden nur mit
`APP_ENV=development` und `DEV_BOOTSTRAP_ENABLED=true` angelegt. Ohne
`DEV_BOOTSTRAP_PASSWORD` bleibt der Seed-Benutzer absichtlich nicht
anmeldefähig. Das Passwort kann anschließend mit `set-password` gesetzt werden.

Der Entwicklungs-Tenant-Fallback ist ein expliziter Adapter für spätere lokale
Telefoniearbeiten. Er funktioniert nur mit
`APP_ENV=development` und `ALLOW_DEVELOPMENT_TENANT_FALLBACK=true`.
Produktion verweigert diese Kombination beim Start.

## PostgreSQL-RLS und Datenbankrollen

Revision `0011` prüft vorhandene Verknüpfungen vorab, ergänzt zusammengesetzte
Tenant-Fremdschlüssel und aktiviert Row-Level Security. Die Runtime setzt nach
der Sitzungsprüfung transaktionslokal `app.tenant_id`; Policies vergleichen
jede tenantgebundene Zeile damit.

In Produktion müssen getrennte Rollen verwendet werden. Compose führt Alembic
in einem einmaligen `migrate`-Service aus; der dauerhafte Backend-Container
erhält das privilegierte Migrationskennwort nicht:

- `MIGRATION_DATABASE_URL`: Eigentümer-/Migrationsrolle für Alembic, ohne
  Anwendungstraffic.
- `DATABASE_URL`: Runtime-Rolle ohne DDL-Rechte, Tabellenbesitz und
  `BYPASSRLS`.

Vor Revision `0010` oder `0011` ist ein verifiziertes `pg_dump` erforderlich.
Ein Downgrade von `0010` entfernt Passwort-Hashes und Sitzungen und darf daher
nur aus einer geprüften Sicherung erfolgen.

## Betriebswerte

Produktion benötigt HTTPS für `APP_BASE_URL` und alle `CORS_ORIGINS` sowie einen
eigenen `AUTH_HMAC_SECRET` mit mindestens 32 zufälligen Bytes. Das
Session-Cookie heißt dort `__Host-telefonagent_session`, ist `Secure`,
`HttpOnly`, `SameSite=Lax`, hat `Path=/` und keine Domain. In Entwicklung wird
derselbe Vertrag ohne `__Host-` und ohne `Secure` verwendet.

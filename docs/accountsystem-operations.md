# Accountsystem – Betrieb, Migration und Rückkehr

## Vor jeder Aktivierung

1. Wartungsfenster festlegen und Anwendungsschreibzugriffe stoppen.
2. Custom-Format-Dump mit `ops/accountsystem/backup.ps1` erstellen. Im Compose-Betrieb `-DatabaseContainer telefonagent-database-1` verwenden; alternativ steht der Host-Modus mit `-DatabaseUrl` bereit. SHA-256 und `pg_restore --list` müssen erfolgreich sein.
3. Den Dump mit `ops/accountsystem/verify-restore.ps1` ausschließlich in eine Datenbank mit dem geschützten Präfix `telefonagent_restore_` oder `telefonagent_accountsystem_` zurückspielen. Im Compose-Betrieb ebenfalls `-DatabaseContainer telefonagent-database-1` verwenden.
4. Auf der Restore-Kopie `alembic current`, Bestandszähler und Passwort-Hash-Digests erfassen; danach `alembic upgrade 0014` ausführen und erneut vergleichen.
5. Anwendung mit getrennten Rollen starten: `DATABASE_URL` verwendet die Runtime-Rolle ohne Ownership/BYPASSRLS. Nur der Migrationsprozess erhält `APP_COMPONENT=migration` und `MIGRATION_DATABASE_URL`; der Backend-Runtimeprozess erhält die privilegierte URL ausdrücklich nicht.
6. Exakt einen aktiven Plattforminhaber prüfen. Falls erforderlich, nur den reauthentifizierten Wartungsbefehl `promote-platform-owner --username …` verwenden.
7. SMTP, HTTPS, HMAC-Geheimnis, CORS und Kalender-Tokenverschlüsselung prüfen. Roh-Tokens oder Schlüssel dürfen nicht in Diagnoseausgaben erscheinen.

## Kontrollierter Rückweg

- Vor produktiver Token-/Rollennutzung kann die vorherige Anwendung gegen die unveränderte `0013`-Restore-Kopie gestartet werden.
- Nach produktiver Nutzung von Einladungen, Recovery-Tokens, Plattformrollen oder nullable Sitzungen wird kein blindes Alembic-Downgrade verwendet.
- Stattdessen Anwendung stoppen, das vor dem Wartungsfenster verifizierte Backup in eine neue Datenbank zurückspielen, Bestandszähler und Hash prüfen und erst dann die vorherige Anwendung auf diese Datenbank umschalten.
- DNS, Deployment oder Produktivdaten werden durch diese Repository-Skripte nicht automatisch verändert.

## Sicherheitsprüfungen

- Runtime-Rolle: kein Tabellenowner, kein `BYPASSRLS`.
- Plattformmodus: globale Kontrolltabellen, aber keine fachlichen Tenantzeilen ohne ausgewählten Kontext.
- `suspended`/`archived`: keine Realtime-Sitzung, kein OAuth-Start, keine Buchung und keine Konfigurationsänderung.
- Audit-Metadaten: nur Allowlist-Felder, keine Passwörter, Roh-Tokens, OAuth-Werte oder Gesprächsinhalte.
- Status-, Rollen- und Passwortänderungen: betroffene Sitzungen sofort widerrufen.

## E2E-Stub und Abhängigkeitsprüfung

- `VITE_REALTIME_E2E_STUB=true` darf ausschließlich für ein separat benanntes lokales E2E-Image mit `APP_ENV=test`, eigener Datenbank und ohne Providerzugänge verwendet werden. Der normale Builddefault ist `false`; vor Freigabe muss ein Bundle-Scan auf `E2E-Stub|e2e_stub_connected` leer sein.
- `npm audit --omit=dev --audit-level=high` meldet derzeit ausschließlich den React-Router-RSC-/Server-Action-Befund. Die Anwendung verwendet nur die statische Browserrouter-SPA und keine RSC-/SSR-/Server-Actions. Die Ausnahme ist bei jedem Upgrade erneut zu prüfen; ein automatischer `--force`-Downgrade ist untersagt.
- Frontend-Lockfile reproduzierbar mit `npm ci`, danach Tests, Lint und Produktionsbuild. Backend-Image zusätzlich mit `pip check` prüfen.

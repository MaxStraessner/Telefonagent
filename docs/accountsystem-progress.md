# Accountsystem – Implementierungsfortschritt

Stand: 2026-08-05
Branch: `fix/realtime-conversation-core`
Baseline-Commit: `e909ce0 chore: preserve tested realtime and auth baseline`

## Sicherheitsgrenzen

- Kein Push, Deployment oder Branchwechsel.
- Keine Produktionsdaten verändert.
- Keine realen E-Mails, Kalenderbuchungen oder kostenpflichtigen Realtime-Sitzungen.
- Vorhandene uncommittete Arbeit wurde vor fachlichen Accountsystem-Änderungen extern gesichert und anschließend als lokaler Baseline-Commit reproduzierbar gemacht.

## Phase 0 – Sichere Basis

Status: **abgenommen**

### Sicherungen

- Lokales PostgreSQL-Entwicklungssystem bestätigt (`APP_ENV=development`).
- Custom-Format-Dump außerhalb des Repositorys:
  `C:\Users\user\AppData\Local\Temp\telefonagent-accountsystem-phase0-20260805\telefonagent-0013.dump`
- Dump: PostgreSQL 17.10, 323 TOC-Einträge, SHA-256 `F0F7B8D06D90038CB6BBD45BD0E66F4C46AE9B06143F85C9D5AA06968E93C199`.
- Tracked-Worktree-Patch: SHA-256 `A25EDB74A60514D5EDBDEBA733968CA74D3E9C3330501A8D12E3EC7B99FAC253`.
- Untracked-Dateiarchiv: SHA-256 `D240BAB25E972C8BECFD920845DB140E4343960CDE1F085A5C744DE7F50A67D9`.

### Migration und Restore

- Originaldatenbank: Alembic `0013`.
- Restore in `telefonagent_accountsystem_restore_20260805`: erfolgreich; Alembic `0013`.
- Erhaltene Bestandszähler nach Restore: 1 Tenant, 2 Benutzer, 1 Agentenkonfiguration, 1 Kalenderverbindung.
- Leere Testdatenbank `telefonagent_accountsystem_empty_20260805` erfolgreich von `0001` bis `0013` migriert; 33 öffentliche Tabellen.
- `alembic upgrade head` auf der Restore-Kopie ist ein erfolgreicher No-op; Bestandszähler bleiben unverändert.

### Testbasis

- Zeitabhängige Kalenderintegrationstests verwendeten den inzwischen vergangenen 3. August 2026. Nur die Testzeitbasis wurde auf den jeweils nächsten Montag umgestellt.
- Backend/Python 3.13: `168 passed, 6 skipped`.
- PostgreSQL-17-RLS: `6 passed`.
- Ruff: bestanden.
- Frontend/Vitest: `91 passed`.
- ESLint: bestanden.
- TypeScript/Vite-Produktionsbuild: bestanden; bestehende Warnung wegen eines ca. 966-kB-Chunks.
- Browser-Smoke-Test auf `http://localhost:5173/`: Loginseite sichtbar, keine Browser-Konsolefehler.

### Abnahmekriterien

- [x] Keine bestehende Änderung verloren.
- [x] Reproduzierbarer lokaler Baseline-Commit einschließlich `0012/0013`.
- [x] Datenbankbackup erstellt, gehasht und mit `pg_restore --list` validiert.
- [x] Migration `0013` aus leerer Datenbank erreichbar.
- [x] Restore einer bestehenden Datenbankkopie erfolgreich und bestandserhaltend.
- [x] Backend-, Frontend-, RLS-, Lint-, Build- und Browser-Baseline dokumentiert.

## Phase 1 – Rollen, Datenmodell und Zugriffskontext

Status: **abgenommen**

### Umsetzung

- Additive Alembic-Revision `0014_account_control_plane.py` mit Rollen-/Statusmapping, normalisierter E-Mail, nullable aktivem Unternehmenskontext, Primäradmin-Kennzeichnung sowie Einladungs-, Recovery- und Audit-Kontrolltabellen.
- Plattformrollen `owner | admin`, Unternehmensrollen `company_admin | company_user` und Status `trial | active | suspended | archived` eingeführt; der alte Plattformadmin-Boolean bleibt nur für die Übergangsrevision erhalten.
- `AccessContext` und zentrale Guards für Plattformowner, Plattformadmin, Unternehmensadmin und Einzelberechtigungen eingeführt.
- Plattformlogin funktioniert ohne Tenant-Mitgliedschaft und startet ohne aktiven Unternehmenskontext. Kontextwahl/-verlassen rotiert Sitzung und CSRF-Token und erzeugt Audit-Einträge.
- `promote-platform-owner` verlangt Reauthentifizierung; ein partieller Unique-Index verhindert mehr als einen Plattformowner.
- RLS wird für Tenanttabellen erzwungen. Plattformakteure dürfen globale Kontrollinformationen lesen, sehen ohne expliziten Kontext aber weiterhin keine fachlichen Tenantzeilen.

### Migrationsentscheidung für vorhandene Mehrfach-Owner

- Der erste Upgradeversuch auf der sicheren Restore-Kopie brach korrekt ab, weil das Bestandsunternehmen zwei aktive Alt-Owner besitzt.
- Nicht heuristische Auflösung: Der bereits durch Migration `0012` in `initial_app_setup.user_id` explizit gespeicherte Ersteinrichtungsbenutzer wird Primäradmin; weitere Alt-Owner werden zu normalen `company_admin`.
- Bei mehreren Alt-Ownern ohne genau diese explizite Zuordnung oder bei aktiven Unternehmen ohne aktiven Owner bricht `0014` weiterhin mit einem Inventar ab.

### Nachweise

- Endgültiges Upgrade der frischen Restore-Kopie `0013 -> 0014`: erfolgreich.
- Bestandsdaten: weiterhin 2 Benutzer, 1 Agentenkonfiguration und 1 Kalenderverbindung; alle 8 alten Sitzungen widerrufen.
- Passwort-Hash-Digests vor/nach Migration für beide Benutzer exakt identisch.
- Rollenbackfill: 1 aktiver primärer `company_admin`, 1 weiterer aktiver `company_admin`.
- Leere PostgreSQL-Kopie: `0013 -> 0014 -> 0013 -> 0014` erfolgreich.
- Backend gesamt: `171 passed, 8 skipped`; Skips sind die separat konfigurierbaren PostgreSQL-Integrationstests.
- Gezielt Auth/Provisionierung/Tenant/Agent/Migration: `28 passed`.
- PostgreSQL 17 mit zwei Tenants und separater Runtime-Rolle: `8 passed`; Runtime-Rolle ist weder Tabellenowner noch `BYPASSRLS`.
- Ruff für App, Tests und Migration: bestanden.

### Abnahmekriterien

- [x] Höchstens ein Plattformowner durch DB-Constraint; explizite, reauthentifizierte CLI-Konfiguration vorhanden.
- [x] Alte Rollen und Statuswerte korrekt und bestandserhaltend migriert.
- [x] Plattformlogin ohne Tenant-Mitgliedschaft und ohne impliziten Tenant möglich.
- [x] Unternehmensbenutzer startet nur mit eigener aktiver Mitgliedschaft; fremde Kontextwahl wird serverseitig abgewiesen.
- [x] Kontextwechsel rotiert Sitzung/CSRF und wird auditiert.
- [x] Tenant A kann Tenant B weder lesen noch beschreiben; Plattformmodus besitzt keinen globalen Fachdaten-Bypass.

## Phase 2 – Vollständiger Auth-Lebenszyklus

Status: **abgenommen**

### Umsetzung

- Login akzeptiert normalisierten Benutzernamen oder normalisierte E-Mail; die neutrale Fehlerantwort und das persistente Throttling bleiben erhalten.
- Passwort-Recovery verwendet 256-Bit-Zufallstokens, speichert ausschließlich SHA-256-Hashes, läuft nach 30 Minuten ab, ist einmalig und widerruft bei erfolgreicher Verwendung alle Sitzungen und übrigen Recovery-Tokens.
- Einladungen verwenden dieselben Hash-/Einmalprinzipien, laufen nach 72 Stunden ab und sind für Unternehmensrollen sowie `platform_admin` vorbereitet; eine Einladung kann niemals einen Owner erzeugen.
- Ein injizierbarer Mailadapter trennt SMTP von den Tests. Fehlschlagende Zustellung widerruft das erzeugte Token; in der Abnahme wurde ausschließlich ein In-Memory-Fake verwendet.
- Vorläufige Passwörter setzen `must_change_password`. Bis zum erfolgreichen, reauthentifizierten Wechsel bleiben nur Sessionabfrage, Logout und Passwortwechsel zugänglich.
- Frontendabläufe für Passwort vergessen/zurücksetzen, Einladung und Pflichtpasswortwechsel sind implementiert; `window.prompt` wurde aus der Kontenverwaltung entfernt.
- Authantworten erhalten `Cache-Control: no-store`; CSP, Frame-, Referrer- und Content-Type-Sicherheitsheader sind aktiv. Produktion verlangt zusätzlich konfigurierte SMTP-Zustellung.

### Nachweise

- Gezielte Lifecycle-Tests: `6 passed`; enthalten neutrale Recovery, Hashspeicherung, Ablauf, Einmalverwendung, Zustellfehler, Sitzungswiderruf, Unternehmens-/Plattformeinladung und Pflichtpasswortwechsel.
- Backend gesamt: `177 passed, 8 skipped`.
- Frontend gesamt: `95 passed`; davon 4 neue Auth-Lifecycle-Tests.
- PostgreSQL 17 auf frisch migrierter Datenbank `telefonagent_auth_test`: `9 passed`; die Runtime-Rolle kann ohne den exakt passenden Einladungshash keine Einladung lesen.
- Ruff und ESLint: bestanden.
- TypeScript/Vite-Produktionsbuild: bestanden; unveränderte Chunkgrößenwarnung (ca. 977 kB).

### Abnahmekriterien

- [x] Login per Benutzername und E-Mail sowie sicherer Logout bestehen.
- [x] Pflichtwechsel sperrt alle fachlichen Endpunkte bis zur Passwortänderung.
- [x] Einladungs- und Recovery-Tokens sind gehasht, begrenzt, einmalig und widerrufbar.
- [x] Abgelaufene oder bereits verwendete Tokens sind nicht nutzbar.
- [x] Deaktivierte Benutzer und gesperrte Unternehmen erhalten keine nutzbare Fachsitzung.
- [x] Tests versenden keine reale E-Mail.

## Phase 3 – Plattform- und Unternehmensverwaltung

Status: **abgenommen**

### Umsetzung

- Zentrale Account- und Auditservices mit redigierter Metadaten-Allowlist und pseudonymisierter IP eingeführt.
- Plattform-APIs für Dashboard, Suche/Filter, atomare Firmenanlage, Status, Benutzer, Einladungen, Plattformadmins und Audit implementiert.
- Unternehmens-APIs für operative Stammdaten, Benutzer, Einladungen, Primäradmintransfer und Tenant-Audit implementiert; die alten `/auth/users`-Routen bleiben als Übergangsaliase erhalten.
- Firmenanlage unterstützt sichere Einladung oder ein temporäres Startpasswort. Bei Zustellfehler wird die gesamte Anlage zurückgerollt; Startpasswörter erzwingen den Pflichtwechsel.
- DB- und Service-Invarianten schützen den Owner, den primären Administrator und den letzten aktiven Unternehmensadministrator. Aktivierung ohne aktiven Primäradmin wird abgewiesen.
- Owner-exklusive Plattformadminänderungen verlangen das aktuelle Owner-Passwort und widerrufen Zielsitzungen.
- Neue Plattformnavigation, Dashboard, Firmenliste, zweistufiger Wizard, Firmendetail, Plattformadmin- und Auditseiten; Plattformseiten werden lazy geladen.

### Nachweise

- Backend gesamt: `181 passed, 9 skipped`; gezielte Verwaltungs-/Rollenmatrix `4 passed`.
- Frontend gesamt: `98 passed`; neue Plattform-/Wizardtests `3 passed`.
- Ruff, ESLint und TypeScript/Vite-Build bestanden; Plattformseiten werden als separate Chunks ausgegeben.
- Tests belegen atomare Rückabwicklung bei Mailfehler, Owner-Schutz, Reauthentifizierung, Primäradmintransfer, letzten Admin, sofortigen Sitzungswiderruf und geheimnisfreies Audit.

### Abnahmekriterien

- [x] Unternehmen können atomar angelegt, gesucht, gefiltert, bearbeitet, gesperrt und archiviert werden.
- [x] Plattformadmin besitzt Firmenverwaltung, aber keine Plattformadminverwaltung.
- [x] Company-Admin ist auf den eigenen Tenant begrenzt; Rollenänderungen beachten Primäradminregeln.
- [x] Owner ist über normale APIs unveränderlich.
- [x] Kritische Verwaltungsaktionen werden ohne Passwörter oder Roh-Tokens auditiert.

## Phase 4 – Kontextgebundene Fachfunktionen und professionelle UI

Status: **abgenommen**

### Umsetzung

- Agent-, Kalender-, OAuth-, Realtime-, Call- und Toolpfade verwenden ausschließlich den aktiven `AccessContext`; fremde IDs werden unabhängig von ihrer Existenz nicht aufgelöst.
- Kalender-OAuth stellt den Tenantkontext vor RLS-geschützten State-Abfragen über den eng begrenzten Resolver her. `trial` und `active` sind nutzbar, `suspended` und `archived` werden vor OAuth-, Buchungs-, Konfigurations- und Realtime-Aktionen abgewiesen.
- Kontextwechsel rotiert Sitzung und CSRF-Token, widerruft den vorherigen Zugriffspfad und erzeugt einen Audit-Eintrag. Plattformmodus besitzt keinen impliziten Tenant.
- Der sichtbare Kontextbanner bleibt in Plattform- und Unternehmenslayout bestehen. `CompanyShell` wird nach der Tenant-ID neu gemountet, sodass beim Wechsel keine alten Providerdaten im Speicher verbleiben.
- Die bestehende Unternehmensseite bearbeitet operative Kontakt-/Zeitzonendaten über `/company`; rechtlicher Name, Status und Demo-Kennzeichnung bleiben Plattformaktionen.
- Ungültige oder tenantfremde Kalender-/Gesprächsressourcen werden neutral als 404/403 behandelt und lösen keinen unkontrollierten Serverfehler aus.

### Nachweise

- Gezielte Zwei-Tenant-Kontexttests: `2 passed`; enthalten Sitzungs-/CSRF-Rotation, Zieltenantdaten, fremde Service-/Call-/Tool-IDs sowie Sperrung und sofortigen Sitzungswiderruf.
- Kalenderintegration: `30 passed`; `trial` darf OAuth verwenden, `suspended` wird auch beim Callback abgewiesen.
- Frontend-Plattform-/Kontexttests: `5 passed`; enthalten stale-freien Wechsel Tenant A → Plattform → Tenant B und direkte verbotene URLs.
- Gesamtstand nach Phase 4: Backend `184 passed, 10 skipped`, Frontend `100 passed`, Ruff/ESLint und Produktionsbuild bestanden.

### Abnahmekriterien

- [x] Aktiver Unternehmenskontext ist permanent sichtbar und auditiert.
- [x] Kontextwechsel lädt ausschließlich die Konfiguration des Zieltenants und verwirft alte Frontenddaten.
- [x] Manipulierte Firmen-, Service-, Kalender-, Call-, Tool- und Realtime-IDs liefern keine tenantfremden Daten.
- [x] Bestehende Unternehmens-, Agenten-, Kalender-, Termin- und Testseiten sind im Unternehmenslayout erhalten.
- [x] Gesperrte/archivierte Unternehmen können keine OAuth-Flows, Buchungen, Konfigurationsänderungen oder Testgespräche ausführen.

## Phase 5 – Härtung, Migration und Rolloutfreigabe

Status: **abgenommen**

### Migration, Backup und Rückkehr

- `ops/accountsystem/backup.ps1` erzeugt Custom-Format-Dump, SHA-256 und Inhaltsliste. Der neue Container-Modus funktioniert ohne freigegebenen PostgreSQL-Port; Host-Modus bleibt verfügbar.
- `ops/accountsystem/verify-restore.ps1` akzeptiert nur die Präfixe `telefonagent_restore_` und `telefonagent_accountsystem_`, unterstützt Compose-Container und prüft nach dem Restore den Alembic-Stand.
- Praktischer 0014-Backup-/Restore-Test: SHA-256 `E31E317130D6C2CAB2A70C7F0BD2AFEDAF97DD1182F97A0F05201C7E0B272BAA`; Quelle und Restore jeweils Revision 0014, 2 Tenants, 2 Benutzer, 1 Mitgliedschaft, 2 Services und identischer Passwort-Sammeldigest.
- Praktisches Upgrade des unveränderten Phase-0-Dumps `0013 -> 0014`: weiterhin 1 Tenant, 2 Benutzer, 2 Mitgliedschaften, 1 Agentenkonfiguration und 1 Kalenderverbindung. Passwort-, Agenten- und Kalender-Sammeldigests sind vor/nach Upgrade exakt identisch; genau ein aktiver Primäradmin bleibt vorhanden.
- Praktischer Rückkehrtest: Die vorherige Anwendung aus Baseline-Commit `e909ce0` startete gegen eine zweite unveränderte 0013-Restore-Kopie; `/api/v1/health` meldete `healthy` und `database: connected`. Der temporäre Container wurde danach entfernt.
- Die echte Entwicklungsdatenbank wurde nicht migriert und steht weiterhin unverändert auf 0013: 1 Tenant, 2 Benutzer, 2 Mitgliedschaften, 1 Agentenkonfiguration, 1 Kalenderverbindung, 8 Sitzungen und dieselben drei Sammeldigests wie in Phase 0.

### Sicherheits- und Betriebsnachweise

- PostgreSQL 17 / RLS: `10 passed`; zwei Tenants, kein Kontext, Tenant A/B, Plattformlogin ohne Tenant, Einladungshash, zusammengesetzte FKs und Resolver. Die Runtime-Rolle ist weder Tabellenowner noch `BYPASSRLS`.
- Produktionskonfiguration trennt `APP_COMPONENT=runtime|migration|maintenance`. Nur der Migrationsprozess darf `MIGRATION_DATABASE_URL` erhalten; die Runtime lehnt diese privilegierte URL ab. HTTPS, HMAC-Mindestlänge, SMTP, CSP, HSTS und `Cache-Control: no-store` werden geprüft.
- `pip check`: keine defekten Backend-Abhängigkeiten. Kompatible npm-Sicherheitsupdates für Hono/MCP, `fast-uri`, `ip-address`, PostCSS und `brace-expansion` sind im Lockfile fixiert; React Router ist auf 7.18.2 gepinnt.
- Verbleibende npm-Audit-Ausnahme: Ein High-Advisory betrifft ausschließlich React-Router-RSC-/Server-Action-Verarbeitung. Diese Anwendung ist eine statische Vite-SPA mit `createBrowserRouter`, besitzt keine RSC-/SSR-/Server-Action-Routen und ist von diesem Codepfad nicht erreichbar. Ein Downgrade auf 7.11 würde mehrere bereits geschlossene XSS-/Redirect-Befunde wieder öffnen; bis zu einer kumulativ fehlerfreien Version bleibt das Monitoring erforderlich.
- Der Realtime-Browsertest verwendet `VITE_REALTIME_E2E_STUB=true` nur im separat gebauten lokalen E2E-Image. Der normale Produktionsbuild nutzt den Default `false`; ein Bundle-Scan bestätigt, dass `E2E-Stub` und `e2e_stub_connected` vollständig herausoptimiert sind.
- Browser-E2E in separatem Netzwerk/Volume auf Ports 8014/5194: Owner-Ersteinrichtung, atomare Anlage von `E2E Zielunternehmen`, Pflichtpasswortwechsel, Unternehmensadmin ohne Plattformzugriff, sichtbarer Owner-Supportkontext, Zielkonfiguration v1 und gestubbter Gesprächsstart. Stubereignis: `microphone=false`, `externalProvider=false`.
- E2E-Datenbank nach dem Lauf: 2 Tenants, 2 Benutzer, kein aktives Unternehmen ohne Primäradmin, 0 Calls, 0 Termine, 0 Kalenderbuchungen, 0 OAuth-States, 3 Auditereignisse und 0 Geheimnistreffer. Container, Netzwerk, Volume und Stub-Image wurden entfernt.

### Finale Tests

- Backend gesamt: `184 passed, 10 skipped`; die 10 PostgreSQL-Fälle wurden separat mit echter PostgreSQL-17-Runtime-Rolle als `10 passed` ausgeführt.
- Frontend gesamt nach frischem `npm ci`: `100 passed` in 11 Dateien.
- Ruff, ESLint, TypeScript/Vite-Build, `docker compose config --quiet`, `pip check` und `git diff --check`: bestanden.
- Keine reale E-Mail, kein realer Kalendereintrag, keine Mikrofonfreigabe und keine externe/kostenpflichtige Realtime-Sitzung wurden ausgelöst.

## Gesamtabnahme aus Abschnitt 16

- [x] Owner, Plattformadmin, Unternehmensadmin und Unternehmensbenutzer werden durch zentrale serverseitige Guards exakt getrennt; Owner-/letzter-Admin-Schutz ist getestet.
- [x] URL-, API-, OAuth-, Call-, Tool-, Realtime- und Ressourcen-IDs ermöglichen in Zwei-Tenant-Tests keinen Fremdzugriff.
- [x] Plattformidentität bleibt sichtbar; Unternehmensdaten werden erst nach rotierter und auditierter Kontextwahl geladen, ohne Imitation.
- [x] Jedes aktive/Testphasen-Unternehmen besitzt einen aktiven Primäradmin; Datenbank- und Serviceinvarianten verhindern den Gegenfall.
- [x] Bestehende Benutzer, Passwort-Hashes, Agentenkonfiguration und Kalenderverbindung sind über Restore-/Migrationsdigests erhalten.
- [x] Einladung, Startpasswort, Recovery, Ablauf/Einmalverwendung, Sitzungsablauf und persistentes Login-Throttling sind getestet.
- [x] Deaktivierung sowie Tenant-Sperrung/-Archivierung widerrufen aktive Sitzungen und blockieren Fachpfade.
- [x] Audit deckt Verwaltungs-/Kontextaktionen ab; Allowlist und E2E-Datenprüfung zeigen keine Passwörter, Tokens oder OAuth-Geheimnisse.
- [x] Backend-, Frontend-, PostgreSQL- und kritische Browser-E2E-Szenarien bestehen mit mindestens zwei real getrennten Tenants.
- [x] Backup, Restore, 0013→0014-Migration und Wiederanlauf der vorherigen Anwendung wurden praktisch nachgewiesen.

## Verbleibende Risiken und Aktivierungsvoraussetzungen

- Der Frontend-Hauptchunk bleibt mit etwa 985 kB über der Vite-Warnschwelle; Plattformseiten sind bereits lazy geladen, weitere Zerlegung ist eine Performanceverbesserung und kein Sicherheits-/Abnahmeblocker.
- Die dokumentierte React-Router-RSC-Audit-Ausnahme muss bei jeder Abhängigkeitsaktualisierung neu bewertet und auf eine kumulativ bereinigte Version gehoben werden, sobald verfügbar.
- Vor einer echten Produktivaktivierung bleiben betriebliche Schritte: Wartungsfenster, frischer Produktivdump/Restore, getrennte reale DB-Rollen, exakt ein per Wartungsbefehl bestätigter Owner, HTTPS/SMTP/CORS/HMAC-Prüfung und Smoke-Test. Diese Sitzung hat weder Deployment noch Produktionsdaten verändert.
- Reale SMTP-, Kalenderprovider- und Realtime-Verbindungen wurden aufgrund der ausdrücklichen Sicherheitsvorgabe nicht als externer Smoke-Test verwendet; ihre Adapter-, Sperr- und Fehlerpfade sind durch Fakes/Mocks abgedeckt.

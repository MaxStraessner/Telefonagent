# Vollständiges Accountsystem mit Mandantentrennung

Dieses Dokument ist die verbindliche fachliche, technische und sicherheitsbezogene Spezifikation für die Umsetzung des Accountsystems bis einschließlich Phase 5.

## 1. Ausgangszustand

- FastAPI, PostgreSQL/Alembic, React/Vite und Docker bilden die vorhandene Plattform.
- Argon2id-Passwort-Hashes, serverseitige Sitzungen, HttpOnly-/SameSite-Cookies, CSRF-Prüfung, neutrale Loginfehler, Login-Throttling und geschützte Frontendrouten bleiben erhalten.
- `Tenant`, `AppUser`, `TenantMembership`, `UserSession` und `AuthenticationRateLimit` existieren bereits.
- Migration `0011` ergänzt zusammengesetzte Tenant-Fremdschlüssel und PostgreSQL-RLS; `0012` und `0013` gehören zur gesicherten Baseline.
- Bestehende Benutzer, Passwort-Hashes, Agentenkonfigurationen, Kalenderverbindungen, Realtime-Funktionen und Benutzeränderungen dürfen nicht verloren gehen.

## 2. Bestehende Komponenten

- Datenmodell und Migrationen: `backend/app/models/entities.py`, `backend/alembic/versions/0010_tenant_authentication.py`, `0011_tenant_integrity_and_rls.py`.
- Authentifizierung und Autorisierung: `backend/app/api/dependencies.py`, `backend/app/services/authentication.py`, `backend/app/api/v1/auth.py`.
- Provisionierung und Sicherheit: `backend/app/services/provisioning.py`, `backend/app/core/security.py`, `backend/app/services/login_throttle.py`, `backend/app/repositories/auth.py`, `backend/app/cli.py`.
- Tenant-Fachfunktionen: `backend/app/api/v1/router.py`, `agent.py`, `calendar.py`, `services/realtime.py`, `calendar_oauth.py`, `calendar_connections.py`.
- Frontend: `frontend/src/App.tsx`, `api/AuthProvider.tsx`, `api/client.ts`, `layouts/AppLayout.tsx` und bestehende Unternehmens-, Agenten-, Kalender- und Testseiten.

## 3. Zu erhaltende Sicherheits- und Fachfunktionen

- Argon2id, Passwort-Rehashing, generische Loginantworten und persistentes IP-/Benutzer-Throttling.
- Gehashte Sitzungstokens, Idle-/Absolutablauf, CSRF-Token und Sitzungswiderruf.
- Tenant- und Benutzerermittlung ausschließlich aus validierten serverseitigen Sitzungen.
- Tenant-Mitgliedschaften als Grundlage für spätere Mehrmandantenzuordnung.
- Tenant-Fremdschlüssel, RLS, tenantgefilterte Repositories und verschlüsselte Kalender-OAuth-Tokens.
- Bestehendes UI-Design und vorhandene Fachseiten; diese werden eingebettet, nicht dupliziert.
- Einmalige Ersteinrichtung, erweitert auf Plattforminhaber plus erstes Unternehmen.

## 4. Fachliches Zielmodell

- Die interne Tabelle `tenants` bleibt bestehen; UI und öffentliche API sprechen von Unternehmen.
- Es gibt genau einen `platform_owner`. Er kann nicht über normale APIs deaktiviert, gelöscht oder herabgestuft werden. Eigentümerwechsel erfolgen ausschließlich per Wartungs-CLI mit Reauthentifizierung und Auditierung.
- `platform_admin` verwaltet Unternehmen und Unternehmensbenutzer, aber nicht den Plattforminhaber oder andere Plattformadministratoren.
- Unternehmensrollen sind `company_admin` und `company_user`; `is_primary_admin` markiert den verantwortlichen ersten Administrator.
- Nach abgeschlossenem Onboarding besitzt jedes Unternehmen mindestens einen aktiven primären Administrator.
- Das Schema erlaubt mehrere Mitgliedschaften je Benutzer; V1 beschränkt normale Provisionierung auf eine aktive Unternehmensmitgliedschaft.
- Plattformadministratoren verwenden eine sichtbare, auditierte Kontextumschaltung und niemals Benutzerimitation.
- Unternehmensstatus sind `trial`, `active`, `suspended` und `archived`; `is_demo` ist davon unabhängig.

## 5. Rollen- und Berechtigungsmatrix

| Aktion | platform_owner | platform_admin | company_admin | company_user |
|---|---:|---:|---:|---:|
| Alle Unternehmen sehen und verwalten | Ja | Ja | Nein | Nein |
| Unternehmen sperren/archivieren | Ja | Ja | Nein | Nein |
| Plattformadministratoren verwalten | Ja | Nein | Nein | Nein |
| Plattforminhaber verändern | Nur Wartungs-CLI | Nein | Nein | Nein |
| Unternehmensbenutzer verwalten | Ja | Ja | Eigenes Unternehmen | Nein |
| Primären Unternehmensadmin bestimmen | Ja | Ja | Nur primärer Admin | Nein |
| Unternehmenskonfiguration bearbeiten | Aktiver Kontext | Aktiver Kontext | Eigenes Unternehmen | Nein |
| Agent/Kalender/Testbereich nutzen | Aktiver Kontext | Aktiver Kontext | Ja | Freigegebene Funktionen |
| Tenant-Audit lesen | Alle | Alle | Eigenes Unternehmen | Nein |
| Plattform-Audit lesen | Ja | Ja | Nein | Nein |
| Supportkontext wechseln | Ja | Ja | Nein | Nein |

- Der primäre `company_admin` darf weitere Administratoren ernennen oder die Primärverantwortung übertragen, aber niemals den letzten aktiven Administrator entfernen.
- Unternehmensadministratoren bearbeiten operative Stammdaten; Status, Demo-Kennzeichnung und rechtlicher Name bleiben Plattformaktionen.
- Berechtigungen werden serverseitig erzwungen; Frontendnavigation ist nur eine zusätzliche Darstellungsschicht.

## 6. Ziel-Datenmodell

Die nächste lineare Revision nach `0013` ist `0014`.

- `app_users`: nullable `platform_role` (`owner | admin`), nullable eindeutige `normalized_email`, `must_change_password`; bestehende Passwort-Hashes bleiben unverändert.
- `tenant_memberships`: `company_admin | company_user`, `is_primary_admin`, höchstens ein aktiver primärer Administrator je Tenant.
- `user_sessions`: nullable `active_tenant_id`; Plattformmodus besitzt keinen aktiven Tenant; Kontextwechsel rotiert Sitzung und CSRF-Token.
- `tenants`: `legal_name`, `contact_name`, `contact_email`, `contact_phone`, Status `trial | active | suspended | archived`, `is_demo`; Sprache bleibt kanonisch in `tenant_settings.default_language`.
- `invitations`: Tenant optional, Zielrolle, E-Mail, Token-Hash, Ablauf, Einmalverwendung, Widerruf, Ersteller und Zustellstatus.
- `password_reset_tokens`: Benutzer, Token-Hash, Ablauf, Verwendungs-/Widerrufszeitpunkt.
- `audit_logs`: Akteur, Plattformrolle, Tenant optional, Aktion, Ziel, Ergebnis, redigierte Vorher-/Nachher-Metadaten, Request-ID, pseudonymisierte IP und Zeitstempel.
- Alle künftigen fachlichen Tabellen und Dateien müssen tenantgebunden und durch tenantkonsistente Fremdschlüssel geschützt sein.

## 7. Migrationsstrategie

- Vor Schemaänderungen werden ein verifiziertes `pg_dump`, der Alembic-Stand, eine Wiederherstellungskopie und ein Bestandsinventar geprüft.
- Rollenmapping: `owner` → `company_admin` plus `is_primary_admin`; `admin` → `company_admin`; `employee/member` → `company_user`; `is_platform_admin=true` → `platform_admin`, außer dem explizit benannten Plattforminhaber.
- Der Plattforminhaber wird nie heuristisch gewählt. `promote-platform-owner --username …` wählt genau einen aktiven Benutzer; Produktion aktiviert die Plattformverwaltung nur mit exakt einem Owner.
- Statusmapping: `draft` → `trial`, `active` → `active`, `inactive` → `suspended`; keine automatische Archiv-/Demo-Erkennung.
- Neue Kontaktfelder bleiben leer; Agentenkontakte werden nicht automatisch als administrative Kontaktdaten übernommen.
- Passwort-Hashes bleiben erhalten; alte Sitzungen werden beim neuen Kontextmodell widerrufen.
- Verwaiste oder tenantfremde Datensätze führen zum kontrollierten Migrationsabbruch, nicht zu Löschung oder geratenen Zuordnungen.
- Migrationen erfolgen additiv, mit Backfill und Konsistenzprüfung; alte Kompatibilitätsfelder werden erst in einer späteren Revision entfernt.

## 8. Backend und öffentliche Schnittstellen

- Eine zentrale `AccessContext`-Abhängigkeit enthält Benutzer, Plattformrolle, Mitgliedschaft, aktiven Tenant, Sitzungsmodus und Berechtigungen.
- Guards: `require_platform_owner`, `require_platform_admin`, `require_company_admin`, `require_permission`, `require_active_company_context`.
- Auth-API: Login, Logout, Session, Passwortwechsel, neutraler Forgot-Password-Flow, 30-Minuten-Reset, 72-Stunden-Einladungen sowie Kontext wählen/verlassen.
- Plattform-API: Dashboard, Unternehmenliste/-detail/-anlage/-bearbeitung/-status, Unternehmensbenutzer, Einladungen, owner-exklusive Plattformadmins und Auditprotokoll.
- Unternehmens-API: eigene Stammdaten, Benutzer, Einladungen und Primäradmintransfer.
- `/tenant` und `/auth/users` bleiben für eine Übergangsrevision kompatible Aliase.
- Login akzeptiert normalisierten Benutzernamen oder normalisierte E-Mail.
- Ein injizierbarer SMTP-Maildienst versendet Einladungs- und Recovery-Links; Tests verwenden einen Fake-Adapter. Roh-Tokens werden nicht gespeichert oder geloggt.

## 9. Frontend

- Getrennte `PlatformLayout`- und `CompanyLayout`-Bereiche im vorhandenen Design.
- `AuthProvider` verwaltet Sitzung, Berechtigungen, Pflichtpasswortwechsel und aktiven Kontext.
- Seiten für Forgot/Reset, Einladungsannahme, Pflichtpasswortwechsel, Plattformdashboard, Unternehmen, Benutzer, Plattformadmins und Audit.
- Das allgemeine Kontenformular wird in Unternehmensbenutzer- und Plattformadmin-Workflows getrennt; Passwort-Reset per `window.prompt` entfällt.
- Navigation und direkte Routen werden rollen- und kontextabhängig geschützt.
- Kritische Aktionen erhalten Bestätigungsdialoge sowie klare Lade-, Leer-, Status- und Fehlerzustände.

## 10. Testbereich und Unternehmenskontext

- Im Plattformmodus wird kein Tenant automatisch angenommen.
- Nach Auswahl erscheint dauerhaft: „Aktiver Unternehmenskontext: …“, einschließlich Wechsel-/Verlassen-Aktion.
- Kontextwechsel prüft Rolle und Unternehmen, rotiert die Sitzung und erzeugt einen Audit-Eintrag.
- Agent-, Kalender-, Realtime- und Testendpunkte verwenden ausschließlich `AccessContext.active_tenant_id`.
- IDs aus Payloads oder Realtime-Nachrichten können den Tenant nie überschreiben; Ressourcen werden immer nach ID und Tenant aufgelöst.
- Gesperrte/archivierte Unternehmen sind administrativ sichtbar, aber Realtime, OAuth, Buchung und Konfigurationsänderungen bleiben deaktiviert.
- Benutzerimitation wird nicht eingeführt.

## 11. Sicherheitsmaßnahmen

- Produktion verlangt HTTPS, sichere HMAC-Geheimnisse, getrennte Migrations-/Runtime-Datenbankrollen und konfigurierte Mailzustellung.
- Runtime besitzt weder Tabellenownership noch `BYPASSRLS`; tenantgebundene Tabellen erzwingen RLS.
- Auch Plattformadministratoren greifen auf Fachdatentabellen nur im explizit ausgewählten Tenant zu.
- Owner-/Adminänderungen verlangen erneute Passworteingabe und Auditierung.
- Temporäre Passwörter setzen `must_change_password`; bis zum Wechsel sind nur Sitzung, Logout und Passwortwechsel erlaubt.
- Einladungs-/Reset-Tokens besitzen 256 Bit Zufall, werden nur gehasht gespeichert, laufen ab und sind einmalig.
- Status-, Rollen- und Passwortänderungen widerrufen betroffene Sitzungen sofort.
- Auditdaten entstehen über Allowlist und enthalten keine Geheimnisse oder Gesprächsinhalte.
- Authantworten verwenden `Cache-Control: no-store`; das Frontend erhält eine restriktive Content Security Policy.

## 12. Teststrategie

- Backend: vollständige Rollenmatrix, Owner-/letzter-Admin-Schutz, Login per Benutzername/E-Mail, Throttling, Pflichtwechsel, Einladung, Reset, Kontext, Audit und manipulierte IDs.
- PostgreSQL: RLS ohne Kontext, Tenant A/B, Cross-Tenant-Schreibschutz, Fremdschlüssel und Runtime-Rolle ohne Ownership/BYPASSRLS.
- Frontend/Vitest: Navigation, verbotene Direkt-URLs, Sessionablauf, Wizard, Formzustände, Kontextbanner und Datenwechsel.
- Browser-E2E: Owner legt Unternehmen plus Admin an; Unternehmensadmin bleibt im eigenen Tenant; Plattformadmin wechselt Kontext und startet ein vollständig gestubbtes Testgespräch.
- Migration: Upgrade einer `0013`-Kopie, Rollenerhalt, Passwort-Hash-Erhalt, kontrollierter Konfliktabbruch und Wiederherstellung.
- Tests senden keine realen E-Mails, erzeugen keine realen Kalendertermine und starten keine kostenpflichtigen Realtime-Sitzungen.

## 13. Risiken und Rückkehrstrategie

- Vor Beginn muss der vorhandene Arbeitsstand lokal gesichert und reproduzierbar committed sein.
- Plattformzugriff darf nie durch pauschales `BYPASSRLS` gelöst werden.
- Fehlgeschlagene Mailzustellung widerruft unbekannt gebliebene Tokens.
- Status-/Rollenänderungen unterbrechen Sitzungen als dokumentiertes Sicherheitsverhalten.
- Vor Migration existieren Datenbankbackup und Quellstand-Snapshot; Schemaänderungen sind zuerst additiv.
- Bei Fehlern wird die Anwendung auf den Baseline-Commit zurückgestellt und die Datenbank aus dem geprüften Backup wiederhergestellt; kein blindes Downgrade nach produktiver Tokennutzung.

## 14. Umsetzungsreihenfolge

1. Arbeitsstand, Migrationen und Datenbankkopie sicher reproduzierbar machen.
2. Kontrolltabellen, Rollenmapping, nullable Sitzungskontext und Auditmodell migrieren.
3. Zentrale Berechtigungslogik und RLS anpassen.
4. Login, Kontext, Einladungen, Recovery und Pflichtpasswortwechsel vervollständigen.
5. Plattform- und Unternehmens-APIs implementieren.
6. Frontend in Plattform- und Unternehmensbereich aufteilen.
7. Agent-, Kalender-, Realtime- und Testpfade härten.
8. Zwei-Tenant-, Migrations-, Browser- und Restore-Tests abschließen.
9. Kein Push oder Deployment ohne gesonderten Auftrag.

## 15. Phasen und Abnahmekriterien

### Phase 0 – Sichere Basis

- Keine bestehende Änderung verloren; reproduzierbarer lokaler Commit; geprüftes Datenbankbackup; Migration `0013` aus leerer und bestehender Datenbank erreichbar; dokumentierte grüne Testbasis.

### Phase 1 – Rollen, Datenmodell und Zugriffskontext

- Exakt ein Plattformowner konfigurierbar; alte Rollen korrekt migriert; Plattformlogin ohne Tenant; Firmenbenutzer nur in zulässigem Kontext; Tenant A kann Tenant B nicht lesen oder schreiben.

### Phase 2 – Auth-Lebenszyklus

- Login per Benutzername/E-Mail, Logout, Pflichtwechsel, Einladung und Reset bestehen; Tokens sind gehasht, begrenzt und einmalig; gesperrte Benutzer/Tenants erhalten keine nutzbare Sitzung.

### Phase 3 – Plattform- und Unternehmensverwaltung

- Unternehmen atomar mit erstem Admin anlegbar, such-/filter-/bearbeit-/sperr-/archivierbar; Rollenmatrix serverseitig; Owner per API unveränderlich; kritische Aktionen auditiert.

### Phase 4 – Kontextgebundene Fachfunktionen und UI

- Kontext permanent sichtbar; Wechsel lädt ausschließlich Zieltenantdaten; bestehende Funktionen eingebettet; gesperrte/archivierte Tenants können keine Testgespräche, OAuth-Flows oder Buchungen ausführen.

### Phase 5 – Härtung und Rolloutfreigabe

- Rollen- und Zwei-Tenant-Szenarien, Restore, Volltests und Browser-E2E bestehen; keine realen externen Aktionen; Betriebs-, Sicherheits- und Migrationsdokumentation vollständig.

## 16. Gesamtabnahmekriterien

- Alle vier Rollen besitzen exakt die dokumentierten Rechte.
- Manipulierte URL-, API-, Realtime-, OAuth-, Call-, Tool- oder WebSocket-Identifier ermöglichen keinen tenantfremden Zugriff.
- Plattformadministratoren arbeiten nur nach sichtbarer, auditierter Kontextwahl; keine Imitation.
- Jedes aktive Unternehmen besitzt mindestens einen aktiven primären Administrator.
- Bestehende Benutzer, Passwort-Hashes, Agentenkonfigurationen, Kalenderverbindungen und Realtime-/Testfunktionen bleiben erhalten.
- Einladungen, Startpasswörter, Recovery, Sitzungsablauf und Login-Throttling funktionieren sicher.
- Gesperrte/archivierte Unternehmen und deaktivierte Benutzer verlieren aktive Sitzungen unmittelbar.
- Auditprotokolle enthalten sicherheitsrelevante Verwaltungsaktionen, aber keine Geheimnisse.
- Backend-, Frontend-, PostgreSQL- und kritische Browser-E2E-Tests bestehen mit mindestens zwei Tenants.
- Backup, Rollback und Wiederanlauf der Baseline wurden vor einer Produktivaktivierung nachgewiesen.

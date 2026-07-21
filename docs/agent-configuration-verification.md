# Abschlussbericht: Konfiguration der Gesprächs-KI

## 1. Analysierte Ausgangsarchitektur

FastAPI, SQLAlchemy, Alembic und PostgreSQL bilden das Backend; React, TypeScript und das OpenAI Agents SDK die Browseroberfläche. Mandanten wurden bereits serverseitig über `ACTIVE_TENANT_SLUG` ermittelt. Authentifizierte Benutzer, Rollen und eine strukturierte Agentenkonfiguration fehlten. Der bestehende Realtime-Agent verwendete `TenantSettings` und mehrere harte Prompt-/VAD-/Stimmenwerte.

## 2. Geänderte Dateien

Geändert beziehungsweise ergänzt wurden Modelle, Migration, Seed, Abhängigkeiten, Schemas, Agent-/Realtime-Services, API-Router, Compose-Konfiguration, React-Routing, API-Client, Realtime-Client, Testseite, neue Einstellungsseite, Styles, Tests und Dokumentation. Die vollständige Liste ist im PR-Diff sichtbar.

## 3. Neues Datenmodell

Versionierte `AgentConfiguration`, normalisierte Themen, Regeln, Wissen, Öffnungszeiten und Capability-Aktivierungen sowie `AppUser`, `TenantMembership` und Audit-Snapshots. `CallSession` speichert die wirksame Konfigurationsversion.

## 4. Datenbankmigrationen

Alembic `0002_tenant_agent_configuration.py`; anschließend übernimmt der idempotente Seed die bisherige Lina-Konfiguration und ergänzt sichere Standardwerte.

## 5. Neue API-Endpunkte

Konfiguration, Wissen, Katalog, Capabilities, Promptvorschau, Stimmprobe und Testsession unter `/api/v1/agent/*`. Realtime-Endpunkte nutzen nun denselben zentralen Runtime-Dienst.

## 6. Neue Benutzeroberflächen

Navigation „KI konfigurieren“ mit sieben responsiven Tabs, Einfach/Erweitert, Schreibschutz nach Rolle, Dirty-/Saving-/Success-/Error-Zuständen, Zurücksetzen, Wissenslisten, Öffnungszeiten, Stimmprobe, Promptvorschau und Testdiagnose.

## 7. Aufbau des Prompt-Compilers

Vierzehn deterministische Abschnitte, feste Plattformregeln mit höchster Priorität, konkrete Mappingtabellen für Stil, Antwortlänge, Aussprache, Kadenz und Off-topic-Verhalten sowie begrenztes aktives Wissen.

## 8. Runtime-Zuordnung

Frisches Laden pro Sitzung; Stimme, Geschwindigkeit, Transkription, VAD, Unterbrechung, Idle-Timeout, Prompt und Werkzeugliste werden in einer `AgentRuntimeConfig` zusammengeführt. Test und Realtime verwenden denselben Dienst.

## 9. Provider-Capability-Registry

Zentrale Registry und Schnittmenge mit tenantseitiger Aktivierung. Sie ist derzeit leer, weil noch kein autorisierter Action-Executor vorhanden ist. OpenAI erhält deshalb keine Tools.

## 10. Sicherheitsmaßnahmen

Serverseitige Tenant-/Benutzer-/Rollenauflösung, Owner/Admin-Schreibschutz, Optimistic Locking, Rate Limits, Eingabegrenzen, pseudonyme Safety-ID, kurzlebige Client-Secrets, keine Secrets im Datenmodell/Browser, Prompt-Injection-Regeln und keine Speicherung von Audio/Transkript.

## 11. Mandantentrennung

Alle Tabellen und Queries werden mit dem serverseitigen `tenant_id` gefiltert. Frontend-Payloads können keinen Mandanten wählen. Ein automatisierter zweiter Mandant weist die Trennung nach.

## 12. Tests und Ergebnisse

Abgeschlossen mit 35 bestandenen Backend-Pytest-Tests und 32 bestandenen Frontend-Vitest-Tests. Ruff und ESLint laufen ohne Befund; TypeScript und der Vite-Produktions-Build sind erfolgreich. Der Migrations-Roundtrip ist automatisiert getestet, PostgreSQL meldete Alembic `0002 (head)`, und der isolierte Compose-Stack war gesund. Der Browser-Smoke-Test deckte alle sieben Tabs, Dirty/Reset, Einfach/Erweitert, Promptvorschau, leere Werkzeugliste und die responsive 390-px-Darstellung ab; es gab keine Browser-Warnungen oder -Fehler.

## 13. Manuell durchzuführende Schritte

Für echte Sprache `OPENAI_API_KEY` ausschließlich serverseitig setzen. Für einen produktiven Mehrbenutzerbetrieb den `ACTIVE_USER_EMAIL`-Adapter durch eine authentifizierte Sitzung ersetzen. Browser-Mikrofon außerhalb von localhost benötigt HTTPS.

## 14. Noch nicht umgesetzte Funktionen

Bewusst außerhalb dieses Goals: Login-Provider, Kalender, OAuth, Telefonie, Buchung, Rückruf, Weiterleitung, eigene Stimmen und Action Tools.

## 15. Vorbereitung Kalenderintegration

Der nächste Schritt kann einen Kalender-Provider und autorisierten Tool-Executor ergänzen. Erst danach wird die passende Capability mit Risikostufe, Bestätigungspflicht, Ergebnisprüfung und Auditierung registriert und dadurch dynamisch für die Realtime-Sitzung verfügbar.

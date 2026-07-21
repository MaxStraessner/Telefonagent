# Architektur

## Komponentenübersicht

```text
Browser / React
      │  HTTP + JSON, /api/v1
      ▼
FastAPI-Router
      │  Dependencies: DB-Session + TenantContext
      ▼
TenantRepository / Services
      │  SQLAlchemy 2
      ▼
PostgreSQL 17
```

Das Frontend besteht aus einem App-Layout, routbaren Seiten, wiederverwendbaren Status-/Fehlerkomponenten und einem zentralen `PlatformDataProvider`. Der Provider bündelt alle Read-only-Aufrufe, typisiert Antworten, bricht veraltete Requests beim Unmount ab und bietet einen manuellen Wiederholungsweg.

FastAPI stellt die API unter `/api/v1` bereit. Konfiguration, Logging, Datenbanksitzungen, Tenant-Auflösung, Repositories, Schemas und Router sind getrennte Verantwortlichkeiten. Jede Request-Session wird per Dependency Injection geöffnet und zuverlässig geschlossen.

Alembic ist alleinige Quelle für die produktive Schemastruktur. Der Container führt Migrationen vor Seed und Serverstart aus. PostgreSQL-Daten liegen in `postgres_data` und werden durch gewöhnliches `docker compose down` nicht entfernt.

## Datenfluss

1. Der Browser lädt die Anwendung ohne eingebettete Unternehmensstammdaten.
2. Der zentrale Provider ruft Tenant, Leistungen, Mitarbeiter, Termine, Health und Plattformstatus ab.
3. FastAPI öffnet eine requestgebundene SQLAlchemy-Session.
4. `get_tenant_context` löst `ACTIVE_TENANT_SLUG` serverseitig auf.
5. `TenantRepository` filtert fachliche Abfragen immer mit der ermittelten UUID.
6. Pydantic validiert und serialisiert die Antwort; React zeigt Lade-, Erfolgs- oder Fehlerzustand.

## Tenant Scoping

`tenant` ist der einheitliche interne Begriff. Alle fachlichen Tabellen besitzen `tenant_id` und einen Index. Der Client sendet keine Tenant-ID und kann den aktiven Mandanten nicht auswählen. Der aktuelle Testbetrieb verwendet genau einen normalen Tenant-Datensatz.

Der zentrale `TenantContext` bildet später den Austauschpunkt für Authentifizierung und autorisierte Mandantenauswahl. Eine spätere Auth-Schicht muss den Tenant aus der geprüften Identität ableiten. Repositorymethoden bleiben tenant-gebunden; direkter, ungefilterter Zugriff aus Routen ist zu vermeiden. Row Level Security ist im aktuellen lokalen Fundament bewusst nicht aktiviert und kann ergänzend eingeführt werden.

## Datenmodell

- `tenants` und 1:1 `tenant_settings`
- `locations` mit vorbereiteter Primärstandort-Kennzeichnung
- `services` und `staff_members` als terminunabhängige Stammdaten
- `appointments` mit optionalem Service/Mitarbeiter, Status, Quelle und zeitzonenfähigem Zeitraum
- `call_sessions` als späterer kanalneutraler Gesprächsrahmen
- `tool_executions` als minimale, payload-freie technische Ausführungsspur

Es werden UUIDs, Foreign Keys, tenant-spezifische Eindeutigkeiten und gezielte Indizes verwendet. Löschkaskaden sind absichtlich nicht eingerichtet, damit fachliche Daten nicht beiläufig verloren gehen.

## Spätere Realtime-Erweiterung

Das typisierte Frontend-Zustandsmodell umfasst `idle`, `connecting`, `connected`, `user_speaking`, `assistant_thinking`, `assistant_speaking`, `tool_running`, `error`, `ended` und `not_configured`. Eine spätere WebRTC-Schicht soll diese Zustände treiben, ohne das Seitenlayout neu aufzubauen.

Vorgesehener Ablauf:

1. Backend prüft Tenant und Konfiguration.
2. Backend stellt eine kurzlebige, eng begrenzte Client-Berechtigung aus.
3. Browser baut WebRTC direkt zum Realtime-Dienst auf.
4. Ereignisadapter überführt Provider-Events in den vorhandenen Zustandsautomaten.
5. Werkzeugaufrufe gehen ausschließlich über validierte, tenant-gebundene Backend-Endpunkte.

Dauerhafte API-Schlüssel bleiben serverseitig. Der aktuelle Stand führt keine dieser Aktionen aus.

## Spätere Kalender- und Telefonie-Provider

Kalenderanbieter werden hinter einer normalisierten Provider-Schnittstelle für Verfügbarkeitsabfrage, Reservierung und Synchronisation ergänzt. Provider-IDs gehören in eigene tenant-spezifische Konfigurationstabellen. Zeitzonen werden an Systemgrenzen explizit behandelt; intern bleiben Zeitpunkte zeitzonenfähig.

Telefonieanbieter werden analog über Adapter für eingehende Sessions, Signaturprüfung, Statusereignisse und Medienübergabe angebunden. Browser- und Telefonkanal teilen sich `call_sessions`, aber nicht zwingend denselben Transport. Webhooks benötigen Replay-Schutz und idempotente Verarbeitung.

## Plattformkern und Branchenkonfiguration

Der Kern kennt Mandanten, Standorte, Leistungen, Mitarbeiter, Termine, Gespräche und Werkzeugausführungen. Begriffe, Dauerwerte, Begrüßungen, Rollen und spätere Buchungsregeln sind Konfiguration des jeweiligen Unternehmens oder der Branche. `hair_salon` ist Seed-Konfiguration, keine Verzweigung im Plattformcode. Weitere Branchen können dadurch Daten und Regeln ergänzen, ohne Navigation, Tenant-Auflösung oder Basisschema zu duplizieren.


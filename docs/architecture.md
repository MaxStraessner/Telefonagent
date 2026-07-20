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

Browser / Realtime-Modul ── WebRTC-Audio ──► OpenAI Realtime
             ▲                                ▲
             └── kurzlebiges Client-Secret ───┘
                       FastAPI
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

## Realtime-Sprachverbindung

Das typisierte Frontend-Zustandsmodell umfasst `not_configured`, `idle`, `requesting_microphone`, `connecting`, `connected`, `user_speaking`, `assistant_thinking`, `assistant_speaking`, `muted`, `error` und `ended`. Das gekapselte Realtime-Modul treibt diese Zustände in der bestehenden Testgesprächsseite.

Ablauf:

1. Der Nutzer startet das Gespräch und gibt erst dann sein Mikrofon frei.
2. FastAPI löst den aktiven Tenant serverseitig auf und lädt dessen Assistentenname, Sprache und Begrüßung.
3. FastAPI ruft mit dem ausschließlich serverseitigen `OPENAI_API_KEY` OpenAIs Client-Secret-Endpunkt auf. Ein gehashter Tenantbezug wird als `OpenAI-Safety-Identifier` gesendet.
4. Der Browser erhält nur das kurzlebige `ek_…`-Secret und nicht geheime Agentenkonfiguration.
5. `RealtimeAgent`, `RealtimeSession` und `OpenAIRealtimeWebRTC` bauen die direkte Audioverbindung auf.
6. SDK- und Transportereignisse aktualisieren Zustand, flüchtiges Transkript und Diagnosemetriken.
7. Beenden, Fehler, Seitenwechsel oder Zeitlimit schließen Sitzung, Transport, Mikrofontracks, Audioelement, Listener und Timer.

Dauerhafte API-Schlüssel bleiben serverseitig. Audio, Transkripte und Realtime-Ereignisse werden nicht in PostgreSQL persistiert. Die vorhandenen Tabellen `call_sessions` und `tool_executions` bleiben in diesem Schritt ungenutzt. Werkzeuge sind sowohl in der Backend-Sitzung als auch im Agents SDK explizit leer.

Die zentral konfigurierte VAD nutzt `server_vad` mit Schwelle `0.5`, 300 ms Präfix, 600 ms Stille, automatischer Antwort und automatischer Unterbrechung. Beide Seiten erhalten diese Werte aus derselben Backend-Konfiguration; Details und Testablauf stehen in [realtime-voice.md](realtime-voice.md).

## Spätere Kalender- und Telefonie-Provider

Kalenderanbieter werden hinter einer normalisierten Provider-Schnittstelle für Verfügbarkeitsabfrage, Reservierung und Synchronisation ergänzt. Provider-IDs gehören in eigene tenant-spezifische Konfigurationstabellen. Zeitzonen werden an Systemgrenzen explizit behandelt; intern bleiben Zeitpunkte zeitzonenfähig.

Telefonieanbieter werden analog über Adapter für eingehende Sessions, Signaturprüfung, Statusereignisse und Medienübergabe angebunden. Browser- und Telefonkanal teilen sich `call_sessions`, aber nicht zwingend denselben Transport. Webhooks benötigen Replay-Schutz und idempotente Verarbeitung.

## Plattformkern und Branchenkonfiguration

Der Kern kennt Mandanten, Standorte, Leistungen, Mitarbeiter, Termine, Gespräche und Werkzeugausführungen. Begriffe, Dauerwerte, Begrüßungen, Rollen und spätere Buchungsregeln sind Konfiguration des jeweiligen Unternehmens oder der Branche. `hair_salon` ist Seed-Konfiguration, keine Verzweigung im Plattformcode. Weitere Branchen können dadurch Daten und Regeln ergänzen, ohne Navigation, Tenant-Auflösung oder Basisschema zu duplizieren.


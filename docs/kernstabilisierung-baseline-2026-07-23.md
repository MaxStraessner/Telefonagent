# Kernstabilisierung – Baseline 2026-07-23

## Repository

- Branch: `feature/realtime-config-drift`
- Ausgangs-Commit: `e803fbc`
- Rückkehrpunkt: `main` auf `e803fbc`
- Arbeitsbaum vor Beginn: sauber
- OpenAI Agents SDK: `@openai/agents` 0.13.5

## Bestehende Tests

- Backend: 112 bestanden
- Frontend: 68 bestanden

Die Backendtests wurden mit `backend/.venv/Scripts/python.exe` ausgeführt. Das
globale Python 3.14 ist nicht die Projektlaufzeit und ist mit der installierten
SQLAlchemy-Version nicht kompatibel.

## Docker

Docker Desktop und der Docker-Client 29.6.1 sind installiert. Der erste
Statusabruf war innerhalb der Codex-Sandbox nicht zulässig. Der frische
Compose-Build und die Containerprüfung erfolgen deshalb im abschließenden
Integrationscheck mit explizit freigegebenem Docker-Zugriff.

Ein Git- oder Branchwechsel ändert bereits laufende Container nicht. Für jeden
geprüften Stand werden Images neu gebaut und Container neu erstellt.

## Freigegebener Umfang

In diesem Branch werden ausschließlich Runtime-Konfiguration, Realtime- und
Toolkoordination, Audio/Mikrofon, Europe/Berlin-Datumsauflösung,
Buchungszustand, kontextbezogene Zustimmung und die zugehörigen lokalen Tests
bearbeitet. Authentifizierung, CSRF, PostgreSQL-Ausschlussconstraints,
Reconciliation, Worker und Abhängigkeitsupdates bleiben außerhalb dieses
Umsetzungsschritts.

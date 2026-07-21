# Prüfbericht: OpenAI Realtime Sprachagent über WebRTC

Stand: 20. Juli 2026

## Ausgangszustand und Git-Basis

Die lokale Foundation aus `feature/foundation-platform-ui` wurde vor Beginn in den lokalen `main` übernommen. Die Realtime-Implementierung liegt getrennt auf `openai-realtime-sprachagent-ueber-webrtc` und ist als gestapelter Draft-PR gegen die Foundation geöffnet. Die vorhandene React-/FastAPI-/PostgreSQL-Anwendung wurde erweitert; es entstand keine zweite Web-App.

Relevante Commits:

- `2ed8473` – erste vollständige Realtime-Integration
- `a22d18e` – gehärteter Sitzungslebenszyklus, strukturierte Fehler, Diagnose und zusätzliche Abnahmetests

Draft-PR: <https://github.com/MaxStraessner/Telefonagent/pull/2>

## Umgesetzter Stand

- Direkte Browser-Audioverbindung über den offiziellen OpenAI Agents SDK mit `RealtimeAgent`, `RealtimeSession` und `OpenAIRealtimeWebRTC`
- Standardmodell `gpt-realtime-2.1`, Standardstimme `marin`, beide zentral über das Backend konfigurierbar
- Server-seitiges Erzeugen eines 60 Sekunden gültigen Client-Secrets über `POST /v1/realtime/client_secrets`
- Dauerhafter `OPENAI_API_KEY` ausschließlich im Backend; keine geheime `VITE_`-Variable
- Tenantbindung aus dem serverseitigen Kontext und pseudonymer `OpenAI-Safety-Identifier`
- Deutscher Terminassistent mit klaren Grenzen: keine Tools, keine Buchung, keine Verfügbarkeitsbehauptung und keine sachfremde Beratung
- Zustände für Mikrofonanfrage, Verbindungsaufbau, verbunden, stumm, Nutzer spricht, Assistent denkt/spricht, Fehler und Ende
- Manueller Start, Begrüßung, Mute, Barge-in, explizites Unterbrechen und maximales Sitzungslimit
- Flüchtiges Live-Transkript ohne Backendübertragung oder Persistenz
- Normalisierte, auf 40 Einträge begrenzte Diagnoseereignisse und redigierte optionale Rohdetails
- Lokale Latenzmessung ab erkanntem Nutzer-Sprachende bis zur hörbaren Audioausgabe sowie separate Zählung abgeschlossener Gesprächsrunden
- Einheitlicher Cleanup für Ende, Fehler, Timeout, Navigation und Komponentenabbau
- Verständliche Fehler für Mikrofonberechtigung, fehlendes/blockiertes Mikrofon, unsicheren/inkompatiblen Browser, abgelaufenes Secret, Konfigurationsabweichung, Verbindungs-Timeout, Transportverlust und blockierte Audiowiedergabe

## Sicherheits- und Datenschutzprüfung

- Der Standard-API-Key wird nur als `Authorization`-Header vom Backend an OpenAI gesendet.
- Client-Secret-Antworten enthalten nur Secret, Ablauf, optionale Session-ID, Modell, Stimme und Tenant-ID.
- Provider-Antwortkörper und geheime Details werden nicht an den Browser durchgereicht.
- Der Browser erhält keine Function Tools; Backend und SDK setzen Toollisten leer und `tool_choice` auf `none`.
- Audio, Transkripte, Ereignisse, Call-Sessions und Tool-Ausführungen werden durch diesen Ablauf nicht persistiert.
- Ein Repository-Scan fand weder VPS-Zugangsdaten noch einen API-Schlüssel. Der dokumentierte Name `VITE_OPENAI_API_KEY` kommt ausschließlich als ausdrückliches Negativbeispiel vor.
- Die VPS wurde in diesem Arbeitsschritt nicht verändert.

## Zentrale Konfiguration

```dotenv
OPENAI_API_KEY=
OPENAI_REALTIME_MODEL=gpt-realtime-2.1
OPENAI_REALTIME_VOICE=marin
OPENAI_REALTIME_MAX_SESSION_MINUTES=10
OPENAI_REALTIME_TRANSCRIPTION_ENABLED=true
OPENAI_REALTIME_LOG_RAW_EVENTS=false
OPENAI_SAFETY_IDENTIFIER_SALT=installationsspezifischer-zufallswert
```

`VITE_API_BASE_URL` ist die einzige Realtime-relevante Frontend-Buildvariable und enthält ausschließlich die öffentliche Backend-Basis.

## Wesentliche geänderte Bereiche

- `backend/app/core/config.py`, `backend/app/services/realtime.py`, `backend/app/schemas/api.py`, `backend/app/api/v1/router.py`
- `frontend/src/features/realtime/` mit Client, Hook, Fehlern, Ereignissen, Metriken, Timer und Transkriptabbildung
- `frontend/src/pages/ConversationPage.tsx` und `frontend/src/styles/global.css`
- `backend/tests/test_realtime.py`, `frontend/tests/realtimeFlow.test.tsx`, `frontend/tests/realtimeHelpers.test.ts`
- `.env.example`, `docker-compose.yml`, `README.md`, `docs/architecture.md`, `docs/realtime-voice.md`

## Automatisierte Verifikation

| Prüfung | Ergebnis |
| --- | --- |
| `npm.cmd test -- --run --reporter=dot` | 3 Testdateien, 29 Tests bestanden |
| `npm.cmd run build` | TypeScript und Vite erfolgreich; 362 Module transformiert |
| `docker-compose run --build --rm --no-deps backend python -m pytest` | 23 Tests bestanden; eine bekannte Starlette-Abkündigungswarnung |
| `docker-compose config --quiet` | erfolgreich |
| `git diff --check` | keine Whitespace-Fehler |
| Secret-/Zugangsdaten-Scan | keine realen Geheimnisse gefunden |

Die Frontendtests decken unter anderem Zustandsübergänge, POST des Client-Secrets, Mikrofonfehler, Browserunterstützung, Secret-Ablauf, WebRTC-Fehler, Aufbau-Timeout, Audioblockade, Transportabbruch, Mute, Unterbrechung, Ereignisnormalisierung, Puffergrenze, Metriken, verspätet auflösende Starts und vollständigen Cleanup ab. Backendtests prüfen Tenant-Scoping, zentrale Modell-/Stimmenwerte, Upstream-Payload, Safety-ID, fehlenden/blanken Key, strukturierte Providerfehler, Timeouts, ungültige Antworten und ausbleibende Persistenz.

## Docker- und Browser-Smoke-Test

Der Compose-Stack dieses Projekts wurde getrennt auf Frontend-Port `5173` und Backend-Port `8001` gebaut. Port `8000` und fremde Projekte wurden nicht berührt.

- Backend: healthy, Datenbank verbunden
- Frontend `/testgespraech`: HTTP 200
- Plattformstatus: Modell `gpt-realtime-2.1`, Stimme `marin`, `realtime_voice_configured: false`
- Client-Secret ohne API-Key: kontrolliert HTTP 503 mit `realtime_not_configured`
- UI: sichtbarer „Nicht eingerichtet“-Hinweis und deaktivierter Start
- Präsentationsmodus: technische Diagnose ausgeblendet
- Mobile Prüfung bei 390 × 844 Pixeln: kein horizontaler Überlauf
- Browserkonsole: keine Fehler

## Nicht praktisch verifizierbar

In der lokalen Umgebung ist kein `OPENAI_API_KEY` konfiguriert. Daher wurden keine kostenpflichtige echte OpenAI-Realtime-Sitzung, keine physische Mikrofon-/Lautsprecherausgabe, keine hörbare Begrüßung, kein reales Barge-in und keine echten Latenzwerte erzeugt. Diese Punkte sind ausdrücklich nicht als praktisch bestanden markiert. Der vollständige manuelle Ablauf steht in `docs/realtime-voice.md` und muss mit einem abrechnungsfähigen API-Projekt, Modellzugriff und einer verfügbaren Stimme durchgeführt werden.

## Bekannte Hinweise

- Der produktive Frontend-Build meldet lediglich die bestehende Vite-Warnung für ein JavaScript-Bundle über 500 kB; der Build ist erfolgreich.
- Modell- und Stimmenverfügbarkeit hängen vom verwendeten OpenAI-Projekt ab und werden bei Providerablehnung strukturiert gemeldet.
- Dieser Schritt enthält bewusst keine SIP-Telefonie, Kalenderintegration, Function Tools, Buchung, Stornierung, CRM, Aufzeichnung oder persistente Gesprächsdaten.

## Exakter nächster Entwicklungsschritt

Mehrmandantenfähige Leistungs, Mitarbeiter, Öffnungszeiten, Verfügbarkeits und Buchungslogik mit sicherer PostgreSQL Persistenz und Doppelbuchungsschutz.

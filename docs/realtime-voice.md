# Realtime-Sprachagent über WebRTC

## Überblick

Die bestehende Testgesprächsseite verwendet den offiziellen OpenAI Agents SDK für TypeScript (`@openai/agents/realtime`) mit `RealtimeAgent`, `RealtimeSession` und `OpenAIRealtimeWebRTC`. Es gibt keine zweite Web-App und keinen eigenen Audio-Uploader. Der SDK verbindet das vom Nutzer freigegebene Mikrofon und ein kontrolliertes `<audio>`-Element direkt per WebRTC mit OpenAI.

FastAPI bleibt die Sicherheitsgrenze: Der normale `OPENAI_API_KEY` existiert ausschließlich dort. Der Browser bekommt nicht geheime, tenantbezogene Agentendaten und ein kurzlebiges Client-Secret. PostgreSQL bleibt für Stammdaten zuständig; Audio, Transkripte und vollständige Gesprächsinhalte werden nicht gespeichert.

## Verbindungs- und Secret-Ablauf

1. Der Browser lädt Plattformstatus und aktiven Tenant über `/api/v1`.
2. Erst der Klick auf „Testgespräch starten“ löst `getUserMedia` aus.
3. Nach der Mikrofonfreigabe lädt der Browser `GET /api/v1/realtime/agent-config` und `POST /api/v1/realtime/client-secret`.
4. FastAPI ermittelt den Tenant aus der validierten Administrationssitzung; der Client kann keine Tenant-ID wählen.
5. FastAPI ruft `POST https://api.openai.com/v1/realtime/client_secrets` mit kurzem Timeout und 60 Sekunden Secret-Laufzeit auf.
6. Der Request enthält dynamische Anweisungen, Modell, Stimme, Transkription, leere Werkzeugliste und zentrale VAD-Werte. Der `OpenAI-Safety-Identifier` ist ein HMAC aus Tenant-UUID und installationsbezogenem Salt, nicht aus personenbezogenen Daten.
7. Der Browser prüft Tenant, Modell und Stimme der beiden Antworten gegeneinander und verbindet die `RealtimeSession` mit dem `ek_…`-Secret.
8. Erst nach erfolgreicher Verbindung fordert der Transport die gespeicherte Begrüßung als erste hörbare Antwort an.

Providerfehler werden in kontrollierte Codes wie `realtime_not_configured`, `realtime_provider_timeout`, `realtime_provider_authentication_failed`, `realtime_model_unavailable`, `realtime_voice_unavailable`, `realtime_provider_rejected` oder `realtime_provider_invalid_response` übersetzt. OpenAI-Antwortkörper und Standard-Key werden nicht weitergereicht.

## Browser-Sitzung

Der Browser verwaltet Mikrofonfreigabe, Audioelement, WebRTC-Transport, SDK-Sitzung, Zustand und flüchtige Diagnose. Vor dem Start werden sicherer Browserkontext, Media-APIs und WebRTC-Unterstützung geprüft. Der Verbindungsaufbau hat ein 15-Sekunden-Limit. Mehrfachstarts werden während Aufbau und aktiver Sitzung verhindert. `RealtimeSession.mute()` schaltet ohne Neuverbindung stumm; VAD und `interrupt_response` ermöglichen Barge-in. Eine zusätzliche Schaltfläche kann über `RealtimeSession.interrupt()` eine Ausgabe kontrolliert stoppen.

Das Audioelement liefert das Ereignis `playing` für die wahrgenommene Hauptlatenz:

```text
erste hörbare Antwort - Ende des Nutzerbeitrags
```

Angezeigt werden außerdem Verbindungsdauer, letzte, mittlere, schnellste und langsamste Antwort, Rundenzahl und Sitzungsdauer. Diese Werte sind lokale Entwicklungsdiagnosen, keine garantierten Providerwerte.

Der Ereignispuffer hält höchstens 40 Einträge. Transportnamen werden für die Diagnose normalisiert, darunter `speech_started`, `speech_stopped`, `response_created`, `response_completed`, `first_audio_playing`, `session_connected` und `session_disconnected`. Standardmäßig werden nur Ereignisnamen und Zeitpunkte gehalten. Rohpayloads erscheinen nur mit `OPENAI_REALTIME_LOG_RAW_EVENTS=true`; sensible Felder und Gesprächsinhalte werden dabei zusätzlich redigiert. Die Option ist trotzdem ausschließlich für kontrollierte lokale Entwicklung gedacht.

## VAD und Transkript

Die ausgewogene zentrale Startkonfiguration lautet:

```text
Typ: server_vad
Schwelle: 0.5
Präfix: 300 ms
Stille bis Turn-Ende: 600 ms
Automatische Antwort: ja
Automatische Unterbrechung: ja
```

Die Eingabetranskription verwendet bei aktivierter Option `gpt-4o-mini-transcribe`. SDK-History-Items werden über ihre stabile Item-ID aktualisiert, sodass Deltas keine doppelten Nachrichten erzeugen. Unvollständige Assistentenbeiträge erscheinen als unterbrochen. Das sichtbare Transkript kann lokal geleert werden und wird nie automatisch an das Backend gesendet. Ist Eingabetranskription im Projekt nicht verfügbar, kann die Audioverbindung weiterarbeiten, während das Nutzertranskript unvollständig bleibt.

## Sitzungsgrenze und Cleanup

`OPENAI_REALTIME_MAX_SESSION_MINUTES` ist standardmäßig 10. Eine Minute vor Ablauf erscheint im Testmodus ein Hinweis; beim Limit wird keine neue Sitzung gestartet. Manuelles Ende, Limit, Fehler, Navigation und Komponentenabbau führen denselben Cleanup aus:

- Realtime-Sitzung und WebRTC-Transport schließen
- Peer-Verbindung und Datenkanal über den SDK schließen
- alle Mikrofontracks stoppen
- sämtliche SDK-, Transport-, Track- und Audio-Listener entfernen
- Audioelement pausieren und `srcObject` leeren
- Fetch und Zeitgeber abbrechen
- keine weitere API-Nutzung durch die alte Sitzung zulassen

## Umgebungsvariablen

```dotenv
OPENAI_API_KEY=
OPENAI_REALTIME_MODEL=gpt-realtime-2.1
OPENAI_REALTIME_VOICE=marin
OPENAI_REALTIME_MAX_SESSION_MINUTES=10
OPENAI_REALTIME_TRANSCRIPTION_ENABLED=true
OPENAI_REALTIME_LOG_RAW_EVENTS=false
OPENAI_SAFETY_IDENTIFIER_SALT=installationsspezifischer-zufallswert
```

Modell und Stimme müssen für das verwendete OpenAI-Projekt verfügbar sein. Keine dieser Einstellungen darf als `VITE_OPENAI_API_KEY` oder vergleichbare Frontend-Variable angelegt werden. `VITE_API_BASE_URL` enthält nur die öffentliche Backend-Basis.

## Automatisierte Tests

Frontendtests mocken Agents SDK, Medien-APIs und Backend. Sie prüfen unter anderem fehlende und vorhandene Konfiguration, Nutzerstart, POST des Client-Secrets, Mikrofonablehnung, Tokenablauf, WebRTC-Fehler, Stummschaltung, Unterbrechung, Transkript-Deduplication, lokales Leeren, Latenz, Zeitlimit, Mehrfachstart und Cleanup. Backendtests mocken `httpx` und prüfen Tenant-Scoping, Secret-Payload, Providerfehler, Timeouts, Safety-ID, zentrale Modell-/Stimmenwerte und fehlende Persistenz. Automatisierte Tests öffnen keine echte OpenAI-Verbindung und erzeugen keine API-Kosten.

## Manueller Abnahmetest

Voraussetzungen: API-Projekt mit eingerichteter Abrechnung, gültiger Key nur in `.env`, Zugriff auf Modell und Stimme, gestarteter Compose-Stack und aktueller Browser mit Mikrofon.

1. Systemseite öffnen; OpenAI Realtime muss „Konfiguriert“ zeigen.
2. Testgespräch öffnen und starten.
3. Mikrofon erlauben und die Begrüßung hören.
4. Ohne eingerichteten Kalender sagen: „Hallo, ich hätte gerne nächste Woche einen Termin.“ Der Agent muss die fehlende Buchungsmöglichkeit offen benennen und darf keine Verfügbarkeit behaupten.
5. Optional einen Kalender gemäß [calendar-integrations.md](calendar-integrations.md) einrichten und dieselbe Anfrage wiederholen. Der Agent soll Terminart und Zeitraum klären und nur tatsächlich gelieferte Slots nennen.
6. Einen angebotenen Slot auswählen und Kontaktdaten bestätigen. Der Agent darf die Buchung erst nach erfolgreicher Tool- und Providerantwort als bestätigt melden.
7. Während einer Antwort sprechen; alte Audioausgabe muss abbrechen.
8. Mikrofon stummschalten und wieder aktivieren.
9. Nutzer- und Agententranskript, Zustände, Ereignisse und Latenzen prüfen.
10. In den Präsentationsmodus wechseln; Modell, VAD, Rohereignisse und Latenzen müssen verborgen sein.
11. Gespräch beenden; Browser-Mikrofonindikator muss erlöschen.
12. Ein neues Gespräch starten, dann währenddessen die Seite verlassen; auch hier muss das Mikrofon enden.
13. Sachfremd fragen: „Wer hat die Fußballweltmeisterschaft gewonnen?“ Erwartet ist eine knappe Rückführung auf Terminangelegenheiten.

Ziel für lokale Entwicklung ist eine erste hörbare Antwort überwiegend unter 1,5 Sekunden nach erkanntem Sprachende. Das ist kein vertraglicher oder technisch garantierter Wert.

## Typische Fehler

- „Nicht eingerichtet“: `OPENAI_API_KEY` fehlt im Backend oder der Backend-Container wurde nach `.env`-Änderung nicht neu erstellt.
- Secret abgelehnt: API-Key, Abrechnung, Projektzugriff, Modell und Stimme prüfen.
- Timeout: Netzwerk und Erreichbarkeit der OpenAI API prüfen und erneut starten.
- Mikrofon verweigert/nicht gefunden/blockiert: Site-Berechtigung, Betriebssystemfreigabe, Eingabegerät und konkurrierende Anwendungen prüfen.
- WebRTC/ICE fehlgeschlagen: Firewall, VPN, Unternehmensnetz oder Browser prüfen.
- Keine Nutzertranskription: Projektzugriff auf das Transkriptionsmodell prüfen; die Audioverbindung kann dennoch funktionieren.
- Audio vom Browser blockiert: Site-Audiofreigabe und Autoplay-Einstellung prüfen und das Gespräch erneut per Nutzerklick starten.
- Keine Ausgabe: Lautsprecher und Ausgabegerät prüfen; das Audioelement meldet Wiedergabefehler kontrolliert in der Oberfläche.
- Verbindung nach 15 Sekunden abgebrochen: Netzwerk, Firewall, VPN und WebRTC-Unterstützung prüfen.

## Kosten und bewusste Nichtziele

Realtime-Audio nutzt die kostenpflichtige OpenAI API. Ein ChatGPT-Abonnement deckt API-Nutzung nicht automatisch ab. Sitzungsgrenze und manueller Start reduzieren unbeabsichtigte Nutzung, ersetzen aber kein Projektbudget oder Usage-Limit.

Enthalten sind ausschließlich die kontrollierten Kalenderwerkzeuge für Terminarten, Verfügbarkeit und Neubuchung. Nicht enthalten sind Mitarbeiter- oder Ressourcenverteilung, Terminverschiebung, Stornierung, SIP/Telefonnummer, CRM, n8n, Aufzeichnung oder persistente Transkripte. Der Agent darf diese Fähigkeiten nicht vortäuschen.

Bewusste SDK-Abweichungen sind auf Kontrolle beschränkt: Ein explizites Audioelement ermöglicht echte Wiedergabelatenz, ein expliziter MediaStream erlaubt zuverlässigen Track-Cleanup, und Transportereignisse liefern Diagnosezustände. Signalisierung, Audiotransport und Barge-in bleiben beim offiziellen SDK.

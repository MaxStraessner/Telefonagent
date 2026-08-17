# Twilio lokal testen

Twilio ist nur ein zusätzlicher Transport für den bestehenden Realtime-Agenten. Prompt, Stimme, Tools, Kalender und Tenant-Kontext stammen weiterhin aus derselben Agentenkonfiguration wie beim Browser-Testgespräch.

## Voraussetzungen

In `.env` werden ausschließlich serverseitig gesetzt:

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_STREAM_TOKEN_SECRET` mit mindestens 32 zufälligen Bytes
- `OPENAI_API_KEY`
- `APP_BASE_URL` als aktuelle öffentliche HTTPS-Tunnel-URL, ohne abschließenden Slash

Die Werte dürfen nicht mit `VITE_` beginnen. Nach einer Änderung muss der Backend-Container neu gebaut beziehungsweise neu gestartet werden.

## Tunnel und Synchronisation

Der lokale Compose-Stack veröffentlicht den Backend-Port über `BACKEND_PORT`. Wenn der Stack wie im lokalen Entwicklungsstand auf Port `8001` läuft, kann beispielsweise ein Tunnel mit `ngrok http 8001` gestartet werden. Danach wird dessen HTTPS-URL als `APP_BASE_URL` gesetzt und der Backend-Container neu erstellt.

Ein Plattformadmin öffnet anschließend das Unternehmen, wählt im Abschnitt „Telefonie“ eine bereits im zentralen Twilio-Konto vorhandene Voice-Nummer und speichert. Das Backend setzt für diese Nummer zentral:

- Voice URL: `<APP_BASE_URL>/api/v1/twilio/voice`
- Voice method: `POST`
- Stream status callback: `<APP_BASE_URL>/api/v1/twilio/stream-status`

Nach jedem Wechsel der Tunnel-URL muss „Erneut synchronisieren“ ausgeführt werden. Nummern mit TwiML Application oder SIP-Trunk werden bewusst nicht überschrieben und erscheinen als blockiert.

## Sicherer Testablauf

1. Migration `0015` und gesunde Container prüfen.
2. Zwei aktive Testunternehmen mit zwei verschiedenen vorhandenen Twilio-Nummern verbinden.
3. Beide Zuordnungen müssen `synced` anzeigen und dieselbe zentrale Voice URL besitzen.
4. Nacheinander beide Nummern anrufen und Tenant-Konfiguration, Begrüßung, Stimme sowie einen harmlosen Verfügbarkeitsaufruf prüfen.
5. Während einer Agentenantwort dazwischen sprechen und kontrollieren, dass gepuffertes Audio nicht später nachläuft.
6. Auflegen und prüfen, dass je Nummer genau eine technische `telephone`-CallSession beim richtigen Tenant abgeschlossen wurde.
7. Danach ein Browser-WebRTC-Testgespräch durchführen.

Audio, Transkripte, Toolargumente und rohe Providerereignisse dürfen weder in der Datenbank noch in Logs erscheinen. Der Test umfasst keinen Nummernkauf, keine ausgehenden Anrufe und kein Deployment.

# Realtime-Sprachagent über WebRTC

## Autoritativer Ablauf

Die Testgesprächsseite verwendet `@openai/agents` 0.13.5 mit
`RealtimeAgent`, `RealtimeSession` und `OpenAIRealtimeWebRTC`. Es gibt genau
eine technische Gesprächssteuerung im Browser:

1. Nach dem Nutzerklick fordert der Browser das Mikrofon an.
2. `POST /api/v1/realtime/session-bootstrap` kompiliert das tenantgebundene
   Runtime-Manifest, legt die technische `CallSession` an und mintet ein
   60 Sekunden gültiges `ek_…`-Secret.
3. Die Secret-Anfrage enthält nur die Laufzeit des Secrets. Prompt, Tools,
   Stimme, VAD und weitere Sessionwerte werden nicht ein zweites Mal im
   Backend-Request gespiegelt.
4. Der Browser prüft, dass Secret-Metadaten und Manifest zusammengehören.
5. Der Agents-SDK setzt beim Verbindungsaufbau die eine aktive
   Sessionkonfiguration. Der WebRTC-SDP-Request verwendet dabei explizit
   `https://api.openai.com/v1/realtime/calls?model=<manifest-model>`, weil die
   installierte SDK-Version den Modellparameter am Standardendpunkt nicht
   selbst ergänzt. Erst nach `session.updated` gilt die Verbindung als bereit.
6. Die Begrüßung wird einmalig über den SDK-Transport angefordert.
7. Danach erzeugt Server-VAD normale Gesprächsantworten automatisch.

Der Standard-Key bleibt ausschließlich im Backend. Der Browser erhält nur das
kurzlebige Secret und das tenantgebundene Runtime-Manifest. Audio, vollständige
Transkripte und Gesprächsinhalte werden nicht in PostgreSQL gespeichert.

Die Trennung von Secret-Ausstellung und überschreibbarer Clientkonfiguration
entspricht der offiziellen
[Client-Secret-API](https://developers.openai.com/api/reference/resources/realtime/subresources/client_secrets/methods/create).

## Tool-Aufrufe und Fortsetzung

Für einen Function Call gilt ausschließlich dieser Pfad:

```text
response mit function_call
  -> Agents-SDK führt das lokale Tool einmal aus
  -> Backend liefert ein strukturiertes Ergebnis
  -> SDK sendet function_call_output mit derselben call_id
  -> SDK-Sequencer fordert genau eine Folgeantwort an
  -> response.created / Audio / response.done
```

Die App ruft nach einem Tool weder `response.create` selbst auf noch wartet sie
mit einem Fortsetzungs-Timer auf ein bestimmtes Ereignis. Erfolg, leeres
Ergebnis und kontrollierter Toolfehler werden gleichermaßen als Toolausgabe an
das Modell zurückgegeben. Der Prompt verlangt anschließend eine natürliche
Einordnung und genau die nächste nötige Frage.

Der Frontend-Executor dedupliziert `(session_id, tool_call_id)` während der
gesamten Browsersitzung. Das Backend lehnt eine bereits verwendete
`tool_call_id` vor einer zweiten Ausführung mit `409 duplicate_tool_call` ab.
Provider-Buchungen besitzen zusätzlich ihre bestehende fachliche
Idempotenzsicherung.

Die erwartete Realtime-Reihenfolge ist in der offiziellen Dokumentation unter
[Function-Call-Ergebnis bereitstellen](https://developers.openai.com/api/docs/guides/realtime-conversations#provide-the-results-of-a-function-call-to-the-model)
beschrieben.

## VAD, Audio und Unterbrechung

Normale Nutzerturns werden durch die tenantgebundene VAD-Konfiguration erkannt.
Bei der ausgewogenen Server-VAD-Konfiguration gelten typischerweise:

```text
threshold: 0.5
prefix_padding_ms: 300
silence_duration_ms: 600
create_response: true
interrupt_response: tenantgebundene Einstellung
```

`create_response=true` bedeutet, dass die App nach erkanntem Sprachende keine
zweite Antwort anfordert. Bei aktivierter Unterbrechung bleibt das Mikrofon
während Antwort und Playback offen; der Server und SDK behandeln Barge-in. Bei
deaktivierter Unterbrechung bleibt es bis zum tatsächlichen Ende des
Audiopuffers gesperrt. Ein laufendes Tool sperrt das Mikrofon nicht pauschal.

`response.output_audio.done` belegt nur das Ende der Generierung. Erst
`output_audio_buffer.stopped` belegt das tatsächliche Wiedergabeende.
`output_audio_buffer.cleared` und `conversation.item.truncated` kennzeichnen
eine Unterbrechung. Details zu den Server-VAD-Ereignissen stehen in der
[Realtime-VAD-Dokumentation](https://developers.openai.com/api/docs/guides/realtime-vad#server-vad).

## Fehler und Diagnose

Ein Providerfehler wird mit Typ, Code und Parameter klassifiziert; die
Provider-Nachricht wird nicht geloggt oder an die Oberfläche durchgereicht.
Beispiele:

- `realtime_response_create_rejected`: OpenAI lehnt `response.create` ab.
- `realtime_provider_request_failed`: sonstiger Realtime-Providerfehler.
- `realtime_connection_lost`: WebRTC-Transport wurde getrennt.
- `realtime_configuration_ack_timeout`: `session.updated` bleibt aus.
- `realtime_bootstrap_mismatch`: Secret-Metadaten und Manifest widersprechen
  sich.

Providerfehler, die sowohl über Datenkanal als auch SDK-Event eintreffen,
werden anhand Typ, Code und Parameter nur einmal gemeldet.

Strukturierte Diagnoseereignisse enthalten, soweit verfügbar:

- Session-, Response-, Item- und Tool-Call-ID
- Toolname und Toolstatus
- Response- und Audiozustand
- Data-Channel- und Peer-Connection-Status
- Fehlercode und Zeitstempel

Passwörter, API-Keys, Client-Secrets, CSRF-Werte, Audio, Transkripte,
Toolargumente und Providerfehlermeldungen gehören nicht in diese Ereignisse.
Rohereignisse bleiben standardmäßig deaktiviert.

## Cleanup und Sitzungsgrenze

`OPENAI_REALTIME_MAX_SESSION_MINUTES` begrenzt lokale Testgespräche. Manuelles
Ende, Fehler, Navigation, Zeitlimit und Komponentenabbau verwenden denselben
Cleanup:

- SDK-Sitzung und WebRTC-Transport schließen
- Mikrofontracks stoppen
- SDK-, Transport-, Track- und Audio-Listener entfernen
- Audioelement pausieren und `srcObject` leeren
- offene Fetches und UI-Zeitgeber abbrechen

Jeder Start besitzt eine UUID `call_attempt_id`. Das Backend persistiert den
Zustandsautomaten `starting -> provisioned -> connected -> ended` mit den
terminalen Alternativen `cancelled`, `failed` und `abandoned`. Browserreloads
speichern ausschließlich die nicht sensitive Attempt-ID vorübergehend in
`sessionStorage`; Client-Secrets und Sessiontokens werden dort nie abgelegt.
Die Connected- und Finish-Endpunkte sind tenantgebunden und idempotent.
Generation-ID, `AbortController` und eine gemeinsame Cleanup-Promise stellen
sicher, dass eine verspätete Providerantwort keinen neueren Start
überschreiben kann und Ressourcen genau einmal geschlossen werden.

## Automatisierte Abnahme

Provider und Kalender werden in Tests vollständig gefakt. Die Suite enthält:

- einen Vertragstest gegen die installierte Agents-SDK-Version für
  `function_call_output`, unveränderte `call_id` und genau eine Folgeantwort
- 20 deterministische vollständige Tool-Happy-Paths
- alle Kalenderwerkzeuge bei Erfolg und kontrolliertem Fehler
- keine Slots, Providerfehler und abgelehntes `response.create`
- doppelten Tool-Call, verzögertes Tool und neue Nutzerrunde während des Tools
- Server-VAD, Unterbrechung, echtes Playback-Ende, Stummschaltung und Cleanup
- Bestätigungssätze wie „Ja bitte“ sowie Buchungs- und Provider-Idempotenz

Automatisierte Tests öffnen keine echte OpenAI-Verbindung und erzeugen keine
echten Kalendertermine.

## Kurzer manueller Test

Voraussetzungen sind ein nur im Backend gesetzter `OPENAI_API_KEY`, ein
gestarteter Compose-Stack, Modellzugriff und Browser-Mikrofonfreigabe.

1. `docker compose up --build` starten.
2. `http://localhost:5173/testgespraech` öffnen und anmelden.
3. Testgespräch starten und die einmalige Begrüßung abwarten.
4. Eine Terminart sowie Datum und Uhrzeit nennen.
5. Prüfen, dass der Agent nach jeder Kalenderprüfung selbständig weiterspricht.
6. Eine Alternative auswählen, Kontaktdaten nennen und die Zusammenfassung mit
   „Ja bitte“ bestätigen.
7. Prüfen, dass eine Bestätigung nur bei erfolgreicher externer Buchung erfolgt.
8. Während einer Antwort sprechen und Barge-in prüfen.
9. Gespräch beenden und kontrollieren, dass der Browser-Mikrofonindikator
   erlischt.

Ein echter Kalender- oder OpenAI-Test verursacht externe API-Aufrufe und
möglicherweise Kosten. Er gehört deshalb nicht in die automatisierte Suite.

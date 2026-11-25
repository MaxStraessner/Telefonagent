# Anrufbeantworter: E-Mail und Gesprächsübersicht

Die bestehende Nachrichtenaufnahme wird um optionalen SMTP-Versand je Unternehmen erweitert. Telefonnummer, Twilio-Routing, Kalenderintegration und Sprachmodell bleiben erhalten. Es wird kein n8n-/Make-Workflow benötigt.

## Bedienung

1. Unter **Unternehmen → Name und Kontakt** den Unternehmensnamen oder bei einem Einzelunternehmen den eigenen Vor- und Nachnamen eintragen und speichern. Eine Namensänderung übernimmt der Assistent für neue Gespräche.
2. Unter **KI konfigurieren → Identität** den Betriebsmodus **Intelligenter Anrufbeantworter** wählen. Das Feld **Name im Gespräch** zeigt, für wen der Assistent Nachrichten entgegennimmt.
3. Die **Nachrichtenbegrüßung** festlegen und **Änderungen speichern** wählen. Zum Beispiel „Guten Tag, hier ist der Anrufbeantworter von {Name}. Welche Nachricht möchten Sie hinterlassen?“ Unter **Testen** erscheint die gespeicherte Begrüßung mit eingesetztem Namen.
4. Wer eine Zusammenfassung per E-Mail erhalten möchte, trägt unter **Nachrichten per E-Mail** eine feste Empfängeradresse ein und aktiviert den Versand. Dafür muss SMTP auf dem Server eingerichtet sein.
5. Neue Anrufe erscheinen unter **Anrufe** mit Zusammenfassungsvorschau und E-Mail-Status. Im Anrufdetail stehen die vollständige Auswertung und eine Transkripttabelle mit Zeitpunkt, Sprecher, Inhalt und Bestätigungsstand.

Im vollständigen Telefonagenten bleibt die Historie verfügbar; dieser Modus versendet keine neuen Anrufbeantworter-Mails. Bereits vor dem Umschalten gestartete Gespräche behalten ihren eingefrorenen Modus und ihre ursprüngliche Versandentscheidung. E-Mail-Einstellungen allein lösen keinen Versand aus und verschicken keine alten Anrufe nachträglich.

Die E-Mail enthält Zeitpunkt, bekannte Anrufer-/Kontaktdaten, Hauptanliegen, Zusammenfassung, Rückrufbedarf und einen normalen Link zum Anrufdetail. Dieser Link gewährt keine Anmeldung oder zusätzlichen Rechte. Das vollständige Transkript bleibt in der geschützten Plattform. Ein vom Anrufer genanntes E-Mail-Ziel oder eine KI-Ausgabe kann den fest konfigurierten Empfänger nicht überschreiben.

## SMTP vorbereiten

Die bestehenden serverseitigen Variablen verwenden; Zugangsdaten ausschließlich in der geschützten Serverkonfiguration pflegen, niemals im Frontend, Git oder Chat:

| Variable | Bedeutung |
| --- | --- |
| `MAIL_ENABLED` | Globaler Schalter; ohne vorbereiteten Versand `false` lassen |
| `SMTP_HOST`, `SMTP_PORT` | Mailserver und Port des bestehenden Postfachanbieters |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | SMTP-Zugang des vorhandenen Postfachs, ggf. Anbieter-App-Passwort |
| `SMTP_FROM_ADDRESS` | Vom Anbieter erlaubte Absenderadresse |
| `SMTP_STARTTLS=true`, `SMTP_USE_SSL=false` | STARTTLS, üblicherweise Port 587 |
| `SMTP_STARTTLS=false`, `SMTP_USE_SSL=true` | Alternativ implizites TLS, üblicherweise Port 465 |
| `FRONTEND_URL` | Bestehende öffentliche HTTPS-Plattformadresse für den Detail-Link |

Beide TLS-Wege prüfen Zertifikatskette und Hostnamen mit dem System-Vertrauensspeicher. SMTP-Sockets haben ein Zeitlimit von zehn Sekunden. In Produktion ist unverschlüsselter Mailversand nicht zulässig. Eine positive Konfigurationsanzeige prüft die vorhandenen Einstellungen, nicht die tatsächliche Anmeldung, Absenderfreigabe oder Inbox-Zustellung beim Anbieter. Der globale Mailtransport wird auch für Einladungen und Passwort-Recovery genutzt.

## Verarbeitung und Grenzen

Die Warteschlange liegt auf den vorhandenen mandantengeschützten `call_sessions`. Bei einem neuen Telefonanruf im aktivierten Anrufbeantwortermodus werden Empfänger und Versandentscheidung gespeichert. Erst ein abgeschlossenes, erfolgreich ausgewertetes Gespräch mit bestätigtem Anruferbeitrag darf versendet werden. Eine reine Begrüßung oder ein leeres Transkript erzeugt keine erfundene Nachricht. Endgültige Analysefehler bleiben sichtbar und erzeugen keine KI-Zusammenfassungs-Mail.

Der bestehende Hintergrundworker beansprucht den Auftrag unter einer Zeilensperre, committed und übergibt SMTP an einen Thread. Während der SMTP-Verbindung hält er keine Zeilensperre. Weitere Worker sehen den bereits beanspruchten Auftrag. Nur eindeutig vorübergehende Fehler werden automatisch wiederholt, höchstens drei Zustellversuche mit Wartezeiten. Bereits versendete Nachrichten werden bei späteren Transkriptkorrekturen nicht erneut automatisch zugestellt; noch wartende Nachrichten benötigen die aktuelle fertige Auswertung.

Bei Verbindungsabbruch während der Übergabe oder verlorenem Worker nach Beanspruchung ist der Status **unklar**: SMTP bietet keine garantierte Exactly-once-Zustellung. Es erfolgt keine automatische Wiederholung. Administratoren können nach Postfachprüfung im Anrufdetail einen erneuten Versand ausdrücklich vorbereiten und bestätigen; eine doppelte E-Mail bleibt dabei möglich. Die Wiederholung ist nur bei weiterhin passendem Empfänger, eingeschalteter Einstellung und fertiger Auswertung erlaubt.

**Vom Mailserver angenommen** bestätigt die SMTP-Annahme, keine Inbox-Zustellung. Bounce-/Spam-Verarbeitung ist nicht Bestandteil dieser Erweiterung. Adressänderung oder Abschalten cancelt noch nicht beanspruchte Aufträge. Löschung/Aufbewahrungsfrist entfernt außerdem den gespeicherten Zustellempfänger. Eine bereits laufende SMTP-Übergabe lässt sich weder bei Konfigurationsänderung noch bei Löschung zurückrufen; bereits zugestellte E-Mail-Kopien liegen außerhalb der Plattform-Aufbewahrung.

## Migration und spätere Freigabe

Migration `0018` ergänzt zwei Konfigurationsspalten und sieben Zustellspalten sowie einen Index. Bestehende Einstellungen erhalten `call_email_enabled=false`, bestehende Anrufe `notification_status=not_requested`. Keine neue Tabelle, keine Änderung an RLS-Policies, Rollen oder der Workerfunktion `call_processing_tenant_ids()`. Die bekannten Abweichungen zwischen ORM-Metadaten und bestehendem Schema werden nicht automatisch angeglichen; kein Alembic-Autogenerate anwenden.

Der bisherige Anwendungscode kann nach 0018 mit den zusätzlichen Spalten weiterarbeiten. Bei einem späteren Rollout gelten unverändert `docs/deployment.md` und `ops/deploy.ps1`: vorher PostgreSQL-Backup/Prüfung, aktive Gespräche auslaufen lassen, Migration mit Migrator-Rolle, Workerfunktion/RLS-Rechte und Healthchecks prüfen. Bei Rückkehr zu bisherigen Anwendungsimages kein destruktives Datenbankdowngrade durchführen. Wartende E-Mails würden während eines solchen App-Rollbacks nicht verarbeitet; bereits beanspruchte unklare Zustellungen dürfen nicht blind erneut eingeplant werden.

## Reale Abnahme nach gesondert freigegebenem Rollout

Kein Kauf einer Nummer und keine zweite öffentliche Umgebung nötig. Nach SMTP-Einrichtung und Aktivierung die bestehende Nummer selbst anrufen:

1. KI-/Transkriptionshinweis vollständig anhören, Begrüßung abwarten. Namen, Anliegen und Rückrufnummer nennen; einen Termin verlangen. Es darf im Anrufbeantwortermodus keine Kalenderaktion stattfinden.
2. Die KI einmal unterbrechen, eine Angabe korrigieren und eine Rückfrage beantworten. Rückrufwunsch eindeutig formulieren. Höflichen Abschluss abwarten und selbst auflegen.
3. Nach kurzer Verarbeitung **Anrufe → neues Gespräch** öffnen. Korrigierte Angaben, Sprecherfolge, Zusammenfassung, Rückrufbedarf und fehlende Kalenderbuchung prüfen. Genau eine Mail im vorgesehenen Postfach erwarten; deren Detail-Link muss dieselben Daten nach Anmeldung anzeigen.
4. Zweiten Anruf ohne Nachricht abbrechen: keine erfundene Zusammenfassung und keine Nachrichten-Mail erwarten. Einen weiteren Anruf mit unvollständigen Angaben prüfen: Unbekanntes muss unbekannt bleiben.
5. Im vollständigen Telefonagenten die bisherige Kalenderfunktion separat nach bestehender Abnahmeanleitung testen; keine Anrufbeantworter-Mail erwarten.

Die Sprachqualität, Erkennung echter Unterbrechungen, Telefonielatenz und tatsächliche Postfachzustellung sind durch lokale Provider-Simulationen nicht bewiesen. GPT Realtime 2.1 bleibt aktiv; GPT Live wird durch diese Änderung nicht aktiviert.

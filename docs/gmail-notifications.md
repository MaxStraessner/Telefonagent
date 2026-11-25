# Gesprächsberichte über Gmail

Unter **Benachrichtigungen** lassen sich die Empfängeradresse und die Abschnitte der E-Mail einstellen. **Mit Google verbinden** öffnet die Google-Freigabe. Nach der Zustimmung wird der Versand für neue Telefonate des ausgewählten Unternehmens aktiviert. Wenn im Benutzerkonto eine gültige E-Mail-Adresse hinterlegt ist, erscheint sie als Vorschlag für den Empfänger. Ein anderer Empfänger kann vor der Verbindung eingetragen werden.

Der Bericht gilt für beide Betriebsmodi und verwendet die gespeicherte Gesprächshistorie und KI-Auswertung. Bei endgültig fehlender Auswertung enthält der Bericht die verfügbaren Gesprächsdaten. Der optionale Kostenabschnitt zeigt die reinen Twilio-Verbindungskosten, falls Twilio sie zum Versandzeitpunkt bereitstellt. [Twilio Call Resource](https://www.twilio.com/docs/voice/api/call-resource)

Wenn mehrere Unternehmensadmins Google verbunden haben, versendet jeweils ein Benutzerkonto die Berichte. Die Aktivierung eines anderen Kontos wechselt den Absender für neue Anrufe. Änderungen an Empfänger, Abschnitten oder Verbindung machen bereits vorgemerkte, noch nicht übergebene Berichte mit der vorherigen Konfiguration ungültig. Für Anrufe, die mit aktivem Gmail-Versand beginnen, ist nur Gmail als Versandweg hinterlegt. Die separat eingerichteten SMTP-Benachrichtigungen können für andere Anrufbeantworter-Anrufe weiterhin greifen.

Der Versandworker prüft die aktive Berechtigung des Absenders und den ursprünglichen Empfänger. Bei eindeutig abgelehnten, vorübergehenden Fehlern wiederholt er den Versuch mit Wartezeit bis zu insgesamt drei Versuchen. Wenn Gmail die Annahme möglicherweise bereits verarbeitet hat, erscheint in der Anrufhistorie **E-Mail-Zustellung unklar** und der Worker sendet die Nachricht nicht erneut. Bereits zugestellte E-Mails unterliegen den Aufbewahrungseinstellungen des Empfängerpostfachs. **Google-Konto trennen** entfernt den lokal gespeicherten Zugang und beendet den Gmail-Versand.

## Google-Projekt

Die Gmail-Anbindung verwendet den bestehenden Web-OAuth-Client aus `GOOGLE_CALENDAR_CLIENT_ID`, `GOOGLE_CALENDAR_CLIENT_SECRET` und `GOOGLE_CALENDAR_REDIRECT_URI`. Für den Gmail-Flow genügt die vorhandene Google-Callback-URL. Google muss im selben Projekt die Gmail API sowie die Autorisierung für die vorgesehenen Konten zulassen. Der Flow fordert ausschließlich `https://www.googleapis.com/auth/gmail.send` an. [Gmail-Scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)

Der vorhandene `CALENDAR_TOKEN_ENCRYPTION_KEY` verschlüsselt den Gmail-Refresh-Token vor der Speicherung. Access-Tokens bleiben im Arbeitsspeicher. Der Einmalstatus ist an die angemeldete Browsersitzung und das ausgewählte Unternehmen gebunden. In vorgeschalteten Proxy-Logs sollten OAuth-Callback-Queryparameter ebenfalls unterdrückt bleiben.

Bei einem separaten Gmail-Client werden `GMAIL_CLIENT_ID` und `GMAIL_CLIENT_SECRET` gemeinsam gesetzt. In Google ist die URL aus `APP_BASE_URL` mit dem Pfad `/api/v1/calendar/oauth/google/callback` als Redirect-URI zu registrieren. Google Calendar behält seinen eigenen Client. Bei einem Wechsel des Gmail-Clients muss das sendende Konto neu verbunden werden. Für alleinige private Nutzung kann ein separates Google-Cloud-Projekt im Personal Mode verwendet werden. [Google Auth Platform](https://support.google.com/cloud/answer/15544987)

## Abnahme

1. Als Unternehmensadmin **Benachrichtigungen** öffnen und einen kontrollierten Testempfänger speichern.
2. Das gewünschte Google-Konto verbinden und die Sendeberechtigung bestätigen.
3. Einen Testanruf abschließen. Unter **Anrufe** den E-Mail-Status und im Empfängerpostfach die ausgewählten Abschnitte des Gesprächsberichts prüfen.
4. Nach Bedarf das vollständige Transkript abwählen und den nächsten Bericht auf die geänderte Auswahl prüfen.

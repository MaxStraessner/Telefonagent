# Lokale Abnahme: Anrufbeantworter, E-Mail und Historie

Stand: 23. November 2026. Feature-Branch `codex/answering-machine-email-inbox`, Basis `f56460238af6941652727231183654e21ba7ec70`. Umsetzung und lokale Abnahme; kein Push, Merge, Produktivdeployment, echter Telefonanruf oder echter Mailversand.

## Umgesetzt

- Bestehende KI-Nachrichtenaufnahme mit optionaler fester E-Mail-Adresse pro Unternehmen erweitert; Bearbeitung nur durch berechtigte Administratoren.
- Nur neue Telefonanrufe im aktivierten Anrufbeantwortermodus werden nach abgeschlossener KI-Auswertung versendet. Alte Anrufe und der vollständige Agent lösen keinen neuen Nachrichtenversand aus.
- Mail enthält Zusammenfassung, Kontaktdaten, Rückrufbedarf und geschützten Detail-Link; vollständiges Transkript bleibt in der Plattform.
- Bestehender Hintergrundworker verwendet persistente Zustellfelder, konkurrierende Claims und SMTP außerhalb der Audiokette bzw. des Eventloops. Kontrollierte Wiederholungen, sichtbare unklare Zustellungen und ausdrückliche Admin-Wiederholung.
- Adresswechsel/Abschalten cancelt wartende Aufträge. Aufbewahrung/Löschung entfernt auch den Zustellempfänger. Späte Transkriptkorrekturen führen bei bereits versendeten Mails zu keiner automatischen Zweitzustellung.
- Übersicht zeigt Zusammenfassungsvorschau und Mailstatus. Details enthalten die Transkripttabelle mit Zeit, Sprecher, Text und Bestätigungsstand. Abbruch vor Gesprächsbeginn wird als Anruf ohne Nachricht dargestellt.
- Additive Migration 0018 ohne Änderung bestehender RLS-Policies und ohne Schemaangleichung. STARTTLS und implizites SMTP-TLS prüfen Zertifikate und Hostnamen.

## Erfolgreich geprüft

| Prüfung | Ergebnis / Grenze |
| --- | --- |
| Vollständiger Backendlauf mit echtem PostgreSQL | Abschließend 321 Tests erfolgreich, darunter 20 PostgreSQL-Integrationstests |
| Mail- und Reihenfolgeprüfungen | 36 Mailtests im Gesamtlauf; zusätzlich 47 Mail-/Konfigurationstests in der zuvor problematischen Reihenfolge erfolgreich. Veralteten ORM-Zustand in der Testdatenbereinigung korrigiert |
| Frontend nach kompatiblem Sicherheitsupdate | 117 Tests in 12 Dateien erfolgreich, ESLint und TypeScript-/Vite-Build erfolgreich |
| Abhängigkeiten | npm meldete zunächst 11 moderate Befunde; kompatible Lockdatei-Updates, danach `npm ci`: 0 bekannte Sicherheitslücken |
| Deployment-Sicherheit | Alle acht bestehenden Linux-Tests erfolgreich, ohne Produktionsverbindung |
| Migration 0017 → 0018 | Vorhandene Transkripte, Zusammenfassungen, Modus-/Modellfelder und RLS-Einstellungen erhalten; historisch kein Mailauftrag, Unternehmen standardmäßig deaktiviert |
| Mandantentrennung und konkurrierender Versand | Unprivilegierte PostgreSQL-Runtime-Rolle: fremde Anrufe unsichtbar/unveränderbar, nur eine SMTP-Übergabe, Zeile während Netzwerkwartezeit nicht gesperrt |
| Vollständige Prozesskette | Signierter Twilio-HTTP-Webhook → WebSocket → simulierte OpenAI-Ereignisse → fortlaufende Speicherung → Hintergrundanalyse → Historie → simulierte SMTP-Zustellung; beide Betriebsmodi erfolgreich |
| Werkzeuggrenze | Anrufbeantworter hat keine freigegebenen Tools; eingeschleuste Kalenderaufrufe abgelehnt. Bisherige Agent-/Kalendertests erfolgreich |
| SMTP-Fehler | Bestätigte Annahme trotz QUIT-Fehler, temporäre/permanente Ablehnung, Timeout bei Übergabe, Worker-Verlust, Begrenzung auf drei Versuche, keine blinde Wiederholung unklarer Fälle |
| Inhaltsschutz | Ungültige/mehrfache/Header-Injection-Empfänger abgelehnt; Empfänger aus KI-Auswertung überschreibt Versandziel nicht; keine Geheimnisse oder Transkripte in Fehlercodes |
| Lokales HTTP-Lab | Health, Frontend, Login, beide Konfigurationen, Toollisten, Historie beider Modi und Cross-Company-404 erfolgreich; SMTP dort absichtlich deaktiviert |
| Hygiene | Ruff, Compose-Konfiguration und `git diff --check` erfolgreich |

Die PostgreSQL-Tests liefen ausschließlich gegen `telefonagent-local-acceptance` mit synthetischen Daten und temporärem PostgreSQL-17-Speicher. Backend und Frontend anderer lokaler Projekte wurden nicht verändert. Die bestehende Nummer und der VPS wurden in diesem Umsetzungsschritt nicht verändert.

## Verbleibende Grenzen und Risiken

1. **Echter SMTP-Zugang und Zustellung offen.** Vor Aktivierung das bestehende Postfach geschützt auf dem Server konfigurieren; Anmeldung, erlaubten Absender, Spamordner und Inbox-Zustellung real prüfen. Keine Passwörter im Chat eintragen.
2. **Echte Telefonie offen.** Lokale Stubs beweisen keine Spracherkennungsqualität, Telefonielatenz oder akustische Unterbrechung. Anleitung mit Rückfragen, Korrektur, fehlenden Angaben und Kalendergrenze: [answering-machine-email.md](answering-machine-email.md).
3. **SMTP-Grenze.** Annahme ist keine Zustellbestätigung. Eine bereits laufende Übergabe kann nicht zurückgerufen werden. Manuelle Wiederholung bei unklarem Status kann doppelt zustellen; deshalb ausdrückliche Bestätigung im Anrufdetail.
4. **Aufbewahrung.** E-Mails im externen Postfach werden durch Löschen der Plattforminhalte nicht entfernt. Bereits versendete Zusammenfassungen werden bei späteren Transkriptkorrekturen nicht automatisch ersetzt.
5. **Schemaabweichungen.** Bereits bekannte ORM-/Schemaunterschiede werden nicht durch 0018 repariert. Die Migration fügt nur neue Spalten und einen Index hinzu. Kein Autogenerate und kein destruktives Downgrade.
6. **Buildhinweise.** Bestehender großer Frontendchunk (~1,03 MB vor gzip), Zod-Kommentarhinweise und Starlette/httpx-Testdeprecation bleiben nicht blockierende Hinweise.
7. **Visuelle Prüfung blockiert.** Der In-App-Browser ist in diesem Chat nicht verfügbar. Die automatische Sicherheitsprüfung lehnte das vorhandene private Edge-Fenster ab. Daher keine visuelle Browser-Abnahme behauptet; DOM-Komponententests und echtes HTTP-Lab bestanden.

## Freigabestand

Implementierung und lokale technische Abnahme sind abgeschlossen. Für einen Produktivrollout dieser Erweiterung sind gesonderte Freigabe, Standarddeployment von geprüftem `main`, verifiziertes PostgreSQL-Backup und aktuelle VPS-/Drain-/RLS-/Health-Prüfungen erforderlich. Für die vollständige fachliche Abnahme danach SMTP-Zustellung und reale Gespräche mit der vorhandenen Nummer selbst testen. GPT Realtime 2.1 bleibt unverändert; GPT Live wurde nicht aktiviert.

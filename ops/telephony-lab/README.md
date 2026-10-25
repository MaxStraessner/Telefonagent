# Isolierte lokale Abnahmeumgebung

Dieselbe Anwendung und dieselben Migrationen wie im Feature-Branch, mit eigener PostgreSQL-17-Datenbank im RAM, eigenem Netzwerk, unprivilegierter Runtime-Rolle und ausschließlich synthetischen Daten. **Keine echte Telefonie:** Anbieterzugänge sind absichtlich leer; es gibt keinen öffentlichen Tunnel, keine Twilio-Synchronisation und keinen verbundenen Kalender.

Die Datei ist ausschließlich für den lokalen Rechner bestimmt. Sie darf weder als Produktionsüberlagerung noch mit einem anderen Projektnamen verwendet werden. Die bestehende Produktionsroute bleibt unverändert `ops/deploy.ps1` nach ausdrücklicher Freigabe.

Aus dem Repository:

```powershell
docker-compose -f ops/telephony-lab/compose.yaml config --quiet
docker-compose -f ops/telephony-lab/compose.yaml build migrate frontend
docker-compose -f ops/telephony-lab/compose.yaml up -d --wait backend frontend
```

Frontend: `http://localhost:28081`, Backend: `http://localhost:28001/api/v1/health`. Anmeldung mit dem **ausschließlich synthetischen Offline-Zugang** aus dem `bootstrap`-Abschnitt der Compose-Datei. Im Plattformmodus das Unternehmen „Salon Haarkunst Test“ auswählen. Beide Betriebsmodi lassen sich in dessen Einstellungen prüfen. Reale Browser-/Telefoniesitzungen können ohne Anbieterzugang nicht aufgebaut werden.

Ports: ausschließlich Loopback, PostgreSQL 25439, Backend 28001, Frontend 28081. Projekt `telefonagent-local-acceptance`. Kein Traefik-Netz, keine Produktionslabels, keine benannten Datenvolumes und kein Einlesen der normalen `.env` in die Container. Die bekannten Passwörter in dieser Datei sind synthetische Offline-Testwerte, keine wiederverwendbaren Zugangsdaten.

**Die PostgreSQL-Daten gehen beim Stoppen des Datenbankcontainers verloren.** Dieses Lab ist für wiederholbare Prüfungen bestimmt, nicht zur Archivierung echter Anrufnachweise. Eine spätere echte Telefonieabnahme benötigt eine separat freigegebene Testadresse, Testnummer, geschützte Konfiguration und eine festgelegte Aufbewahrung der Abnahmenachweise. Die bestehenden Produktivzugänge dürfen nicht einfach in dieses Offline-Lab kopiert werden.

Vor einem erneuten Start prüfen, dass die Ports frei sind und kein anderer Chat dieses Lab nutzt. Nur Ressourcen dieses eigenen Labs aufräumen; kein `down -v`, keine Volume-Löschung und keine Eingriffe in andere Compose-Projekte.

Die PostgreSQL-Integrationstests dürfen nur gegen die synthetische Lab-Datenbank laufen, während Backend und Bootstrap nicht aktiv sind. Die Tests enthalten bewusst einen Migration-Roundtrip und sind für Kunden-/Produktionsdatenbanken ungeeignet.

HTTP-Smoke-Test aus dem Repository: `backend\.venv\Scripts\python.exe ops/telephony-lab/smoke.py`. Er prüft Anmeldung, Kontextwahl, beide Einstellungen und trockene Werkzeuglisten und stellt die ursprüngliche Einstellung wieder her. Mit `--require-history-fixtures` müssen zusätzlich die synthetischen Daten der beiden PostgreSQL-Prozessketten vorhanden sein. Nach einem Migration-Roundtrip diese zwei Prozessketten gezielt erneut ausführen, bevor das Lab-Backend startet: der Roundtrip entfernt definitionsgemäß die neuen 0017-Modusfelder der zuvor erzeugten Testdaten. Das ist kein Fehler der Historienanzeige.

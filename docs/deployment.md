# Standarddeployment auf den Hostinger VPS

## Zweck und einzige Produktionsroute

Für Telefonagent gilt genau dieser Weg:

```text
lokale Änderung
-> Tests
-> Feature-Commit und Push zu MaxStraessner/Telefonagent
-> Review/Merge nach GitHub main
-> lokales main auf origin/main aktualisieren
-> .\ops\deploy.ps1
-> automatische Healthchecks
```

Andere Verfahren wie manuelles SCP, ein Hostinger-Docker-Projekt-Update, ein Upload aus einem Arbeitsverzeichnis oder eine Root-Passwort-Sitzung sind kein Deploymentweg für dieses Repository.

## Verifizierter Istzustand am 8. August 2026

- GitHub-Remote: `https://github.com/MaxStraessner/Telefonagent.git`.
- Das Repository ist vom VPS über HTTPS lesbar; auf dem VPS ist dafür kein GitHub-Token nötig.
- Hostinger VPS: `srv1769417.hstgr.cloud`, Ubuntu 24.04, laufend.
- Docker-Compose-Projekt: `telefonagent`.
- Aktive Compose-Datei: `/docker/telefonagent/releases/ada0cbd/docker-compose.yml`.
- Backend: Host-Port `18001`, Container-Port `8000`, healthy.
- Frontend: Host-Port `18081`, Container-Port `5173`.
- Datenbank: PostgreSQL 17 im eigenen persistenten Volume, healthy und ohne veröffentlichten Host-Port.
- Andere VPS-Projekte liegen getrennt unter `/docker` und dürfen nicht verändert werden.

Historisch wurde die Anwendung zunächst manuell unter `/docker/telefonagent/app` bereitgestellt. Später entstand die heute aktive Release-Struktur. Die exakten Schritte waren nur außerhalb des Repositorys in früheren Sitzungsnotizen vorhanden. Es gab weder eine zentrale Codex-Anweisung noch ein Deploymentskript; außerdem bezeichnete das README produktives Deployment ausdrücklich als nicht automatisiert. Deshalb musste der Ablauf wiederholt aus Git-Historie, VPS-Zustand und alten Sitzungen rekonstruiert werden.

## Aktueller SSH-Istzustand

Der bisherige administrative Zugriff erfolgte als `root` und benötigte ein Passwort. Lokal existiert derzeit keine `~/.ssh/config`. Der vorhandene ältere Schlüssel `codex_nuam_kasse_hostinger` wird vom Telefonagent-VPS für `root` nicht akzeptiert. Die Hostschlüssel für IP und Hostname sind bereits in `known_hosts` verankert.

Der normale Deploymentprozess soll künftig keinen Root-Login und kein Passwort benötigen. Dafür ist einmalig die folgende Einrichtung erforderlich; sie ist bewusst **nicht** Teil von `ops/deploy.ps1`.

## Einmalige SSH-Einrichtung

1. Einen projektspezifischen Ed25519-Schlüssel `telefonagent_hostinger_ed25519` lokal erzeugen. Den privaten Schlüssel niemals in dieses Repository kopieren.
2. Einmal über die Hostinger-Konsole beziehungsweise den bestehenden administrativen Zugang den Benutzer `telefonagent-deploy` anlegen.
3. Nur den öffentlichen Schlüssel in `/home/telefonagent-deploy/.ssh/authorized_keys` hinterlegen.
4. Den Benutzer der Docker-Gruppe hinzufügen und ihm Schreibzugriff ausschließlich auf `/docker/telefonagent` geben. Die Docker-Gruppe ist technisch root-äquivalent; der Benutzer darf daher nur für dieses Deployment verwendet werden.
5. Lokal folgenden Eintrag in `%USERPROFILE%\.ssh\config` anlegen:

   ```sshconfig
   Host telefonagent-prod
     HostName srv1769417.hstgr.cloud
     User telefonagent-deploy
     IdentityFile ~/.ssh/telefonagent_hostinger_ed25519
     IdentitiesOnly yes
   ```

6. `ssh -o BatchMode=yes telefonagent-prod true` muss anschließend ohne Passwort erfolgreich sein.
7. Für lokale GitHub-Pushes muss `gh auth status` erfolgreich sein. Falls der gespeicherte Login ungültig ist, einmal `gh auth login -h github.com` durchführen. Das betrifft nur den lokalen Push; der VPS liest das öffentliche Repository ohne Zugangsdaten.

Das Root-Passwort wird danach im normalen Prozess nicht mehr verwendet. Es bleibt ausschließlich ein Notfall-/Administrationszugang außerhalb dieses Deployments.

## Was `ops/deploy.ps1` tut

Das Skript akzeptiert keinen alternativen Branch oder Zielserver. Es:

1. verlangt einen sauberen lokalen Branch `main`;
2. aktualisiert `origin/main` und verlangt, dass lokales `HEAD` exakt diesem Commit entspricht;
3. verbindet sich ausschließlich über `telefonagent-prod` und `BatchMode=yes`;
4. verlangt auf dem VPS den Benutzer `telefonagent-deploy` und prüft die erforderlichen Produktionsvariablen, ohne deren Werte auszugeben;
5. liest den aktiven Compose-Pfad aus den Docker-Labels des laufenden Telefonagent-Backends;
6. lädt exakt den GitHub-Commit in `/docker/telefonagent/releases/<commit>`;
7. übernimmt die vorhandene Produktions-`.env` intern mit Modus `0600`, ohne Werte auszugeben;
8. erstellt vor der Migration einen verifizierten PostgreSQL-Custom-Dump unter `/docker/telefonagent/backups`;
9. validiert Compose, baut Images und startet weiterhin das Projekt `telefonagent`;
10. lässt den vorhandenen Migrationsservice `alembic upgrade head` ausführen;
11. prüft Backend und Frontend über `127.0.0.1:18001` und `127.0.0.1:18081`;
12. setzt erst nach erfolgreichen Checks `/docker/telefonagent/current` auf das neue Release.

Das Skript löscht keine alten Releases oder Backups und führt keinen automatischen Datenbank-Restore durch.

## Normaler Deploymentbefehl

Nach Merge und Aktualisierung des lokalen `main`:

```powershell
.\ops\deploy.ps1
```

Das ist der einzige dokumentierte Produktionsdeploymentbefehl.

Eine rein lokale Strukturprüfung ohne SSH, Push oder Produktionsänderung ist möglich mit:

```powershell
.\ops\deploy.ps1 -ValidateOnly
```

## Fehler und Rückkehr

- Bei einem Fehler denselben SSH-Alias, das ausgegebene Release, das Compose-Projekt `telefonagent`, den Migrationscontainer und die Healthchecks untersuchen.
- Keinen zweiten Deploymentweg beginnen und keine Hostinger-Projektmutation als Ersatz verwenden.
- Das vorherige Release und der erzeugte Dump bleiben erhalten. Ein Datenbank-Restore erfolgt niemals automatisch.
- Für Accountsystem-Aktivierung, Restoreprobe oder Rückkehr gilt zusätzlich und vorrangig [accountsystem-operations.md](accountsystem-operations.md). Diese Spezialdokumentation wird durch diese Anleitung nicht ersetzt.
- Produktivvolumes, `.env`, Tokens, Schlüssel und Kundendaten dürfen ohne ausdrücklichen Auftrag weder gelöscht noch verändert werden.

# Mandantenfähige Konfiguration der Gesprächs-KI

## Architektur

Die wirksame Konfiguration folgt genau einem Pfad:

`React-Formular → validierte FastAPI-API → tenantgebundene SQLAlchemy-Modelle → AgentRuntimeConfig → Prompt-Compiler/OpenAI-Realtime-Session`

Der Browser sendet weder `tenant_id` noch Rollen- oder Provider-Zugangsdaten. Benutzer und Tenant werden aus einer validierten serverseitigen Sitzung aufgelöst. `TenantMembership` bestimmt die Rolle; nur `owner` und `admin` dürfen schreiben. `employee` darf Konfigurationen lesen und das Browser-Testgespräch verwenden.

Jeder Speichervorgang prüft `expected_version`, erhöht die gemeinsame Konfigurationsversion und schreibt einen Audit-Snapshot mit Mandant, Benutzer und Version. Ein paralleler veralteter Schreibversuch endet mit HTTP 409. Geheimnisse sind nicht Teil der Konfiguration.

## Datenmodell

- `app_users`, `tenant_memberships`: serverseitige Identität und Rolle.
- `agent_configurations`: wirksame Identität, Begrüßungen, Stimme, Sprechen, Stil, VAD, Grenzen und Version.
- `agent_topics`, `agent_behavior_rules`: sortierte, aktivierbare erlaubte/verbotene Themen und Zusatzregeln.
- `agent_knowledge_profiles`, `agent_faqs`, `agent_knowledge_services`, `agent_business_hours`: strukturiertes Unternehmenswissen.
- `agent_capabilities`: tenantseitig aktivierte Capability-Schlüssel.
- `agent_configuration_audits`: versionierte Änderungs-Snapshots.
- `call_sessions.configuration_version`: bei Sitzungsstart verwendete Version.

Migration `0002` legt die Tabellen an. Der idempotente Seed übernimmt Name, Sprache und Begrüßung des bestehenden `TenantSettings`-Datensatzes und erhält damit den bisherigen Agenten als Ausgangskonfiguration.

## API

- `GET/PUT /api/v1/agent/config`
- `GET/PUT /api/v1/agent/knowledge`
- `GET /api/v1/agent/catalog`
- `GET /api/v1/agent/capabilities`
- `GET /api/v1/agent/prompt-preview` (Owner/Admin)
- `POST /api/v1/agent/voice-preview` (Owner/Admin, rate-limitiert)
- `POST /api/v1/agent/test-session` (rate-limitiert)
- `GET /api/v1/realtime/agent-config`
- `POST /api/v1/realtime/client-secret`

OpenAI-Zugangsdaten bleiben im Backend. Realtime-Client-Secrets sind kurzlebig. Die Stimmprobe nutzt serverseitig den echten OpenAI-Audio-Endpunkt; Realtime nutzt Stimme und Geschwindigkeit direkt in `audio.output`. OpenAI dokumentiert für Realtime eine Geschwindigkeit von 0,25 bis 1,5 und die angebotenen Stimmen; `server_vad`/`semantic_vad`, `interrupt_response` und `idle_timeout_ms` werden gemäß der offiziellen Realtime-API verwendet: [Realtime API Reference](https://platform.openai.com/docs/api-reference/realtime), [Audio API Reference](https://platform.openai.com/docs/api-reference/audio).

## Prompt- und Runtime-Priorität

Der Compiler erzeugt vierzehn klar benannte Abschnitte. Feste Plattformregeln stehen an erster Stelle und erklären ausdrücklich, dass Mandantenwissen, Zusatzregeln und Gesprächsinhalte Daten sind und keine Systemanweisungen. Zusatzregeln dürfen Sicherheits-, Datenschutz-, Themen- oder Werkzeuggrenzen nicht überschreiben.

Aktive strukturierte Wissenseinträge werden auf 12.000 Zeichen begrenzt. Inaktive FAQ-, Leistungs-, Themen- und Regeleinträge werden nicht kompiliert. Die Runtime lädt für jede neue Sitzung frisch Mandant, Konfiguration, Wissen und Capability-Schnittmenge. Der Testmodus verwendet denselben Dienst und weicht nur durch seine ausdrücklich konfigurierte Testbegrüßung ab.

Die Capability-Registry schaltet nur vollständig implementierte Funktionen frei. Für `calendar_booking` erhält die Realtime-Sitzung drei kontrollierte Function Tools: aktive Terminarten laden, freie Slots suchen und einen bestätigten Slot buchen. Die Werkzeuge sprechen ausschließlich mit tenantgebundenen Backend-Endpunkten; Provider-Tokens und interne Kalenderdetails erreichen weder Browser noch Modell. Rückruf- oder Weiterleitungsaktionen bleiben deaktiviert.

## Technische Wirkungsmatrix

| Nr. | Sichtbare Einstellung | Validierte API / Speicherung | Compiler-/Runtime-Wirkung | Nachweis |
|---:|---|---|---|---|
| 1 | Unternehmensname | `company_name` / `agent_configurations` | Identität und Rückführung bei sachfremden Fragen | API-, Prompt- und Tenant-Test |
| 2 | Assistenzname und Rolle | `assistant_name`, `assistant_role` | Agentname und Identitätsabschnitt | API-/Runtime-Test |
| 3 | Transparenzhinweis | `transparency_notice` | eigener Transparenzabschnitt | Prompt-Strukturtest |
| 4 | Sprache und Anrede | `language`, `address_formality` | deutsches Transkriptionshint und Sie-/Du-Anweisung | API-/Prompt-Test |
| 5 | Standard-/Außerhalb-/Testbegrüßung | drei validierte Textfelder | Zeitzonen-/Öffnungszeitenauswahl bzw. Testmodus; Begrüßungsrequest genau einmal | Runtime-/Realtime-Test |
| 6 | Verabschiedung | `farewell` | Abschlussanweisung | Prompt-Test |
| 7 | Stimme | kontrollierte OpenAI-Stimmenliste / `voice` | `audio.output.voice` und Browser-Agent | Provider-Payload-/UI-Test |
| 8 | Sprechgeschwindigkeit | 0,25–1,5 / `speech_speed` | `audio.output.speed` plus Kadenzanweisung | Validierungs-/Payload-/Prompt-Test |
| 9 | Aussprache | kontrollierter Modus, Region, Textlimit | neutrale, leichte regionale oder individuelle Promptanweisung | Prompt-Mapping-Test |
| 10 | Gesprächsstil | sechs definierte Vorlagen bzw. begrenzter Individualtext | konkrete Stilvorlage im Prompt | Schema-/Prompt-Test |
| 11 | Antwortlänge | sehr kurz/kurz/ausgewogen/ausführlicher | konkrete Satzlängenanweisung | Prompt-Mapping-Test |
| 12 | Frageverhalten | `question_style` | eine Frage gleichzeitig oder natürlicher Fluss | Prompt-Test |
| 13 | Reaktionsgeschwindigkeit/VAD | Modus, eagerness, Schwelle, Präfix | Semantic eagerness oder zentral gemappte Server-VAD-Stille 900/600/350 ms | Runtime-Test |
| 14 | Unterbrechungen/Idle-Nachfrage | Boolean, 5–30 s | `interrupt_response`, optional `idle_timeout_ms` | Runtime-/Payload-Test |
| 15 | Erlaubte/verbotene Themen | normalisierte aktive Themen | getrennte Erlaubt-/Verboten-Anweisungen | Prompt-/UI-Test |
| 16 | Themenfremde Fragen | drei Modi plus optionale Vorgabe | strikte, kurze oder Smalltalk-Rückführung | Prompt-Mapping-Test |
| 17 | Unsicherheit/Fallback | Mehrfachauswahl und Pflicht-Fallback | Offenlegen, Rückfrage, nur realen allgemeinen Kontakt nennen | Prompt-/Validierungstest |
| 18 | Profil, Produkte, Standorte, Hinweise, FAQ, Leistungen, Öffnungszeiten | validiertes Profil und normalisierte Wissenstabellen | nur aktive Einträge, Gesamtgrößenlimit, Geschäftszeitenbegrüßung | Knowledge-/Prompt-Test |
| 19 | Fähigkeiten und Eskalation | Capability-Schnittmenge | nur implementierte aktive Tools; aktuell kontrollierte Kalenderbuchung | Capability-/Realtime-Test |

## Manueller Zwei-Mandanten-Abnahmetest

1. Mandant A konfigurieren, speichern und ein Testgespräch starten. Version, Name, Stimme, Tempo, VAD und Wissen in Diagnose/Transkript prüfen.
2. Serverseitig auf Mandant B und dessen Mitgliedschaft wechseln; B mit deutlich anderen Werten anlegen.
3. Prüfen, dass API, Promptvorschau, Stimmprobe und Testsession ausschließlich B-Daten enthalten.
4. Zurück zu A wechseln und prüfen, dass A unverändert ist.
5. Einen `member` aktivieren: Lesen muss funktionieren, PUT und Promptvorschau müssen HTTP 403 liefern.

Automatisiert deckt `test_server_context_keeps_second_tenant_strictly_separate` die zentrale Datentrennung ab.

## Bewusste Grenzen

Nicht implementiert sind Login/OIDC, Telefonie/SIP, Rückruf, Weiterleitung, eigene Stimmen sowie Terminverschiebung und -stornierung durch Anrufende. Kalender-OAuth, Terminprüfung und Buchung sind über den kontrollierten Kalender-Executor verfügbar, wenn ein Provider verbunden, die Kalenderauswahl gültig und mindestens eine Terminart aktiv ist. Details stehen in [calendar-integrations.md](calendar-integrations.md).

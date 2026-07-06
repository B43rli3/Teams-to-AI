# Teams Local LLM

Lokaler Proof of Concept: Microsoft Teams Kanal-Nachrichten lesen, über ein lokales Ollama-LLM beantworten und die Antwort als Thread-Reply im Kanal veröffentlichen.

## Architekturübersicht

```
Microsoft Teams Kanal
        │
        ▼
Microsoft Graph Polling (kein Webhook)
        │
        ▼
Neue Kanalnachricht erkennen
        │
        ▼
HTML-Inhalt bereinigen (MessageParser)
        │
        ▼
Lokales Ollama-LLM aufrufen
        │
        ▼
Antwort validieren und als sicheres HTML formatieren
        │
        ▼
Thread-Reply über Microsoft Graph senden
        │
        ▼
Verarbeitete Nachrichten in SQLite speichern
```

### Komponenten

| Modul | Aufgabe |
|-------|---------|
| `app/auth.py` | MSAL Device-Code-Flow, Token-Cache |
| `app/graph_client.py` | Microsoft Graph REST API (httpx) |
| `app/teams_service.py` | Nachrichtenfilterung und Thread-Replies |
| `app/llm_client.py` | Ollama Chat API |
| `app/message_parser.py` | HTML-Bereinigung und Antwortformatierung |
| `app/repository.py` | SQLite-Persistenz (aiosqlite) |
| `app/worker.py` | Asynchroner Polling-Worker |
| `app/main.py` | FastAPI-Service (Monitoring) |
| `app/cli.py` | Kommandozeilen-Werkzeuge |

### Authentifizierung

- **Public Client** (kein Client Secret)
- **Device-Code-Flow** über MSAL Python
- Token-Cache in `data/msal_token_cache.json` (restriktive Dateirechte)
- Stille Token-Erneuerung bei nachfolgenden Starts

### Polling statt Webhook

Die Anwendung pollt Microsoft Graph in konfigurierbaren Intervallen. Dadurch sind kein Cloudflare Tunnel, ngrok oder öffentlich erreichbare Ports erforderlich.

---

## Voraussetzungen

- **Windows 11** (primäre Zielplattform)
- **Python 3.12**
- **Ollama** (lokal installiert)
- **Microsoft 365-Konto** mit Zugang zum Ziel-Team
- **Microsoft Entra App-Registrierung** (Public Client)

---

## Installation von Python 3.12

1. Laden Sie Python 3.12 von [python.org](https://www.python.org/downloads/) herunter.
2. Aktivieren Sie bei der Installation **"Add Python to PATH"**.
3. Prüfen Sie die Installation:

```powershell
python --version
# Python 3.12.x
```

---

## Virtuelle Umgebung erstellen (Windows PowerShell)

```powershell
cd teams-local-llm
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Falls die Skriptausführung blockiert ist:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

## Abhängigkeiten installieren

```powershell
pip install -e ".[dev]"
```

---

## Ollama installieren und starten

1. Laden Sie Ollama von [ollama.com](https://ollama.com) herunter und installieren Sie es.
2. Starten Sie den Ollama-Dienst (läuft normalerweise automatisch im Hintergrund).
3. Prüfen Sie die Erreichbarkeit:

```powershell
.\scripts\check_ollama.ps1
```

### Modell herunterladen

```powershell
ollama pull qwen3:14b
```

Alternativ ein kleineres Modell für Tests:

```powershell
ollama pull qwen3:8b
```

Passen Sie dann `OLLAMA_MODEL` in der `.env` an.

---

## Microsoft Entra App-Registrierung

### 1. App registrieren

1. Öffnen Sie das [Microsoft Entra Admin Center](https://entra.microsoft.com).
2. Navigieren Sie zu **Identität → Anwendungen → App-Registrierungen → Neue Registrierung**.
3. Name: z. B. `Teams Local LLM PoC`
4. Unterstützte Kontotypen: **Nur Konten in diesem Organisationsverzeichnis**
5. Umleitungs-URI: **leer lassen** (Public Client, kein Web-Redirect nötig)

### 2. Als Public Client aktivieren

1. Öffnen Sie die App-Registrierung.
2. Gehen Sie zu **Authentifizierung**.
3. Aktivieren Sie **Öffentliche Clientflows zulassen** → **Ja**.
4. Speichern.

### 3. Delegierte Graph-Berechtigungen hinzufügen

Unter **API-Berechtigungen → Berechtigung hinzufügen → Microsoft Graph → Delegierte Berechtigungen**:

| Berechtigung | Zweck |
|---|---|
| `User.Read` | Angemeldeten Benutzer ermitteln |
| `ChannelMessage.Read.All` | Kanalnachrichten lesen |
| `ChannelMessage.Send` | Thread-Antworten senden |
| `offline_access` | Refresh Token für stille Erneuerung |
| `Team.ReadBasic.All` | Optional: Discovery-Befehl |
| `Channel.ReadBasic.All` | Optional: Discovery-Befehl |

### 4. Admin Consent

Folgende Berechtigungen erfordern in den meisten Organisationen **Admin Consent**:

- `ChannelMessage.Read.All`
- `ChannelMessage.Send`
- `Team.ReadBasic.All`
- `Channel.ReadBasic.All`

Ein Global Administrator muss unter **API-Berechtigungen → Administratorzustimmung erteilen** klicken.

### 5. IDs notieren

Notieren Sie aus der Übersicht:

- **Anwendungs-ID (Client-ID)** → `AZURE_CLIENT_ID`
- **Verzeichnis-ID (Tenant-ID)** → `AZURE_TENANT_ID`

---

## Konfiguration

Kopieren Sie die Beispielkonfiguration:

```powershell
Copy-Item .env.example .env
```

Bearbeiten Sie `.env` und tragen Sie mindestens ein:

```env
AZURE_TENANT_ID=ihre-tenant-id
AZURE_CLIENT_ID=ihre-client-id
TEAMS_TEAM_ID=
TEAMS_CHANNEL_ID=
```

### Wichtige Einstellungen

| Variable | Standard | Beschreibung |
|---|---|---|
| `PROCESS_BACKLOG` | `false` | Historische Nachrichten beim ersten Start ignorieren |
| `TRIGGER_MODE` | `all` | `all`, `prefix` oder `mention` |
| `BOT_PREFIX` | `/ai` | Prefix für `TRIGGER_MODE=prefix` |
| `POLL_INTERVAL_SECONDS` | `10` | Polling-Intervall |
| `LLM_MAX_CONCURRENCY` | `1` | Parallele LLM-Anfragen |

> **Hinweis zu TRIGGER_MODE=all:** Verwenden Sie diesen Modus nur in einem eigens für den Assistenten vorgesehenen Kanal. In belebten Kanälen antwortet der Assistent sonst auf jede Nachricht.

---

## Team-ID und Channel-ID ermitteln

```powershell
# Anmeldung
python -m app.cli login

# Teams auflisten
python -m app.cli discover-teams

# Kanäle eines Teams auflisten
python -m app.cli discover-channels --team-id "<TEAM-ID>"
```

Tragen Sie die gewünschten IDs in `.env` ein.

---

## Erster Device-Code-Login

```powershell
python -m app.cli login
```

Die Anwendung zeigt eine URL und einen Code an:

```
============================================================
Microsoft-Anmeldung erforderlich
============================================================

To sign in, use a web browser to open the page https://microsoft.com/devicelogin
and enter the code XXXXXXXX to authenticate.
```

1. Öffnen Sie die URL im Browser.
2. Geben Sie den Code ein.
3. Melden Sie sich mit Ihrem Microsoft-365-Konto an.

Der Token-Cache wird in `data/msal_token_cache.json` gespeichert.

---

## Verbindungen testen

```powershell
# Angemeldeten Benutzer anzeigen
python -m app.cli whoami

# Graph-Verbindung testen
python -m app.cli test-graph

# Ollama testen
python -m app.cli test-ollama
```

---

## Anwendung starten

```powershell
.\scripts\start.ps1
```

Oder manuell:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

### Monitoring-Endpunkte

| Endpunkt | Beschreibung |
|---|---|
| `GET http://127.0.0.1:8080/health` | Einfacher Health-Check |
| `GET http://127.0.0.1:8080/ready` | Bereitschaft aller Abhängigkeiten |
| `GET http://127.0.0.1:8080/status` | Worker-Status und Statistiken |
| `POST http://127.0.0.1:8080/poll-now` | Sofortigen Poll auslösen |
| `GET http://127.0.0.1:8080/recent-errors` | Kürzliche Fehler |

---

## Testnachricht im Teams-Kanal

1. Öffnen Sie den konfigurierten Kanal in Microsoft Teams.
2. Senden Sie eine Nachricht (je nach `TRIGGER_MODE`):
   - `all`: beliebige Nachricht
   - `prefix`: Nachricht beginnend mit `/ai`, z. B. `/ai Was ist Python?`
   - `mention`: Nachricht mit Erwähnung des konfigurierten Benutzers
3. Warten Sie bis zum nächsten Poll (Standard: 10 Sekunden).
4. Die Antwort erscheint als **Thread-Reply** unter Ihrer Nachricht.

> **Wichtig:** Antworten erscheinen unter dem **Namen des angemeldeten Benutzers**, nicht als Bot. Für einen echten Bot-Namen und Bot-Avatar ist später das **Microsoft Teams Bot Framework** erforderlich.

---

## Verhalten beim ersten Start (ohne Backlog)

Mit `PROCESS_BACKLOG=false` (Standard):

1. Beim ersten erfolgreichen Polling werden alle vorhandenen Kanalnachrichten als `seen` markiert.
2. Sie werden **nicht** an das LLM weitergegeben.
3. Erst danach eingehende neue Nachrichten werden verarbeitet.

So werden unbeabsichtigte Massenantworten auf historische Nachrichten verhindert.

Mit `PROCESS_BACKLOG=true` werden maximal `BACKLOG_LIMIT` (Standard: 5) historische Nachrichten chronologisch verarbeitet.

---

## Trigger-Modi

### `all` (Standard)

Jede neue Nachricht des Zielkanals wird verarbeitet. Nur für dedizierte Assistenten-Kanäle empfohlen.

### `prefix`

Nur Nachrichten, deren Text mit `BOT_PREFIX` beginnt (Standard: `/ai`). Der Prefix wird vor dem LLM-Aufruf entfernt.

### `mention`

Nur Nachrichten mit einer Teams-Erwähnung des in `BOT_MENTION_ID` konfigurierten Benutzers. Die Prüfung erfolgt über das `mentions`-Array der Graph-API.

---

## Fehlerbehebung

### HTTP 401 (Nicht autorisiert)

- Token abgelaufen: Die Anwendung versucht automatisch eine Erneuerung.
- Falls das fehlschlägt: `python -m app.cli login`
- Löschen Sie ggf. `data/msal_token_cache.json` und melden Sie sich erneut an.

### HTTP 403 (Zugriff verweigert)

- Prüfen Sie die delegierten Graph-Berechtigungen in der App-Registrierung.
- Stellen Sie sicher, dass **Admin Consent** erteilt wurde.
- Prüfen Sie, ob Ihr Konto Zugriff auf das Team und den Kanal hat.

### HTTP 404 (Nicht gefunden)

- Prüfen Sie `TEAMS_TEAM_ID` und `TEAMS_CHANNEL_ID` mit den Discovery-Befehlen.
- Kanal-IDs enthalten oft Sonderzeichen (`@`, `:`) – kopieren Sie die ID vollständig.

### HTTP 429 (Rate Limit)

- Die Anwendung wartet automatisch gemäß `Retry-After`-Header.
- Erhöhen Sie `POLL_INTERVAL_SECONDS` bei häufigen Rate Limits.

### Ollama-Timeouts

- Prüfen Sie, ob Ollama läuft: `.\scripts\check_ollama.ps1`
- Erhöhen Sie `OLLAMA_TIMEOUT_SECONDS` (Standard: 180).
- Verwenden Sie ein kleineres Modell, falls die Hardware langsam ist.

---

## Datenschutz und Sicherheit

- **Tokens, Refresh Tokens und Authorization Header werden niemals geloggt.**
- Vollständige Teams-Nachrichten und LLM-Antworten werden nicht geloggt.
- `.env`, Token-Cache und Datenbank sind über `.gitignore` ausgeschlossen.
- Die FastAPI-Anwendung bindet standardmäßig nur an `127.0.0.1`.
- Keine CORS-Freigabe für externe Domains.
- Antworten erscheinen unter dem angemeldeten Benutzerkonto.

---

## Autostart unter Windows (Aufgabenplanung)

1. Öffnen Sie die **Aufgabenplanung** (`taskschd.msc`).
2. Erstellen Sie eine neue Aufgabe:
   - **Trigger:** Bei Anmeldung oder beim Systemstart
   - **Aktion:** Programm starten
   - **Programm:** `C:\Pfad\zum\teams-local-llm\.venv\Scripts\python.exe`
   - **Argumente:** `-m uvicorn app.main:app --host 127.0.0.1 --port 8080`
   - **Starten in:** `C:\Pfad\zum\teams-local-llm`
3. Aktivieren Sie **Nur ausführen, wenn Benutzer angemeldet ist**.

> Stellen Sie sicher, dass Ollama ebenfalls automatisch startet.

---

## Sauberes Beenden

- Drücken Sie `Ctrl+C` im Terminal.
- Die Anwendung speichert den Token-Cache, schließt HTTP-Clients und die Datenbankverbindung.

---

## Tests ausführen

```powershell
.\scripts\test.ps1
```

Oder manuell:

```powershell
pytest tests/ -v
ruff check app tests
mypy app
```

---

## CLI-Befehle (Übersicht)

```powershell
python -m app.cli login                              # Microsoft-Anmeldung
python -m app.cli whoami                             # Angemeldeter Benutzer
python -m app.cli discover-teams                     # Teams auflisten
python -m app.cli discover-channels --team-id "<ID>" # Kanäle auflisten
python -m app.cli test-graph                         # Graph-Verbindung testen
python -m app.cli test-ollama                        # Ollama testen
python -m app.cli send-test-reply --message-id "<ID>" # Testantwort senden
python -m app.cli reset-watermark                    # Polling-Startpunkt zurücksetzen
```

---

## Zukünftige Erweiterungen

- **Thread-Replies verarbeiten** (`PROCESS_THREAD_REPLIES=true`)
- **RAG** (Retrieval-Augmented Generation) mit lokaler Wissensbasis
- **MCP** (Model Context Protocol) für erweiterte Tool-Integration
- **Dateianhänge** (Bilder, Dokumente) verarbeiten
- **Microsoft Teams Bot Framework** für echten Bot-Namen und Avatar
- **Mehrere Kanäle** gleichzeitig überwachen

---

## Lizenz

MIT License – siehe [LICENSE](LICENSE).

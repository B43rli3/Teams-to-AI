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

## Repository klonen

Bevor Sie die virtuelle Umgebung erstellen, muss das Projekt lokal vorliegen.

### Option A: Mit Git (empfohlen)

```powershell
cd $HOME
git clone https://github.com/B43rli3/Teams-to-AI.git teams-local-llm
cd teams-local-llm
git pull origin main
```

> Der GitHub-Repository-Name ist `Teams-to-AI`. Der lokale Ordner kann beliebig heißen; in dieser Anleitung verwenden wir `teams-local-llm`.
>
> **Hinweis:** Wenn Sie das Repository bereits früher geklont haben und `pyproject.toml` fehlt, holen Sie den aktuellen Stand mit `git pull origin main` nach.

### Option B: Als ZIP herunterladen

1. Öffnen Sie https://github.com/B43rli3/Teams-to-AI
2. Klicken Sie auf **Code → Download ZIP**
3. Entpacken Sie das Archiv, z. B. nach `C:\Users\nuern\teams-local-llm`
4. Wechseln Sie in den Ordner:

```powershell
cd C:\Users\nuern\teams-local-llm
```

### Prüfen, ob Sie im richtigen Ordner sind

```powershell
Get-Location
dir
```

Sie sollten mindestens diese Dateien sehen: `pyproject.toml`, `.env.example`, `README.md`, Ordner `app\` und `scripts\`.

Wenn `pyproject.toml` fehlt, sind Sie im falschen Verzeichnis.

---

## Virtuelle Umgebung erstellen (Windows PowerShell)

**Wichtig:** Führen Sie die folgenden Befehle nur im Projektordner aus (dort, wo `pyproject.toml` liegt), nicht in `C:\Users\nuern`.

```powershell
# 1. In den Projektordner wechseln (Pfad anpassen!)
cd C:\Users\nuern\teams-local-llm

# 2. PowerShell-Skripte für die aktuelle Benutzersitzung erlauben
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 3. Virtuelle Umgebung erstellen
python -m venv .venv

# 4. Virtuelle Umgebung aktivieren
.\.venv\Scripts\Activate.ps1
```

Nach erfolgreicher Aktivierung erscheint `(.venv)` am Anfang der Eingabezeile.

### Alternative ohne Aktivierung

Falls `Activate.ps1` weiterhin blockiert ist, können Sie die venv auch direkt nutzen:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m app.cli login
```

Die Skripte `scripts\start.ps1` und `scripts\test.ps1` verwenden diese Methode bereits intern.

### Aufräumen, falls die venv im falschen Ordner erstellt wurde

Wenn Sie `python -m venv .venv` versehentlich in `C:\Users\nuern` ausgeführt haben:

```powershell
cd C:\Users\nuern
Remove-Item -Recurse -Force .venv
```

Erstellen Sie die venv danach erneut im Projektordner.

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

## Alternativ: Gruppen-Chat statt Team-Kanal

Die Anwendung kann statt eines Team-Kanals einen **Gruppen-Chat** (oder 1:1-Chat) pollen.

### 1. Entra-Berechtigungen für Chats

Zusätzlich bzw. statt der Channel-Berechtigungen in der App-Registrierung:

| Berechtigung | Zweck |
|---|---|
| `User.Read` | Benutzer ermitteln |
| `Chat.Read` | Chat-Nachrichten lesen |
| `Chat.ReadWrite` | Antworten senden |

Admin Consent kann erforderlich sein.

### 2. `.env` für Chat-Modus

```env
TEAMS_TARGET_MODE=chat
TEAMS_CHAT_ID=19:xxxxxxxx@thread.v2

# Chat-Scopes (ohne ChannelMessage.*)
GRAPH_SCOPES=User.Read,Chat.Read,Chat.ReadWrite

# Team/Channel dürfen leer bleiben
TEAMS_TEAM_ID=
TEAMS_CHANNEL_ID=
```

Chat-IDs sehen typischerweise so aus: `19:...@thread.v2`

### 3. Chat-ID prüfen oder auflisten

```powershell
# Nach Scope-Änderung: neu anmelden
python -m app.cli login

# Alle Chats auflisten
python -m app.cli discover-chats

# Graph-Zugriff auf den konfigurierten Chat testen
python -m app.cli test-graph
```

### 4. Starten wie gewohnt

```powershell
.\scripts\start.ps1
```

Eine **neue** Nachricht im Gruppen-Chat senden (bestehende werden bei `PROCESS_BACKLOG=false` nicht beantwortet).

> **Hinweis:** In Gruppen-/1:1-Chats gibt es keine echten Threads wie im Team-Kanal.
> Die Graph-API erlaubt dort kein `.../messages/{id}/replies` (HTTP 405).
> Die Anwendung sendet die Antwort daher als **neue Nachricht** in denselben Chat.

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
| `GET http://127.0.0.1:8080/config` | Lokale Browser-Oberfläche für wichtige `.env`-Werte |
| `POST http://127.0.0.1:8080/poll-now` | Sofortigen Poll auslösen |
| `GET http://127.0.0.1:8080/recent-errors` | Kürzliche Fehler |

### Konfiguration im Browser

Unter `http://127.0.0.1:8080/config` steht eine einfache lokale Oberfläche bereit,
über die Sie folgende Werte ohne manuelles Bearbeiten der `.env` pflegen können:

- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID`
- `TEAMS_TARGET_MODE`
- `TEAMS_CHAT_ID`
- `TEAMS_TEAM_ID`
- `TEAMS_CHANNEL_ID`
- `TRIGGER_MODE`
- `BOT_PREFIX`
- `OLLAMA_VISION_MODEL`

Die Werte werden in die lokale `.env` geschrieben. Nach dem Speichern die Anwendung
bitte **neu starten**, damit Authentifizierung, Worker und Ollama-Client die neuen
Werte vollständig übernehmen.

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

### Installation und PowerShell

| Problem | Ursache | Lösung |
|---|---|---|
| `cd teams-local-llm` → Pfad nicht gefunden | Repository noch nicht geklont | Zuerst `git clone` oder ZIP entpacken (siehe oben) |
| `pyproject.toml` nicht gefunden | Befehle außerhalb des Projektordners | `cd` in den Ordner mit `app\` und `pyproject.toml` |
| `Activate.ps1` → Ausführung von Skripts deaktiviert | PowerShell Execution Policy | `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| venv im Benutzerordner statt im Projekt | `python -m venv` im falschen Verzeichnis | `.venv` in `C:\Users\nuern` löschen, im Projektordner neu erstellen |

### PDF-/Dateianhänge (SharePoint, 401 beim Download)

Teams speichert Dateianhänge in SharePoint/OneDrive. Der direkte `contentUrl`-Download schlägt oft mit **401** fehl. Die Anwendung nutzt daher die Graph-**Shares-API** (`/shares/.../driveItem`).

1. In `.env` (oder Config-UI) `Files.Read.All` zu `GRAPH_SCOPES` hinzufügen, z. B.:
   ```env
   GRAPH_SCOPES=User.Read,ChannelMessage.Read.All,ChannelMessage.Send,Files.Read.All
   ```
2. **Admin Consent** für `Files.Read.All` in Entra prüfen.
3. Token-Cache löschen: `data\msal_token_cache.json`
4. Neu anmelden: `python -m app.cli login`
5. Anwendung neu starten und PDF erneut testen.

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

## Alternative ohne eigene Entra-App: Power Automate

Wenn Sie keine App-Registrierung in Microsoft Entra anlegen können, lässt sich der Assistent über **Power Automate** mit Ihrem normalen M365-Login betreiben. Ollama kann weiterhin lokal laufen (über Power Automate Desktop auf Ihrem Windows-PC).

Ausführliche Schritt-für-Schritt-Anleitung: [docs/POWER_AUTOMATE_ANLEITUNG.md](docs/POWER_AUTOMATE_ANLEITUNG.md)

---

## Anhänge: Bilder und Dokumente

Die Anwendung kann seit Version des Anhang-Features **Bilder** und **Dokumente** aus Teams-Nachrichten verarbeiten.

### Verhalten

| Typ | Verarbeitung |
|---|---|
| Bilder (PNG, JPG, GIF, WEBP, …) | Download → Base64 → Ollama Vision-Modell |
| PDF, DOCX, TXT, MD, CSV, … | Textextraktion → Kontext im Prompt |
| Sonstige Dateien | Hinweis im Log, keine LLM-Übergabe |

### `.env`-Einstellungen

```env
PROCESS_ATTACHMENTS=true
PROCESS_IMAGES=true
PROCESS_DOCUMENTS=true
ATTACHMENT_MAX_FILES=5
ATTACHMENT_MAX_BYTES=10000000
ATTACHMENT_MAX_DOCUMENT_CHARS=30000

# Wichtig für Bilder: separates Vision-Modell
OLLAMA_VISION_MODEL=qwen2.5vl:7b
```

Vision-Modell installieren:

```powershell
ollama pull qwen2.5vl:7b
```

Ohne `OLLAMA_VISION_MODEL` werden Bilder zwar übergeben, aber reine Text-Modelle (z. B. `qwen3:14b`) können sie oft **nicht** sehen – daher die Empfehlung für ein Vision-Modell.

### Nutzung mit Prefix

```
/ai Was ist auf diesem Bild zu sehen?
```

(Anhang/Bild beifügen)

### Hinweise

- Inline-Bilder aus dem Teams-Body werden über Graph `hostedContents` geladen.
- Dateien aus SharePoint/OneDrive können zusätzliche Rechte brauchen (`Files.Read.All`) – sonst erscheint ein Hinweis in der Antwort/Log.
- Große Anhänge werden anhand von `ATTACHMENT_MAX_BYTES` abgelehnt.

### PDF-Antworten erstellen und senden

Wenn der Benutzer eine PDF wünscht (z. B. „als PDF senden“, „PDF erstellen“), erzeugt die Anwendung automatisch eine PDF-Datei aus der LLM-Antwort, lädt sie in den Teams-Dateiordner hoch und hängt sie an die Thread-Antwort an.

```env
SEND_PDF_REPLIES=true
GRAPH_SCOPES=User.Read,ChannelMessage.Read.All,ChannelMessage.Send,Files.Read.All,Files.ReadWrite
```

Beispiel:

```
/ai Fasse unsere Diskussion zusammen und sende das als PDF
```

Nach Scope-Änderungen: `data\msal_token_cache.json` löschen und `python -m app.cli login` ausführen.

### Antwortsprache Deutsch

Antworten sind verbindlich auf Deutsch. Dafür gelten:

- Strenger System-Prompt (`LLM_SYSTEM_PROMPT`)
- Automatischer Nachversuch bei erkannter englischer Antwort (`LLM_FORCE_GERMAN_RETRY=true`)

---

## Zukünftige Erweiterungen

- **Thread-Replies verarbeiten** (`PROCESS_THREAD_REPLIES=true`)
- **RAG** (Retrieval-Augmented Generation) mit lokaler Wissensbasis
- **MCP** (Model Context Protocol) für erweiterte Tool-Integration
- **Erweiterte Anhänge** (OCR, Tabellen, weitere Formate)
- **Microsoft Teams Bot Framework** für echten Bot-Namen und Avatar
- **Mehrere Kanäle** gleichzeitig überwachen

---

## Lizenz

MIT License – siehe [LICENSE](LICENSE).

# VS Code Integration — OpenRouter AI Stack

> **Ziel:** VS Code mit dem Stack verbinden, sodass du KI-Unterstützung direkt beim Coding hast — automatisches Modell-Routing, Gedächtnis aus vergangenen Sessions und optional MCP-Tools.

---

## Inhaltsverzeichnis

- [Schnellstart (5 Minuten)](#schnellstart-5-minuten)
- [Vollständige Einrichtung](#vollständige-einrichtung)
  - [Cline — Agentic Coding](#1-cline--agentic-coding)
  - [Continue.dev — Chat + Autocomplete](#2-continuedev--chat--autocomplete)
  - [MCP Tools aktivieren](#3-mcp-tools-aktivieren)
- [Routing verstehen](#routing-verstehen)
- [Tipps & Best Practices](#tipps--best-practices)
- [Troubleshooting](#troubleshooting)

---

## Schnellstart (5 Minuten)

Für alle, die es direkt ausprobieren wollen.

### Voraussetzung

Der Stack läuft und ist erreichbar:
```
http://DEINE-SERVER-IP:8085
```

### Schritt 1 — Cline installieren

In VS Code: `Strg+Shift+X` → **"Cline"** suchen → Installieren

### Schritt 2 — Cline konfigurieren

Cline-Icon in der linken Sidebar öffnen → **Settings** (Zahnrad oben rechts)

| Feld | Wert |
|------|------|
| API Provider | `OpenAI Compatible` |
| Base URL | `http://DEINE-SERVER-IP:8085/v1` |
| API Key | `openrouter-via-proxy` *(beliebiger Text)* |
| Model | `auto` |

> **Wichtig:** Model muss `auto` sein — nur dann greift das automatische Routing. Jeder andere Wert (z.B. `deepseek/deepseek-v3.2`) wird als manuelle Override gewertet.

### Schritt 3 — Testen

Im Cline-Chat eingeben:
```
Erkläre mir diese Funktion
```
Wenn eine Antwort kommt, ist alles korrekt eingerichtet.

---

## Vollständige Einrichtung

### 1. Cline — Agentic Coding

Cline ist das Haupt-Tool für komplexe Coding-Aufgaben: Dateien lesen/schreiben, Terminal-Befehle ausführen, ganze Features implementieren.

#### Installation

```
VS Code → Extensions → "Cline" von saoudrizwan
```

#### Konfiguration (Settings → API Configuration)

```
API Provider:    OpenAI Compatible
Base URL:        http://DEINE-SERVER-IP:8085/v1
API Key:         openrouter-via-proxy
Model:           auto
```

#### Was passiert im Hintergrund?

```
Cline sendet Request
    ↓
Router empfängt (Port 8085)
    ↓
Routing-Entscheidung:
  - Code-Review, Architektur, Refactoring  →  Qwen3-Coder Plus (komplex, 1M Kontext)
  - Bild im Kontext                        →  Qwen3-VL (vision)
  - Schnelle Fragen, kleine Änderungen     →  DeepSeek V3.2 (fast)
  - Fehler / Timeout                       →  Gemini Flash (fallback)
    ↓
Gedächtnis aus vergangenen Sessions wird automatisch injiziert
    ↓
Antwort zurück an Cline
```

#### Empfohlene Cline-Einstellungen

- **Auto-approve:** Für Lesezugriffe empfohlen, für Schreibzugriffe lieber manuell bestätigen
- **Context window:** Standard lassen (Router handhabt das)
- **Temperature:** Nicht ändern (Router-Default ist optimal)

---

### 2. Continue.dev — Chat + Autocomplete

Continue ist besser für schnelle Code-Ergänzungen und Inline-Chat direkt im Editor.

#### Installation

```
VS Code → Extensions → "Continue" von Continue
```

#### Konfiguration

Datei öffnen: `~/.continue/config.yaml`

```yaml
name: OpenRouter AI Stack
version: 1.0.0
schema: v1

models:
  - name: OpenRouter Auto
    provider: openai
    model: auto
    apiBase: http://DEINE-SERVER-IP:8085/v1
    apiKey: openrouter-via-proxy

tabAutocompleteModel:
  name: OpenRouter Autocomplete
  provider: openai
  model: auto
  apiBase: http://DEINE-SERVER-IP:8085/v1
  apiKey: openrouter-via-proxy

mcpServers:
  - name: OpenRouter AI Stack
    url: http://DEINE-SERVER-IP:8087/mcp
```

> **Wichtig:** Der Header (`name`, `version`, `schema`) ist Pflicht — ohne ihn ignoriert Continue die gesamte Konfiguration.

> Eine fertige Vorlage liegt im Repo unter [`continue.config.example.yaml`](../continue.config.example.yaml).

#### Shortcuts

| Aktion | Shortcut |
|--------|----------|
| Chat öffnen | `Strg+Shift+L` |
| Code erklären | Code markieren → `Strg+Shift+L` |
| Inline-Bearbeitung | Code markieren → `Strg+I` |
| Autocomplete | automatisch beim Tippen |

---

### 3. MCP Tools aktivieren

MCP (Model Context Protocol) gibt der KI zusätzliche Werkzeuge — sie kann z.B. gezielt im Gedächtnis suchen oder das Routing erklären.

#### In Cline (empfohlen)

Cline-Settings → **MCP Servers** → **Add**:

```json
{
  "openrouter-ai-stack": {
    "url": "http://DEINE-SERVER-IP:8087/mcp",
    "type": "http"
  }
}
```

#### In VS Code nativ (ab VS Code 1.99+)

Neue Datei `.vscode/mcp.json` im Projektordner erstellen:

```json
{
  "servers": {
    "openrouter-ai-stack": {
      "type": "http",
      "url": "http://DEINE-SERVER-IP:8087/mcp"
    }
  }
}
```

#### Verfügbare Tools

| Tool | Beschreibung | Beispiel-Aufruf |
|------|-------------|-----------------|
| `chat` | Chat mit automatischem Routing | *"Erkläre diesen Algorithmus"* |
| `complete_code` | Code-Completion mit Datei-Kontext | *"Vervollständige diese Funktion"* |
| `analyze_image` | Bildanalyse via Qwen3-VL | Screenshot einfügen + Frage |
| `search_memory` | Suche in vergangenen Sessions | *"Was haben wir letztes Mal besprochen?"* |
| `route_info` | Routing-Entscheidung anzeigen | *"Welches Modell würde für X gewählt?"* |
| `web_search` | Web-Suche via SearXNG — gezielt aufrufbar | *"Suche die aktuelle FastAPI-Doku"* |
| `screenshot` | Webseite abrufen und Inhalt extrahieren | *"Lies diese URL und fasse sie zusammen"* |

**`web_search` — explizit aufrufen, nie automatisch:**

```
web_search("FastAPI OAuth2 best practices")
web_search("Qwen3 API changelog", max_results=3)
```

Gibt Titel, URL und Snippet zurück — ideal wenn die KI aktuelle Informationen braucht, ohne den gesamten Chat durch Auto-Trigger zu unterbrechen.

**`screenshot` — Seiteninhalte extrahieren:**

```
screenshot("https://docs.example.com/api-reference")
screenshot("https://dashboard.example.com", mode="screenshot")
```

- `mode=text` (Standard): extrahiert lesbaren Text aus der HTML-Seite
- `mode=screenshot`: versucht Screenshot via Puppeteer (falls verfügbar), sonst Text

---

## Routing verstehen

Das automatische Routing passiert **unsichtbar** — du musst nichts manuell einstellen.

```
Deine Frage
    │
    ├── Enthält ein Bild?                     → Qwen3-VL (Vision)
    │
    ├── ≥ 150 Wörter oder Komplex-Keyword?    → Qwen3-VL (Reasoning)
    │   (analyze, refactor, debug, explain,
    │    architecture, optimize, review ...)
    │
    ├── Einfache / kurze Anfrage?             → DeepSeek V3.2 (schnell + günstig)
    │
    └── Fehler beim Primärmodell?             → Gemini Flash (automatischer Fallback)
```

**Manuelle Overrides** sind möglich, indem du ein bekanntes Modell explizit angibst:

| Modell-ID | Wann sinnvoll |
|-----------|---------------|
| `qwen/qwen3-coder-plus` | Komplexe Coding-Aufgaben erzwingen (1M Token Kontext) |
| `qwen/qwen3-vl-32b-instruct` | Vision-Tasks erzwingen |
| `deepseek/deepseek-v3.2` | Kosten sparen bei einfachen Tasks |
| `google/gemini-3.1-flash-lite-preview` | Fallback direkt nutzen |

---

## Tipps & Best Practices

**Modell auf `auto` lassen**  
Jeder andere Wert überschreibt das Routing — nutze das nur wenn du weißt warum.

**Für Cline: CLAUDE.md nutzen**  
Lege eine `CLAUDE.md` im Projektroot an — Cline liest sie automatisch und folgt den Anweisungen.

**Speicher funktioniert projektübergreifend**  
Das Gedächtnis ist nicht an einen Workspace gebunden. Informationen aus Session A stehen in Session B zur Verfügung.

**Kosten beobachten**  
```bash
# Heutige Kosten
curl http://DEINE-SERVER-IP:8085/api/costs/today

# Aufschlüsselung nach Modell
curl http://DEINE-SERVER-IP:8085/api/costs/by_model
```

**Forschungs-Modus (Chat)**  
Tippe `/web`, `/search`, `/research` oder `/recherche` im Chat um aktuelle Web-Informationen abzurufen (via SearXNG).  
Im VS Code Coder-Workflow lieber das `web_search` MCP-Tool verwenden — damit behält die KI den Code-Kontext und sucht nur wenn du es explizit anforderst.

**Audio-Transkription**  
Audiodateien direkt im Open WebUI hochladen → werden automatisch transkribiert (MiMo-V2-Omni via OpenRouter).

---

## Troubleshooting

### "Connection refused" / Router nicht erreichbar

```bash
# Stack läuft?
docker compose ps

# Router gesund?
curl http://DEINE-SERVER-IP:8085/health

# Firewall?
# Port 8085 und 8087 müssen vom Client erreichbar sein
```

### Routing greift nicht / immer dasselbe Modell

- Model in Cline/Continue auf `auto` setzen (nicht leer lassen)
- Prüfen welches Modell gewählt wurde:
  ```bash
  curl -X POST http://DEINE-SERVER-IP:8085/route-info \
    -H "Content-Type: application/json" \
    -d '{"messages":[{"role":"user","content":"DEINE FRAGE"}]}'
  ```

### MCP Tools nicht verfügbar

```bash
# MCP Server gesund?
curl http://DEINE-SERVER-IP:8087/health

# Logs prüfen
docker compose logs ai-mcp --tail=20
```

### Antworten kommen langsam

Normal beim ersten Request nach dem Start (Modell-Warmup bei OpenRouter).  
Ab dem zweiten Request sind gecachte Antworten deutlich schneller (`CACHE_TTL=3600` in `.env`).

### Gedächtnis wird nicht injiziert

```bash
curl http://DEINE-SERVER-IP:8086/health
docker compose logs memory-svc --tail=20
```

---

*Zurück zur [Hauptdokumentation](../README.md)*

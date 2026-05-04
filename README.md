# OpenRouter AI Stack

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/manueldell)


**1 API Key · Mehrere Modelle · Automatisches Routing · Web-Chat + VS Code**

Ein produktionsreifer KI-Stack für selbst gehostete Infrastruktur — optimiert für Portainer LXC / Docker Compose. Keine lokalen Modelle nötig, alle Berechnungen laufen über [OpenRouter](https://openrouter.ai).

---

## Für wen ist was?

| Ich bin... | Ich nutze... | Meine Anleitung |
|-----------|-------------|-----------------|
| Normaler Nutzer / Laie | **Open WebUI** im Browser | [OPENWEBUI_GUIDE.md](docs/OPENWEBUI_GUIDE.md) |
| Entwickler / Coder | **VS Code** mit Cline + Continue | [VSCODE_SETUP.md](docs/VSCODE_SETUP.md) |

---

## Auf einen Blick

```
Browser / Open WebUI  ──────────────────────────────────────┐
                                                             ▼
VS Code (Cline / Continue)  ──────►  Smart Router :8085  ◄──┤
                                           │                 │
                              ┌────────────┼─────────┐       │
                              ▼            ▼         ▼       │
                         Qwen3-VL    DeepSeek V3   Gemini    │
                         Vision +    Schnell +     Fallback  │
                         Komplex     Günstig                  │
                              │                              │
                    ┌─────────┴──────────┐                   │
                    ▼                    ▼                   │
              Memory :8086           Redis                   │
              SQLite + TF-IDF        Cache + Rate Limit      │
                    │                                        │
              MCP Server :8087  ◄──────────────────────────┘
              VSCode Tools
```

---

## Features & Status

| Feature | Status | Details |
|---------|--------|---------|
| **Smart Routing** | ✅ Aktiv | Vision → Qwen3-VL, Komplex/Code → Qwen3-Coder Plus (1M Kontext), Einfach → DeepSeek, Fehler → Gemini |
| **Memory System** | ✅ Aktiv | TF-IDF Ähnlichkeitssuche, SQLite, automatische Kontextinjizierung |
| **Web Research** | ✅ Aktiv | SearXNG, Commands: `/web`, `/search`, `/recherche`, `/internet` |
| **Audio Transkription** | ✅ Aktiv | MiMo-V2-Omni via OpenRouter, Groq Whisper als Fallback |
| **Cost Tracking** | ✅ Aktiv | Jeder API-Call protokolliert, Abfrage via `/api/costs/*` |
| **VS Code Integration** | ✅ Aktiv | Cline + Continue.dev, MCP Tools: chat, complete_code, web_search, screenshot, ... |
| **Bildgenerierung** | ✅ Aktiv (mit Key) | Together.ai FLUX.1-schnell-Free — `TOGETHER_API_KEY` in `.env` eintragen ([together.ai](https://api.together.ai), kostenlos) |
| **Authentik SSO** | ⏳ Vorbereitet | OAuth2/OIDC-Config in docker-compose hinterlegt, nur auskommentiert |

---

## Schnellstart

### Voraussetzungen

```bash
docker --version        # >= 24.0
docker compose version  # >= 2.20
```

### 1. Repository klonen

```bash
git clone https://github.com/ManuelDell/openrouter-ai-stack.git
cd openrouter-ai-stack
```

### 2. Konfiguration

```bash
cp .env.example .env
nano .env
```

**Pflichtfelder:**

```env
OPENROUTER_API_KEY=sk-or-v1-...     # https://openrouter.ai/keys
REDIS_PASSWORD=...                   # openssl rand -hex 32
MCP_SECRET=...                       # openssl rand -hex 32
```

**Optional (für Audio-Transkription als Fallback):**

```env
GROQ_API_KEY=gsk_...                 # https://console.groq.com/keys (kostenlos)
```

### 3. Stack starten

```bash
docker compose pull     # Pre-built Images von GHCR laden
docker compose up -d
```

Erster Start dauert ~30–60 Sekunden (Images + WebUI-Initialisierung).

### 4. Health-Check

```bash
curl http://localhost:8085/health   # Smart Router
curl http://localhost:8086/health   # Memory Service
curl http://localhost:8087/health   # MCP Server
```

### 5. Web-Chat öffnen

**`http://SERVER-IP:8088`**

Der erste registrierte Benutzer wird automatisch Admin. Weitere Accounts müssen vom Admin angelegt werden (`ENABLE_SIGNUP=false`).

---

## Für normale Nutzer — Open WebUI

**`http://SERVER-IP:8088`** im Browser öffnen.

→ **[Vollständige Anleitung: docs/OPENWEBUI_GUIDE.md](docs/OPENWEBUI_GUIDE.md)**

---

## Für Entwickler — VS Code Integration

→ **[Vollständige Anleitung: docs/VSCODE_SETUP.md](docs/VSCODE_SETUP.md)**

**Kurzfassung — Cline in 2 Minuten:**

1. VS Code Extension **"Cline"** installieren
2. Settings öffnen → API Provider: `OpenAI Compatible`
3. Base URL: `http://SERVER-IP:8085/v1` · Model: `auto`

---

## Services & Ports

| Service | Extern | Intern | Beschreibung |
|---------|--------|--------|-------------|
| Open WebUI | **:8088** | :8080 | Browser Chat-Interface |
| Smart Router | **:8085** | :8080 | OpenAI-kompatible API |
| Memory Service | `127.0.0.1:8086` | :8081 | Nur lokal erreichbar |
| MCP Server | **:8087** | :8082 | VSCode Tool-Integration |
| SearXNG | `127.0.0.1:8091` | :8080 | Selbst gehostete Suche |
| Redis | intern | :6379 | Kein Host-Binding |

---

## Routing-Logik

| Bedingung | Modell | Kosten (ca.) |
|-----------|--------|-------------|
| Bild im Request | `qwen/qwen3-vl-32b-instruct` | $0.20 / $0.88 per 1M |
| ≥ 150 Wörter **oder** Komplex-Keyword | `qwen/qwen3-coder-plus` (1M Kontext) | $0.65 / $3.50 per 1M |
| Einfache / kurze Anfrage | `deepseek/deepseek-v3.2` | $0.14 / $0.28 per 1M |
| Fehler / Timeout | `google/gemini-3.1-flash-lite-preview` | sehr günstig |

**Komplex-Keywords** (konfigurierbar via `COMPLEX_KEYWORDS` in `.env`):  
`analyze`, `explain`, `debug`, `refactor`, `optimize`, `design`, `architecture`, `review` + deutsche Entsprechungen

**Manuelle Overrides:** Ein bekanntes Modell explizit angeben überschreibt das Routing. Unbekannte Namen → automatisches Routing.

---

## Special Commands

Direkt im Chat (Open WebUI oder VS Code) verwendbar:

| Command | Varianten | Funktion |
|---------|-----------|---------|
| `/web <frage>` | `/search`, `/internet`, `/suche` | Web-Recherche via SearXNG |
| `/recherche <frage>` | `/recherchiere`, `/research` | Web-Recherche (Deutsch) |
| `/transkribiere` | `/transcribe` | Audio-Transkription (sichtbar) |

Natürliche Phrasen lösen die Recherche ebenfalls aus: *"suche im internet"*, *"google mal"*, *"search the web"* usw.

**Nur im VS Code / Coder-Bereich (MCP Tools — kein Auto-Trigger):**

| Tool | Funktion |
|------|---------|
| `web_search(query)` | Gezielt Web-Suche ohne Chat-Unterbrechung |
| `screenshot(url)` | Seiteninhalte lesen / extrahieren |

---

## Cost Tracking API

```bash
# Heutige Kosten
curl http://localhost:8085/api/costs/today

# Gesamtstatistik
curl http://localhost:8085/api/costs/stats

# Letzte 7 Tage
curl http://localhost:8085/api/costs/history?days=7

# Aufschlüsselung nach Feature
curl http://localhost:8085/api/costs/by_feature

# Aufschlüsselung nach Modell
curl http://localhost:8085/api/costs/by_model
```

---

## Pre-built Images (GHCR)

Alle Custom Services sind fertig gebaut auf GitHub Container Registry:

| Service | Image |
|---------|-------|
| Smart Router | `ghcr.io/manueldell/openrouter-ai-stack/router:latest` |
| Memory Service | `ghcr.io/manueldell/openrouter-ai-stack/memory:latest` |
| MCP Server | `ghcr.io/manueldell/openrouter-ai-stack/mcp:latest` |

Update auf neue Version:
```bash
docker compose pull && docker compose up -d
```

---

## Memory System

Konversationen werden automatisch gespeichert (SQLite + TF-IDF) und bei neuen Anfragen als Kontext injiziert.

```bash
# Statistiken
curl http://localhost:8086/stats

# Suche in Erinnerungen
curl -X POST http://localhost:8086/search \
  -H "Content-Type: application/json" \
  -d '{"query": "FastAPI Authentifizierung", "limit": 3}'

# Alle Memories löschen
curl -X DELETE http://localhost:8086/memories
```

---

## Datei-Struktur

```
openrouter-ai-stack/
├── docker-compose.yml              # Stack-Definition
├── .env.example                    # Konfigurations-Template
├── .env                            # Deine Config (nicht committen!)
├── docs/
│   ├── OPENWEBUI_GUIDE.md          # Benutzerhandbuch (Laien / alle Nutzer)
│   └── VSCODE_SETUP.md             # VS Code Einrichtungsanleitung (Entwickler)
├── services/
│   ├── router/                     # Smart Router (FastAPI)
│   │   ├── app.py
│   │   ├── utils/
│   │   │   ├── cost_tracker.py
│   │   │   └── request_analyzer.py
│   │   ├── dispatchers/
│   │   │   ├── research_dispatcher.py
│   │   │   ├── audio_dispatcher.py
│   │   │   └── imagegen_dispatcher.py  # vorbereitet
│   │   └── routes/
│   │       └── cost_routes.py
│   ├── memory/                     # Memory Service (SQLite + TF-IDF)
│   ├── mcp/                        # MCP Server (VSCode Tools)
│   └── whisper/                    # Whisper Service (vorbereitet)
└── data/                           # Persistente Daten (nicht committen)
    ├── memory/                     # SQLite Datenbank
    ├── costs/                      # Kosten-Protokoll
    ├── redis/                      # Redis Snapshots
    ├── searxng/                    # SearXNG Konfiguration
    └── webui/                      # Open WebUI Daten
```

---

## Verwaltung

```bash
# Status aller Services
docker compose ps

# Logs live
docker compose logs -f
docker compose logs -f ai-router

# Einzelnen Service neu starten
docker compose restart ai-router

# Update (neue Images laden)
docker compose pull && docker compose up -d

# Stack stoppen
docker compose down

# Stack + alle Daten löschen (ACHTUNG: unwiderruflich!)
docker compose down -v
```

---

## Bildgenerierung aktivieren

Bildgenerierung nutzt **Together.ai FLUX.1-schnell-Free** (kostenlos im Free-Tier):

1. Kostenlosen Account erstellen: [api.together.ai](https://api.together.ai)
2. API-Key kopieren
3. In `.env` eintragen:
   ```env
   TOGETHER_API_KEY=dein-key-hier
   ```
4. Router neu starten: `docker compose restart ai-router`

Sobald der Key gesetzt ist, können Nutzer Bilder per Chat-Befehl generieren:
```
Zeichne einen Hund auf einer Wiese
Generiere ein Bild von einem futuristischen Stadtbild
```

---

## Troubleshooting

**Router antwortet nicht:**
```bash
docker compose ps
docker compose logs ai-router --tail=50
```

**OpenRouter 401:**
```bash
grep OPENROUTER_API_KEY .env
curl https://openrouter.ai/api/v1/models \
  -H "Authorization: Bearer $(grep OPENROUTER_API_KEY .env | cut -d= -f2)" | head -c 200
```

**Routing greift nicht / immer dasselbe Modell:**
```bash
# Routing-Entscheidung testen (kein API-Call)
curl -X POST http://localhost:8085/route-info \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"DEINE FRAGE"}]}'
```

**Memory wird nicht injiziert:**
```bash
curl http://localhost:8086/health
docker compose logs memory-svc --tail=20
```

**Rate Limit (429):**  
`RATE_LIMIT_RPM` in `.env` erhöhen (Standard: 60/min).

---

## Lizenz

MIT — siehe [LICENSE](LICENSE)

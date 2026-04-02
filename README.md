# OpenRouter AI Stack

Production-ready AI stack für Portainer LXC / Docker Compose.  
**1 API Key → 3 Modelle → automatisches Routing → Web-Chat + VSCode.**

Pre-built images auf GHCR — kein lokaler Build nötig.

---

## Architektur

```
Browser / Open WebUI :8088
        │
VSCode  │  (Continue Extension)
   └────┤
        ▼
   MCP Server :8087        ← JSON-RPC für VSCode
        │
        ▼
  Smart Router :8085       ← OpenAI-kompatible API
   ├── qwen/qwen3-vl-32b   ← Vision + komplexe Tasks
   ├── deepseek/v3.2        ← Schnell + günstig
   └── gemini-2.5-flash     ← Fallback
        │
   ┌────┴────┐
   ▼         ▼
Memory    Redis
:8086     (intern)
SQLite    Cache + Rate Limit
TF-IDF
```

---

## Routing-Entscheidung

| Bedingung | Modell | Kosten (ca.) |
|-----------|--------|-------------|
| Bild im Request | `qwen/qwen3-vl-32b-instruct` | $0.20/$0.88 per 1M |
| ≥150 Token oder Komplex-Keyword | `qwen/qwen3-vl-32b-instruct` | $0.20/$0.88 per 1M |
| Einfache Anfrage | `deepseek/deepseek-v3.2` | $0.14/$0.28 per 1M |
| Fehler / Timeout | `google/gemini-2.5-flash-lite` | sehr günstig |

**Komplex-Keywords** (konfigurierbar): `analyze`, `explain`, `debug`, `refactor`, `optimize`, `design`, `architecture`, `review` + deutsche Entsprechungen

---

## Schnellstart

### 1. Voraussetzungen

```bash
docker --version        # >= 24.0
docker compose version  # >= 2.20
```

### 2. Repository klonen

```bash
git clone https://github.com/ManuelDell/openrouter-ai-stack.git
cd openrouter-ai-stack
```

### 3. Konfiguration

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

### 4. Stack starten

```bash
docker compose pull    # Pre-built Images von GHCR laden
docker compose up -d
```

Erster Start dauert ~30s (Images downloaden + WebUI initialisieren).

### 5. Health-Check

```bash
curl http://localhost:8085/health   # Router
curl http://localhost:8086/health   # Memory
curl http://localhost:8087/health   # MCP
```

### 6. Web-Chat öffnen

Browser: **http://SERVER-IP:8088**

Erster Benutzer wird automatisch Admin.  
Weitere Accounts müssen vom Admin angelegt werden (`ENABLE_SIGNUP=false`).

---

## Services & Ports

| Service | Port | Beschreibung |
|---------|------|-------------|
| Open WebUI | **8088** | Browser Chat UI |
| Smart Router | **8085** | OpenAI-kompatible API |
| Memory Service | **8086** | 127.0.0.1 only |
| MCP Server | **8087** | VSCode Integration |
| Redis | intern | kein Host-Binding |

---

## Pre-built Images (GHCR)

Die 3 Custom Services sind auf GitHub Container Registry veröffentlicht:

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

## Open WebUI — Web Chat

Open WebUI ([ghcr.io/open-webui/open-webui](https://github.com/open-webui/open-webui)) läuft als Frontend und kommuniziert über die OpenAI-kompatible Router-API.

**Features:**
- Multi-User mit Accounts (Admin-only Signup)
- Eigene Prompt-Shortcuts (`/pharmazie`, `/recherche`, etc.)
- Modell-Wechsel direkt im Chat
- Chat-History, File-Upload, Bildanalyse via Qwen3-VL
- Admin-Panel für Branding, Benutzer, Modelle

**Erster Admin-Account:**  
Beim ersten Start der WebUI kann sich der erste Nutzer ohne Einladung registrieren — dieser wird automatisch Admin.

---

## VSCode Integration (Continue Extension)

### 1. Continue installieren

Extensions → `Continue` von Continue.dev installieren.

### 2. Konfiguration

Vorlage liegt unter `continue.config.example.json`. Kopieren nach `~/.continue/config.json` und `SERVER-IP` ersetzen.

Inhalt:

```json
{
  "models": [
    {
      "title": "OpenRouter Auto",
      "provider": "openai",
      "model": "openrouter-auto",
      "apiBase": "http://SERVER-IP:8085/v1",
      "apiKey": "not-required"
    }
  ],
  "mcpServers": [
    {
      "name": "OpenRouter MCP",
      "url": "http://SERVER-IP:8087/mcp",
      "type": "http"
    }
  ]
}
```

### MCP Tools

| Tool | Beschreibung |
|------|-------------|
| `chat` | Chat mit automatischem Routing |
| `complete_code` | Code-Completion mit Datei-Kontext |
| `analyze_image` | Bildanalyse via Qwen3-VL |
| `search_memory` | Suche in vergangenen Sessions |
| `route_info` | Routing-Entscheidung anzeigen (kein API-Call) |

---

## Memory System

Conversations werden automatisch gespeichert (SQLite + TF-IDF) und als Kontext bei neuen Anfragen injiziert.

```bash
# Stats
curl http://localhost:8086/stats

# Suchen
curl -X POST http://localhost:8086/search \
  -H "Content-Type: application/json" \
  -d '{"query": "FastAPI authentication", "limit": 3}'

# Alle Memories löschen
curl -X DELETE http://localhost:8086/memories
```

### Hindsight Client CLI

```bash
python hindsight_client.py stats
python hindsight_client.py search "Python async patterns"
python hindsight_client.py store "Frage" "Antwort"
python hindsight_client.py forget
```

---

## Datei-Struktur

```
openrouter-ai-stack/
├── docker-compose.yml          # Stack-Definition (pre-built images)
├── .env.example                # Config-Template
├── .env                        # Deine Config (nicht committen!)
├── hindsight_client.py         # Memory Client + CLI
├── vscode_integration.py       # MCP Server (auch standalone nutzbar)
├── .vscode/
│   └── settings.json           # VSCode / Continue Konfiguration
├── services/
│   ├── router/
│   │   ├── app.py              # Smart Router (FastAPI + Streaming)
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── memory/
│   │   ├── app.py              # Memory Service (SQLite + TF-IDF)
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── mcp/
│       ├── app.py              # MCP Service Entrypoint
│       ├── Dockerfile
│       └── requirements.txt
├── data/                       # Persistente Daten (nicht committen)
│   ├── memory/                 # SQLite DB
│   ├── redis/                  # Redis Snapshots
│   └── webui/                  # Open WebUI Datenbank + Uploads
└── logs/                       # Service Logs (nicht committen)
```

---

## Verwaltung

```bash
# Status
docker compose ps

# Logs
docker compose logs -f
docker compose logs -f ai-router

# Neustart einzelner Services
docker compose restart ai-router

# Update (neue Images)
docker compose pull && docker compose up -d

# Stack stoppen
docker compose down

# Stack + alle Daten löschen (ACHTUNG!)
docker compose down -v
```

---

## Features & Roadmap

| Feature | Status | Beschreibung |
|---------|--------|-------------|
| Smart Routing | ✅ Aktiv | Automatisches Routing auf Vision/Complex/Fast/Fallback |
| Memory System | ✅ Aktiv | Kontextinjizierung aus vergangenen Gesprächen (TF-IDF) |
| Web Research | ✅ Aktiv | Selbst gehostete SearXNG-Suche + Seitenextraktion |
| Audio Transkription | ✅ Aktiv | MiMo-V2-Omni via OpenRouter, Groq Whisper als Fallback |
| Cost Tracking | ✅ Aktiv | SQLite-Protokollierung jedes API-Calls, Abfrage via `/api/costs/*` |
| Bildgenerierung | ⏳ Vorbereitet | **Noch nicht aktiv** — OpenRouter gibt Bilddaten nicht in API-Responses zurück (Stand: April 2026). Der Dispatcher ist implementiert und wird aktiviert sobald OpenRouter Image-Output unterstützt. Alternativ: DALL-E Key im Open WebUI Admin Panel hinterlegen. |
| Authentik SSO | ⏳ Vorbereitet | OAuth2/OIDC-Block in docker-compose auskommentiert, aktivierbar sobald Authentik läuft |

---

## Troubleshooting

**Router antwortet nicht:**
```bash
docker compose ps
docker compose logs ai-router --tail=50
```

**Streaming bricht ab (`TransferEncodingError`):**  
Sicherstellen dass das Router-Image aktuell ist: `docker compose pull ai-router && docker compose up -d ai-router`

**OpenRouter 401:**
```bash
grep OPENROUTER_API_KEY .env
curl https://openrouter.ai/api/v1/models \
  -H "Authorization: Bearer $(grep OPENROUTER_API_KEY .env | cut -d= -f2)" | head -c 200
```

**OpenRouter 400 (Invalid model):**  
Modell-IDs in `.env` prüfen. Gültige IDs: `qwen/qwen3-vl-32b-instruct`, `deepseek/deepseek-v3.2`, `google/gemini-2.5-flash-lite`

**Memory wird nicht injiziert:**
```bash
curl http://localhost:8086/health
docker compose logs memory-svc --tail=20
```

**Rate Limit (429):**  
`RATE_LIMIT_RPM` in `.env` erhöhen (Standard: 60/min).

---

## Lizenz

MIT

"""
vscode_integration.py — MCP Server für VSCode
================================================
Implementiert das Model Context Protocol (MCP) als HTTP/SSE Server.
VSCode Continue / Copilot verbindet sich via REST oder stdio.

Tools exposed to VSCode:
  - chat             : Vollständige Chat-Anfrage mit Auto-Routing
  - complete_code    : Code-Completion mit Kontext
  - analyze_image    : Bild-Analyse via Qwen3-VL
  - search_memory    : Suche in gespeicherten Memories
  - route_info       : Zeige welches Modell verwendet würde

Starte standalone:
  python vscode_integration.py

Oder als Docker Service (siehe docker-compose.yml)
"""

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any, AsyncGenerator, Optional

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

log = logging.getLogger("mcp_server")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

ROUTER_URL   = os.getenv("ROUTER_URL", "http://localhost:8085")
MEMORY_URL   = os.getenv("MEMORY_URL", "http://localhost:8086")
SEARXNG_URL  = os.getenv("SEARXNG_URL", "http://searxng:8080")
MCP_SECRET   = os.getenv("MCP_SECRET", "")
MCP_PORT     = int(os.getenv("MCP_PORT", "8087"))

# ─── MCP Tool Definitions ────────────────────────────────────

TOOLS = [
    {
        "name":        "chat",
        "description": "Send a message to the AI. Auto-routes to best model (vision/complex/fast).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type":        "string",
                    "description": "The user message to send",
                },
                "system_prompt": {
                    "type":        "string",
                    "description": "Optional system prompt",
                },
                "image_url": {
                    "type":        "string",
                    "description": "Optional image URL or base64 data URI for vision tasks",
                },
                "stream": {
                    "type":        "boolean",
                    "description": "Stream response tokens (default: false)",
                    "default":     False,
                },
            },
            "required": ["message"],
        },
    },
    {
        "name":        "complete_code",
        "description": "Code completion with automatic language detection and context injection.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code":     {"type": "string", "description": "Code snippet to complete"},
                "language": {"type": "string", "description": "Programming language (auto-detected if omitted)"},
                "context":  {"type": "string", "description": "Additional context about the codebase"},
                "instruction": {"type": "string", "description": "What to do with the code"},
            },
            "required": ["code"],
        },
    },
    {
        "name":        "analyze_image",
        "description": "Analyze an image using Qwen3-VL vision model. Auto-routes to vision model.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "image": {
                    "type":        "string",
                    "description": "Image URL or base64 data URI (data:image/...;base64,...)",
                },
                "question": {
                    "type":        "string",
                    "description": "Question about the image",
                    "default":     "Describe this image in detail.",
                },
            },
            "required": ["image"],
        },
    },
    {
        "name":        "search_memory",
        "description": "Search past conversations and coding sessions for relevant context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default: 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name":        "route_info",
        "description": "Show which AI model would be selected for a given request without calling it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The message to analyze"},
            },
            "required": ["message"],
        },
    },
    {
        "name":        "web_search",
        "description": "Search the web via self-hosted SearXNG. Returns titles, URLs and snippets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query":       {"type": "string",  "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max results to return (default: 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name":        "screenshot",
        "description": "Fetch a webpage and return its content as text (or screenshot base64 for visual pages).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url":  {"type": "string", "description": "URL of the page to fetch"},
                "mode": {"type": "string", "enum": ["text", "screenshot"], "description": "text=extract readable text, screenshot=capture visual pages", "default": "text"},
            },
            "required": ["url"],
        },
    },
]

# ─── App ─────────────────────────────────────────────────────

app = FastAPI(title="OpenRouter MCP Server", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Helpers ─────────────────────────────────────────────────

async def _router_chat(
    messages: list[dict],
    stream: bool = False,
    model: Optional[str] = None,
) -> Any:
    payload = {"messages": messages, "stream": stream, "use_memory": True}
    if model:
        payload["model"] = model

    async with httpx.AsyncClient(timeout=120.0) as client:
        if stream:
            resp = await client.send(
                client.build_request(
                    "POST", f"{ROUTER_URL}/v1/chat/completions", json=payload
                ),
                stream=True,
            )
            return resp
        resp = await client.post(
            f"{ROUTER_URL}/v1/chat/completions", json=payload
        )
        resp.raise_for_status()
        return resp.json()


def _extract_content(response: dict) -> str:
    return (
        response.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )


async def _stream_text(http_response) -> AsyncGenerator[str, None]:
    async for chunk in http_response.aiter_text():
        for line in chunk.split("\n"):
            if line.startswith("data: ") and line != "data: [DONE]":
                try:
                    data  = json.loads(line[6:])
                    delta = data["choices"][0].get("delta", {}).get("content", "")
                    if delta:
                        yield delta
                except Exception:
                    pass

# ─── Tool Handlers ───────────────────────────────────────────

async def handle_chat(args: dict) -> dict:
    messages: list[dict] = []

    if sys_prompt := args.get("system_prompt"):
        messages.append({"role": "system", "content": sys_prompt})

    # Build user content: text + optional image
    if image_url := args.get("image_url"):
        content = [
            {"type": "text",      "text": args["message"]},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": args["message"]})

    stream = args.get("stream", False)
    if stream:
        http_resp = await _router_chat(messages, stream=True)
        full = []
        async for token in _stream_text(http_resp):
            full.append(token)
        return {"type": "text", "text": "".join(full)}

    resp = await _router_chat(messages)
    return {
        "type":    "text",
        "text":    _extract_content(resp),
        "model":   resp.get("_routing", {}).get("used", "unknown"),
        "usage":   resp.get("usage", {}),
    }


async def handle_complete_code(args: dict) -> dict:
    code     = args["code"]
    lang     = args.get("language", "")
    context  = args.get("context", "")
    instr    = args.get("instruction", "Complete or improve this code.")

    system = (
        "You are an expert software engineer. "
        "Return ONLY the code without markdown fences unless asked. "
        "Preserve the existing code style."
    )
    user_parts = []
    if lang:
        user_parts.append(f"Language: {lang}")
    if context:
        user_parts.append(f"Context:\n{context}")
    user_parts.append(f"Instruction: {instr}")
    user_parts.append(f"Code:\n```{lang}\n{code}\n```")

    messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": "\n\n".join(user_parts)},
    ]
    resp = await _router_chat(messages)
    return {
        "type":  "text",
        "text":  _extract_content(resp),
        "model": resp.get("_routing", {}).get("used", "unknown"),
    }


async def handle_analyze_image(args: dict) -> dict:
    question = args.get("question", "Describe this image in detail.")
    image    = args["image"]

    messages = [{
        "role":    "user",
        "content": [
            {"type": "text",      "text": question},
            {"type": "image_url", "image_url": {"url": image}},
        ],
    }]
    # Force vision model
    resp = await _router_chat(messages, model="qwen/qwen3-vl")
    return {
        "type":  "text",
        "text":  _extract_content(resp),
        "model": "qwen/qwen3-vl",
    }


async def handle_search_memory(args: dict) -> dict:
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{MEMORY_URL}/search",
            json={"query": args["query"], "limit": args.get("limit", 5)},
        )
        if resp.status_code != 200:
            return {"type": "text", "text": "Memory service unavailable"}
        memories = resp.json().get("memories", [])

    if not memories:
        return {"type": "text", "text": "No relevant memories found."}

    lines = [f"Found {len(memories)} relevant memories:\n"]
    for i, m in enumerate(memories, 1):
        lines.append(f"{i}. [score={m['score']:.3f}]")
        lines.append(f"   Q: {m['query'][:120]}")
        lines.append(f"   A: {m['response'][:200]}")
    return {"type": "text", "text": "\n".join(lines)}


async def handle_route_info(args: dict) -> dict:
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{ROUTER_URL}/route-info",
            json={"messages": [{"role": "user", "content": args["message"]}]},
        )
        if resp.status_code != 200:
            return {"type": "text", "text": "Router unavailable"}
        info = resp.json()

    text = (
        f"Routing decision for your request:\n"
        f"  Selected model : {info['selected_model']}\n"
        f"  Has image      : {info['has_image']}\n"
        f"  Is complex     : {info['is_complex']}\n"
        f"  Word count     : {info['word_count']} (threshold: {info['threshold']})"
    )
    return {"type": "text", "text": text}


async def handle_web_search(args: dict) -> dict:
    query       = args["query"]
    max_results = int(args.get("max_results", 5))

    if len(query) > 300:
        return {
            "type": "text",
            "text": (
                f"Query too long ({len(query)} chars, max 300). "
                "Please provide a short search term, not file content or code."
            ),
        }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{SEARXNG_URL}/search",
            params={"q": query, "format": "json", "categories": "general"},
        )
        if resp.status_code != 200:
            return {"type": "text", "text": f"SearXNG unavailable (HTTP {resp.status_code})"}
        data = resp.json()

    results = data.get("results", [])[:max_results]
    if not results:
        return {"type": "text", "text": f"No results found for: {query}"}

    lines = [f"Web search results for: **{query}**\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r.get('title', 'No title')}**")
        lines.append(f"   {r.get('url', '')}")
        if snippet := r.get("content", ""):
            lines.append(f"   {snippet[:200]}")
        lines.append("")
    return {"type": "text", "text": "\n".join(lines)}


async def handle_screenshot(args: dict) -> dict:
    url  = args["url"]
    mode = args.get("mode", "text")

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{ROUTER_URL}/v1/fetch",
            params={"url": url, "mode": mode},
        )
        if resp.status_code != 200:
            return {"type": "text", "text": f"Could not fetch {url} (HTTP {resp.status_code})"}
        data = resp.json()

    content    = data.get("content", "")
    is_visual  = data.get("is_visual", False)

    if is_visual and mode == "screenshot":
        return {
            "type": "text",
            "text": (
                f"Screenshot of {url} (base64 JPEG):\n"
                f"data:image/jpeg;base64,{content}"
            ),
        }
    return {"type": "text", "text": f"Content of {url}:\n\n{content}"}


TOOL_HANDLERS = {
    "chat":           handle_chat,
    "complete_code":  handle_complete_code,
    "analyze_image":  handle_analyze_image,
    "search_memory":  handle_search_memory,
    "route_info":     handle_route_info,
    "web_search":     handle_web_search,
    "screenshot":     handle_screenshot,
}

# ─── MCP HTTP Endpoints ──────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "mcp-server"}


@app.get("/mcp/tools")
async def list_tools():
    """MCP tools/list endpoint."""
    return {"tools": TOOLS}


@app.post("/mcp/tools/{tool_name}")
async def call_tool(tool_name: str, request: Request):
    """MCP tools/call endpoint."""
    body = await request.json()
    args = body.get("arguments", body)

    if tool_name not in TOOL_HANDLERS:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")

    try:
        result = await TOOL_HANDLERS[tool_name](args)
        return {"content": [result]}
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail="Router service unavailable. Is docker-compose up?"
        )
    except Exception as e:
        log.exception("Tool %s failed", tool_name)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/mcp")
async def mcp_jsonrpc(request: Request):
    """
    JSON-RPC 2.0 handler for MCP protocol.
    Compatible with VSCode Continue extension.
    """
    body = await request.json()
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    def ok(result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def err(code: int, msg: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": msg}}

    if method == "initialize":
        return JSONResponse(ok({
            "protocolVersion": "2024-11-05",
            "capabilities":    {"tools": {}},
            "serverInfo":      {"name": "openrouter-mcp", "version": "1.0.0"},
        }))

    elif method == "tools/list":
        return JSONResponse(ok({"tools": TOOLS}))

    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        if tool_name not in TOOL_HANDLERS:
            return JSONResponse(err(-32601, f"Unknown tool: {tool_name}"))
        try:
            result = await TOOL_HANDLERS[tool_name](arguments)
            return JSONResponse(ok({"content": [result]}))
        except Exception as e:
            return JSONResponse(err(-32603, str(e)))

    return JSONResponse(err(-32601, f"Unknown method: {method}"))


# ─── Stdio MCP Mode ──────────────────────────────────────────

async def stdio_mcp_loop() -> None:
    """
    Run MCP over stdio for direct VSCode integration.
    VSCode spawns this process and communicates via stdin/stdout.
    """
    loop = asyncio.get_event_loop()

    async def readline() -> str:
        return await loop.run_in_executor(None, sys.stdin.readline)

    while True:
        line = await readline()
        if not line:
            break
        try:
            msg    = json.loads(line.strip())
            method = msg.get("method", "")
            params = msg.get("params", {})
            req_id = msg.get("id")

            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities":    {"tools": {}},
                    "serverInfo":      {"name": "openrouter-mcp-stdio", "version": "1.0.0"},
                }
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                if tool_name in TOOL_HANDLERS:
                    content = await TOOL_HANDLERS[tool_name](arguments)
                    result  = {"content": [content]}
                else:
                    result = {"error": f"Unknown tool: {tool_name}"}
            else:
                result = {"error": f"Unknown method: {method}"}

            resp = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})
            print(resp, flush=True)

        except Exception as e:
            err_resp = json.dumps({
                "jsonrpc": "2.0",
                "id":      None,
                "error":   {"code": -32603, "message": str(e)},
            })
            print(err_resp, flush=True)


if __name__ == "__main__":
    if "--stdio" in sys.argv:
        asyncio.run(stdio_mcp_loop())
    else:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)

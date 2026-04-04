"""
bash_executor.py — Isolated bash execution service for LLM tool calling.

Provides a persistent bash environment:
- Internet access (curl, wget, ssh, etc.)
- Common dev tools pre-installed, more installable via apt/pip/npm
- Working directory persists between calls (saved to /workspace/.cwd)
- Output hard-capped at MAX_OUTPUT_LINES to protect LLM context windows
- /reset clears working directory state
"""

import asyncio
import os
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Bash Executor", version="1.0.0")

MAX_LINES    = int(os.getenv("MAX_OUTPUT_LINES", "50"))
DEFAULT_TIMEOUT = int(os.getenv("DEFAULT_TIMEOUT", "30"))
MAX_TIMEOUT  = 120
WORKSPACE    = "/workspace"
CWD_FILE     = f"{WORKSPACE}/.cwd"

os.makedirs(WORKSPACE, exist_ok=True)


class ExecRequest(BaseModel):
    command: str
    timeout: Optional[int] = None


def _get_cwd() -> str:
    try:
        cwd = open(CWD_FILE).read().strip()
        return cwd if cwd and os.path.isdir(cwd) else WORKSPACE
    except FileNotFoundError:
        return WORKSPACE


def _truncate(text: str, max_lines: int) -> tuple[str, bool]:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, False
    trimmed = "\n".join(lines[:max_lines])
    return (
        trimmed + f"\n[... {len(lines)} Zeilen gesamt — zeige erste {max_lines}. "
        "Nutze head/tail/grep für gezielte Ausgabe.]",
        True,
    )


@app.post("/exec")
async def execute(req: ExecRequest):
    timeout = min(req.timeout or DEFAULT_TIMEOUT, MAX_TIMEOUT)
    cwd = _get_cwd()

    # Script: cd to last known directory, run command, save new cwd
    script = f"""#!/bin/bash
set -o pipefail
cd {cwd!r} 2>/dev/null || cd {WORKSPACE!r}
{req.command}
__exit=$?
pwd > {CWD_FILE!r}
exit $__exit
"""
    try:
        proc = await asyncio.create_subprocess_shell(
            "bash",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=script.encode()),
            timeout=timeout,
        )

        stdout_str, s_trunc = _truncate(stdout.decode(errors="replace"), MAX_LINES)
        stderr_str, e_trunc = _truncate(stderr.decode(errors="replace"), MAX_LINES // 2)

        return JSONResponse({
            "stdout":    stdout_str,
            "stderr":    stderr_str,
            "exit_code": proc.returncode,
            "truncated": s_trunc or e_trunc,
            "cwd":       _get_cwd(),
        })

    except asyncio.TimeoutError:
        return JSONResponse({
            "stdout":    "",
            "stderr":    f"[Timeout nach {timeout}s — Befehl abgebrochen]",
            "exit_code": -1,
            "truncated": False,
            "cwd":       cwd,
        })
    except Exception as e:
        return JSONResponse({
            "stdout":    "",
            "stderr":    str(e),
            "exit_code": -1,
            "truncated": False,
            "cwd":       cwd,
        })


@app.post("/reset")
async def reset():
    """Reset working directory to /workspace."""
    try:
        os.remove(CWD_FILE)
    except FileNotFoundError:
        pass
    return {"status": "ok", "message": "Bash-Session zurückgesetzt"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "bash-executor"}

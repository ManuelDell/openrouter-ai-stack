"""MCP Service entrypoint — re-exports vscode_integration as a containerized service."""
import sys
import os

# Add parent path so we can import the main vscode_integration
sys.path.insert(0, "/app")

# Override ports from env
os.environ.setdefault("MCP_PORT", "8082")

from vscode_integration import app  # noqa: F401 — uvicorn imports this

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8082")))

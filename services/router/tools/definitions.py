"""Tool schemas for LLM function calling (OpenAI-compatible format).

These definitions are sent to the LLM with every request. The LLM decides
autonomously which tool to call — no keyword matching needed.
"""

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information, news, recent events, "
                "or any query that requires up-to-date data. "
                "Use this whenever the user asks about something that may have changed recently, "
                "explicitly requests a web search, or asks about current events."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to look up on the web",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "Generate an image from a text description using AI image generation. "
                "Use this when the user asks to draw, paint, sketch, create, or generate "
                "an image, illustration, photo, or any visual content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detailed description of the image to generate",
                    }
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Execute bash commands in a persistent isolated environment with full internet access. "
                "Use for: curl/wget API calls, file operations, git commands, package installation "
                "(apt/pip/npm), log analysis, running scripts, SSH, and any shell task. "
                "IMPORTANT: Output is hard-capped at 50 lines to protect your context window. "
                "For large outputs (logs, files) always pipe through head/tail/grep — e.g. "
                "'journalctl -n 50' or 'cat file | grep error | head -20'. "
                "The working directory persists between calls. Installed packages persist for the session."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "The bash command to execute. "
                            "Chain commands with && for sequences. "
                            "Always limit log/file output to avoid context overflow."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 30, max: 120). Use higher values for installs or slow commands.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reset_bash",
            "description": (
                "Reset the bash environment to a clean state. "
                "Use this if the shell is in a broken or unexpected state "
                "and commands are failing for no apparent reason."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def flowai_server_config() -> dict[str, Any]:
    """Build a Codex thread config that registers FlowAI's MCP server."""
    return {
        "mcp_servers": {
            "flowai": {
                "command": sys.executable,
                "args": ["-m", "flowai.mcp"],
                "env": {"PYTHONPATH": str(PROJECT_ROOT)},
            }
        }
    }

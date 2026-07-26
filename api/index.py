from __future__ import annotations

import os


os.environ.setdefault("FAIRPOST_MCP_PATH", "/api/mcp")

from mcp_server.remote import app  # noqa: E402,F401

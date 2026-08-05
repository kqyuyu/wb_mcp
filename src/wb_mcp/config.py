"""Configuration loading for wb-mcp."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    """Runtime settings read from environment variables.

    Attributes:
        api_key: Wildberries API key (``WB_API_KEY``, legacy alias
            ``WILDBERRIES_API_KEY``). One key per token category; methods
            that need a different category require that key in their own
            env var (e.g. ``WB_CONTENT_API_KEY``).
        confirm_write: master switch — when False, wb_call_method refuses
            write/destructive calls even if confirm flags are passed.
            Set ``WB_CONFIRM_WRITE=1`` to enable.
        server_name: MCP server name.
        max_methods: cap on registered tools (0 = no cap).
        enable_dynamic_tools: if True, register per-method tool funcs.
    """

    api_key: str | None = None
    confirm_write: bool = False
    server_name: str = "wildberries-mcp"
    max_methods: int = 0
    enable_dynamic_tools: bool = False
    sandbox: bool = False


def load_settings() -> Settings:
    api_key = os.getenv("WB_API_KEY") or os.getenv("WILDBERRIES_API_KEY") or None
    confirm_write = os.getenv("WB_CONFIRM_WRITE", "0") in ("1", "true", "True")
    server_name = os.getenv("WB_MCP_SERVER_NAME", "wildberries-mcp")
    raw_max = os.getenv("WB_MCP_MAX_METHODS", "0")
    try:
        max_methods = int(raw_max)
    except ValueError:
        max_methods = 0
    enable_dynamic_tools = os.getenv("WB_MCP_DYNAMIC_TOOLS", "0") in ("1", "true", "True")
    sandbox = os.getenv("WB_SANDBOX", "0") in ("1", "true", "True")
    return Settings(
        api_key=api_key,
        confirm_write=confirm_write,
        server_name=server_name,
        max_methods=max_methods,
        enable_dynamic_tools=enable_dynamic_tools,
        sandbox=sandbox,
    )

"""Async transport for the Wildberries API.

The transport layer is intentionally a clean island inside the package: it has
no MCP dependencies and can be lifted into a future backend by importing
`from wb_mcp.transport import WBClient`.
"""

from wb_mcp.transport.wb import WBClient

__all__ = ["WBClient"]
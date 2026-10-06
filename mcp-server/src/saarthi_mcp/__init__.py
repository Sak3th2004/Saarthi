"""Saarthi MCP server — the Alexa+ integration surface.

Exposes the AGENTS.md §5 tool contract over Streamable HTTP. Call ``build_server()``
to create an independent server. There is no process-global ``mcp`` instance;
embedded callers must retain the instance returned by the factory.
"""

from saarthi_mcp.server import build_server

__all__ = ["build_server"]

__version__ = "0.1.0"

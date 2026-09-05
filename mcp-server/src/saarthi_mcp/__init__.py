"""Saarthi MCP server — the Alexa+ integration surface.

Exposes the AGENTS.md §5 tool contract over Streamable HTTP. In Week 1 the tools are backed by
an in-memory repository; the same ``HouseholdRepository`` interface is implemented by the Neo4j
graph in Week 2 without changing the tool contract.
"""

from saarthi_mcp.server import build_server, mcp

__all__ = ["build_server", "mcp"]

__version__ = "0.1.0"

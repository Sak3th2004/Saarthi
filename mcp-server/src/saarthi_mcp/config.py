"""Runtime configuration, read from the environment (never from code)."""

from __future__ import annotations

import os
from dataclasses import dataclass

# The MCP spec version we target (AGENTS.md §5). The actually-negotiated version depends on the
# installed `mcp`/`fastmcp` release; we assert the handshake in tests and log any gap in
# FRICTION_LOG.md (#001).
TARGET_MCP_SPEC = "2025-11-25"

# A conservative floor the installed library is expected to meet or exceed. Kept separate from
# TARGET so the test proves a modern handshake without silently going red when the library caps
# below the (newer) target date.
MIN_MCP_SPEC = "2025-06-18"


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    backend: str  # "memory" (Week 1) | "neo4j" (Week 2+)
    path: str = "/mcp"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"


def load_settings() -> Settings:
    return Settings(
        host=os.getenv("SAARTHI_HOST", "127.0.0.1"),
        port=int(os.getenv("SAARTHI_PORT", "8080")),
        backend=os.getenv("SAARTHI_BACKEND", "memory").lower(),
        path=os.getenv("SAARTHI_MCP_PATH", "/mcp"),
    )

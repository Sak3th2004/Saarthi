"""Runtime configuration, read from the environment (never from code)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # Load mcp-server/.env if python-dotenv is available (dev convenience; prod uses real env).
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:  # pragma: no cover - dotenv is a dev dependency
    pass

# The MCP spec version we target (AGENTS.md §5). The actually-negotiated version depends on the
# installed `mcp`/`fastmcp` release; we assert the handshake in tests and log any gap in
# FRICTION_LOG.md (#001).
TARGET_MCP_SPEC = "2025-11-25"
DEFAULT_BEDROCK_MODEL_ID = ""

# A conservative floor the installed library is expected to meet or exceed. Kept separate from
# TARGET so the test proves a modern handshake without silently going red when the library caps
# below the (newer) target date.
MIN_MCP_SPEC = "2025-06-18"


@dataclass(frozen=True)
class Neo4jSettings:
    uri: str
    username: str
    password: str
    database: str


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    backend: str  # "memory" (Week 1) | "neo4j" (Week 2+)
    path: str = "/mcp"
    neo4j: Neo4jSettings | None = None
    agent_mode: str = "off"
    aws_region: str = "us-east-1"
    bedrock_model_id: str = DEFAULT_BEDROCK_MODEL_ID

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"


def load_settings() -> Settings:
    backend = os.getenv("SAARTHI_BACKEND", "memory").lower()
    agent_mode = os.getenv("SAARTHI_AGENTS", "off").lower()
    if agent_mode not in {"off", "bedrock"}:
        raise ValueError("SAARTHI_AGENTS must be 'off' or 'bedrock'.")
    model_id = os.getenv("BEDROCK_MODEL_ID", DEFAULT_BEDROCK_MODEL_ID).strip()
    if agent_mode == "bedrock" and not model_id:
        raise ValueError("Set BEDROCK_MODEL_ID to an explicitly selected, tested Bedrock model.")
    neo4j = None
    if backend == "neo4j":
        neo4j = Neo4jSettings(
            uri=os.environ["NEO4J_URI"],
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=os.environ["NEO4J_PASSWORD"],
            database=os.getenv("NEO4J_DATABASE", "neo4j"),
        )
    return Settings(
        host=os.getenv("SAARTHI_HOST", "127.0.0.1"),
        port=int(os.getenv("SAARTHI_PORT", "8080")),
        backend=backend,
        path=os.getenv("SAARTHI_MCP_PATH", "/mcp"),
        neo4j=neo4j,
        agent_mode=agent_mode,
        aws_region=os.getenv("AWS_REGION", "us-east-1"),
        bedrock_model_id=model_id,
    )

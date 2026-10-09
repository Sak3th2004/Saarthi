"""Repeatable backend checks from any working directory.

Install from repository root with Python 3.11+ in a virtual environment:
    python -m pip install --upgrade pip
    python -m pip install -c constraints.txt mcp
    python -m pip install -c constraints.txt -e ./agents -e ./mcp-server[dev,agents]

Run offline: python scripts/check.py
Real database/HTTP restart tests: python scripts/check.py --neo4j
Paid, opt-in model integration:
    python scripts/check.py --neo4j --bedrock-model moonshotai.kimi-k2.5

Only the dedicated, disposable local test graph is reset. Personal environment
variables are overridden for child processes, never rewritten in .env or AWS config.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--neo4j", action="store_true", help="Start and stop the disposable test graph")
    parser.add_argument("--bedrock-model", help="Explicit model ID; enabling this spends inference credits")
    args = parser.parse_args()
    if args.bedrock_model is not None and (not args.neo4j or not args.bedrock_model.strip()):
        parser.error("A nonempty --bedrock-model requires --neo4j.")
    root = Path(__file__).resolve().parents[1]
    temp = root / (".test-temp-" + uuid4().hex)
    if temp.resolve().parent != root:
        raise RuntimeError("Test temporary directory must stay inside the project.")
    env = dict(os.environ, SAARTHI_AGENTS="off", SAARTHI_BACKEND="memory",
               SAARTHI_RUN_NEO4J_TESTS="0", SAARTHI_RUN_BEDROCK_TESTS="0")
    env["SAARTHI_HOUSEHOLD_FILE"] = ""
    env["SAARTHI_LOCAL_SETUP"] = "0"
    env["SAARTHI_GOOGLE_CONFIG"] = ""
    compose = ["docker", "compose", "-f", str(root / "memory/docker-compose.test.yml")]
    try:
        if args.neo4j:
            subprocess.run([*compose, "up", "-d", "--wait"], cwd=root, check=True, timeout=120)
            env.update(SAARTHI_BACKEND="neo4j", NEO4J_URI="bolt://127.0.0.1:17687",
                       NEO4J_USERNAME="neo4j", NEO4J_PASSWORD="saarthi-test-only",
                       NEO4J_DATABASE="neo4j", SAARTHI_RUN_NEO4J_TESTS="1")
        if args.bedrock_model:
            env.update(BEDROCK_MODEL_ID=args.bedrock_model.strip(), AWS_REGION="us-east-1",
                       SAARTHI_RUN_BEDROCK_TESTS="1")
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-c", "mcp-server/pyproject.toml",
             "mcp-server/tests", "agents/tests", "-q", "--tb=short", "--basetemp", str(temp)],
            cwd=root, env=env, check=False,
        ).returncode
    finally:
        if args.neo4j:
            subprocess.run([*compose, "stop"], cwd=root, check=True, timeout=60)


if __name__ == "__main__":
    raise SystemExit(main())

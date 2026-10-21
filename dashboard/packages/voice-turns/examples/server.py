"""Local-only real MCP example. Reads notes you explicitly saved, not fixtures.

python examples/server.py --save-note "My spare key is in the blue cabinet."
python examples/server.py
Requires the official MCP Python SDK: python -m pip install -r examples/requirements.txt
The local SQLite file contains the exact text you save. Do not use sensitive data
or expose this unauthenticated educational server to a network.
"""
import argparse
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

path = Path(__file__).with_name("notes.sqlite3")
@contextmanager
def connection():
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()

mcp = MCPServer("Voice turns local example")

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False), structured_output=True)
def read_note(question: str) -> dict[str, Any]:
    """Find an exact phrase in user-saved local notes. No generated answers or writes."""
    query = question.strip()
    if not query or len(query) > 500:
        raise ValueError("Search with 1 to 500 characters.")
    with connection() as db:
        rows = db.execute("SELECT id, text FROM notes WHERE instr(lower(text), lower(?)) > 0 ORDER BY id DESC LIMIT 3", (query,)).fetchall()
    return {"speech": rows[0][1] if rows else "No matching note has been saved.",
            "action": None, "evidence": [{"id": row[0], "text": row[1]} for row in rows]}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save-note")
    args = parser.parse_args()
    with connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)")
    if args.save_note is not None:
        if not 1 <= len(args.save_note.strip()) <= 2000:
            parser.error("Save between 1 and 2000 characters.")
        with connection() as db:
            db.execute("INSERT INTO notes(text) VALUES (?)", (args.save_note.strip(),))
        print("Your note was saved locally.")
    else:
        mcp.run(transport="streamable-http", host="127.0.0.1", port=8765, stateless_http=True, json_response=True)

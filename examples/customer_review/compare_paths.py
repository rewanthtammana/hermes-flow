"""Replay saved native history and simulate one new connection in a DB copy."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
from hermes_flow.source import read_database


def copy_sessions(source, target, sid):
    """Copy just this demo's metadata into a minimal comparison database."""
    rows = read_database(source, [sid])
    columns = ("id", "title", "source", "parent_session_id", "started_at", "ended_at", "model_config")
    message_columns = ("role", "timestamp", "tool_calls", "tool_call_id", "tool_name", "active", "compacted")
    with closing(sqlite3.connect(target)) as db, db:
        db.executescript('CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT,source TEXT,parent_session_id TEXT,started_at REAL,ended_at REAL,model_config TEXT);'
                         'CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,timestamp REAL,tool_calls TEXT,tool_call_id TEXT,tool_name TEXT,active INTEGER DEFAULT 1,compacted INTEGER DEFAULT 0);')
        for row in rows:
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?)", [row.get(c) for c in columns])
            for message in row["messages"]:
                values = [message.get(c, True if c == "active" else False if c == "compacted" else None) for c in message_columns]
                db.execute("INSERT INTO messages(session_id," + ",".join(message_columns) + ") VALUES(?,?,?,?,?,?,?,?)", [row["id"], *values])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path, help="Completed customer-review run directory")
    parser.add_argument("--directory", type=Path, help="New output directory; defaults to a temporary directory")
    args = parser.parse_args()
    run = args.run.resolve()
    metadata = json.loads((run / "run.json").read_text())
    sid = metadata["root_session_id"]
    source = Path(metadata.get("database", run / "profiles/demo/state.db"))
    output = args.directory.resolve() if args.directory else Path(tempfile.mkdtemp(prefix="hermes-flow-trusted-paths-")) / "demo"
    output.mkdir(parents=True, exist_ok=False)
    trusted = output / "trusted-paths.json"

    def flow(*flags):
        subprocess.run([sys.executable, "-m", "hermes_flow", *map(str, flags)], cwd=PROJECT, check=True)

    flow("baseline", "create", "--db", source, "--session-id", sid, "--out", trusted)
    baseline_bytes = trusted.read_bytes()
    flow("--db", source, "--session-id", sid, "--compare-with-trusted-paths", trusted,
         "--graph", "none", "--out", output / "trusted.html")
    copy = output / "simulated-state.db"
    copy_sessions(source, copy, sid)
    with closing(sqlite3.connect(copy)) as changed, changed:
        moment = changed.execute("SELECT MAX(timestamp) FROM messages").fetchone()[0] + 1
        calls = json.dumps([{"id": "demo-extra-call", "function": {"name": "demo_unapproved_tool", "arguments": "{}"}}])
        changed.execute("INSERT INTO messages(session_id,role,timestamp,tool_calls) VALUES(?,?,?,?)",
                        (sid, "assistant", moment, calls))
        changed.execute("INSERT INTO messages(session_id,role,timestamp,tool_call_id,tool_name) VALUES(?,?,?,?,?)",
                        (sid, "tool", moment + .1, "demo-extra-call", "demo_unapproved_tool"))
    flow("--db", copy, "--session-id", sid, "--compare-with-trusted-paths", trusted,
         "--graph", "none", "--out", output / "deviation.html")
    assert trusted.read_bytes() == baseline_bytes
    print(f"\nDemo reports: {output}\ntrusted.html: original recorded connections\ndeviation.html: simulated coordinator → demo_unapproved_tool connection in red\nNo agents or model calls were launched; the source database and baseline were unchanged.")


if __name__ == "__main__":
    main()

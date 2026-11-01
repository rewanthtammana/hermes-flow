"""Input adapters. Database reads use a read-only transaction and omit message bodies."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def read_file(path):
    text = Path(path).read_text(encoding="utf-8")
    try:
        value = json.loads(text)
        rows = value if isinstance(value, list) else [value]
    except json.JSONDecodeError:
        rows = []
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                raise ValueError(f"{path}: line {n}: expected JSONL; use a Hermes JSONL export or the event format in README.md") from None
    if not rows or any(not isinstance(r, dict) for r in rows):
        raise ValueError("Input must contain session objects or tool event objects")
    return rows


@contextmanager
def database(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"No Hermes database at {path}. Pass --db PATH or a JSONL file.")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        yield conn
    finally:
        conn.close()


def session_index(conn):
    columns = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    wanted = [k for k in ("id", "title", "source", "agent_role", "parent_session_id", "parent_call_id", "started_at", "ended_at", "end_reason", "model_config", "profile_name", "cwd", "git_repo_root", "last_activity_at") if k in columns]
    if "id" not in wanted or "started_at" not in wanted:
        raise ValueError("Unsupported Hermes database: sessions needs id and started_at")
    return [dict(r) for r in conn.execute("SELECT " + ",".join(wanted) + " FROM sessions ORDER BY started_at DESC")]


def session_listing(conn, limit):
    """Add activity metadata for the listing without reading message bodies."""
    from .core import timestamp
    rows = session_index(conn)[:limit]
    columns = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    for row in rows:
        message_time = None
        if {"session_id", "timestamp"} <= columns:
            message_time = conn.execute("SELECT MAX(timestamp) FROM messages WHERE session_id=?", (row["id"],)).fetchone()[0]
        candidates = [timestamp(v) for v in (row.get("last_activity_at"), message_time)]
        valid = [v for v in candidates if v is not None]
        row["last_active"] = max(valid) if valid else timestamp(row.get("started_at"))
    return rows


def read_database(path, session_ids=(), *, all_sessions=False):
    if all_sessions and session_ids:
        raise ValueError("Choose selected sessions or all sessions")
    with database(path) as conn:
        rows = session_index(conn)
        if not rows:
            raise ValueError("No recorded Hermes sessions found")
        # Choose the latest root by default, and include every recorded descendant.
        roots = list(session_ids) or [next((r["id"] for r in rows if not r.get("parent_session_id")), rows[0]["id"])]
        known = {r["id"] for r in rows}
        if all_sessions:
            roots = list(known)
        missing = set(roots) - known
        if missing:
            raise ValueError("Unknown session ID: " + ", ".join(sorted(missing)))
        children = {}
        for row in rows:
            children.setdefault(row.get("parent_session_id"), []).append(row["id"])
        selected, pending = set(), roots[:]
        while pending:
            sid = pending.pop()
            if sid not in selected:
                selected.add(sid)
                pending.extend(children.get(sid, []))
        columns = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        wanted = [k for k in ("role", "timestamp", "tool_calls", "tool_call_id", "tool_name", "active", "compacted") if k in columns]
        if not {"role", "tool_calls", "tool_call_id"}.issubset(columns):
            raise ValueError("Unsupported Hermes messages schema")
        result = []
        for row in reversed(rows):
            if row["id"] in selected:
                row["messages"] = [dict(m) for m in conn.execute(
                    "SELECT " + ",".join(wanted) + " FROM messages WHERE session_id=? ORDER BY timestamp,id", (row["id"],))]
                result.append(row)
        return result

"""Local tools for a fictional customer review; no network or shell execution."""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

ARTIFACTS = {"analysis.json", "privacy.json", "draft.md", "review.json", "report.md"}
LOCK = threading.Lock()


def seed(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory / "customers.db")) as db, db:
        db.executescript("""
        CREATE TABLE customers (
          id INTEGER PRIMARY KEY, name TEXT, email TEXT, national_id TEXT,
          bank_account TEXT, annual_income INTEGER, balance INTEGER, support_note TEXT);
        CREATE TABLE invoices (id INTEGER PRIMARY KEY, customer_id INTEGER,
          amount INTEGER, overdue_days INTEGER);
        """)
        db.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?)", [
            (1, "Alice Example", "alice@example.test", "000-00-0001", "DEMO-ACCT-0001", 82000, 12500, "SYNTHETIC: temporary hardship"),
            (2, "Bob Example", "bob@example.test", "000-00-0002", "DEMO-ACCT-0002", 64000, 8400, "SYNTHETIC: billing address changed"),
            (3, "Casey Example", "casey@example.test", "000-00-0003", "DEMO-ACCT-0003", 96000, 17200, "SYNTHETIC: accessibility preference"),
        ])
        db.executemany("INSERT INTO invoices VALUES (?,?,?,?)", [(1, 1, 200, 35), (2, 2, 150, 10), (3, 3, 400, 0)])
    artifacts = directory / "artifacts"
    artifacts.mkdir()


def dispatch(name, args, directory=None):
    root = Path(directory or os.environ["CUSTOMER_REVIEW_DATA"]).resolve()
    try:
        if name == "demo_customer_lookup":
            with closing(sqlite3.connect((root / "customers.db").as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                row = db.execute("SELECT * FROM customers WHERE id=?", (args["customer_id"],)).fetchone()
            result = {"synthetic": True, "customer": dict(row) if row else None}
        elif name == "demo_portfolio_summary":
            with closing(sqlite3.connect((root / "customers.db").as_uri() + "?mode=ro", uri=True)) as db:
                count, balance = db.execute("SELECT COUNT(*),SUM(balance) FROM customers").fetchone()
                overdue, amount = db.execute("SELECT COUNT(*),SUM(amount) FROM invoices WHERE overdue_days>0").fetchone()
            result = {"synthetic": True, "customers": count, "total_balance": balance,
                      "overdue_invoices": overdue, "overdue_amount": amount}
        elif name == "demo_redact":
            text = args["text"]
            for pattern in (r"[\w.+-]+@[\w.-]+\.[A-Za-z]+", r"\b\d{3}-\d{2}-\d{4}\b", r"DEMO-ACCT-\d+"):
                text = re.sub(pattern, "[MASKED]", text)
            result = {"text": text, "note": "Masks demo email, national ID and account patterns only; not a general DLP system."}
        elif name in {"demo_artifact_read", "demo_artifact_write"}:
            filename = args["filename"]
            if filename not in ARTIFACTS:
                raise ValueError("Unknown artifact; use a declared artifact filename")
            path = root / "artifacts" / filename
            if name == "demo_artifact_write":
                content = args["content"]
                if len(content) > 24000:
                    raise ValueError("Demo artifacts are limited to 24000 characters")
                path.write_text(content, encoding="utf-8")
                result = {"written": filename, "bytes": path.stat().st_size}
            else:
                result = {"filename": filename, "content": path.read_text(encoding="utf-8")}
        elif name == "demo_audit_receipt":
            result = {"artifacts": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in sorted((root / "artifacts").iterdir()) if p.is_file()}}
        else:
            raise ValueError("Unknown demo tool")
        status = "ok"
    except (KeyError, ValueError, OSError, sqlite3.Error) as exc:
        status, result = "error", {"error": str(exc)}
    # Supplemental receipts intentionally omit arguments and result bodies.
    with LOCK, (root / "tool-receipts.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"timestamp": time.time(), "tool": name, "status": status}) + "\n")
    return json.dumps(result)


def register(ctx):
    specs = [
        ("demo_customer_lookup", "Read one fictional sensitive customer record from SQLite.", {"customer_id": {"type": "integer"}}),
        ("demo_portfolio_summary", "Query aggregate customer balances and overdue invoices in SQLite.", {}),
        ("demo_redact", "Mask demo email, national ID and bank account patterns in text.", {"text": {"type": "string"}}),
        ("demo_artifact_read", "Read a declared local handoff artifact.", {"filename": {"type": "string", "enum": sorted(ARTIFACTS)}}),
        ("demo_artifact_write", "Write a declared local handoff artifact.", {"filename": {"type": "string", "enum": sorted(ARTIFACTS)}, "content": {"type": "string"}}),
        ("demo_audit_receipt", "Compute SHA256 receipts for the current handoff artifacts.", {}),
    ]
    for name, description, properties in specs:
        def handler(args, _name=name, **kwargs):
            return dispatch(_name, args)
        ctx.register_tool(name=name, toolset="customer_review", handler=handler,
                          schema={"name": name, "description": description, "parameters": {
                              "type": "object", "properties": properties, "required": list(properties),
                              "additionalProperties": False}})

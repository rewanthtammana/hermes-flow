"""Observe recorded flows and optionally compare approved connections."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import webbrowser
from uuid import uuid4

from .core import normalize
from .graph import graph, safe
from .source import database, read_database, read_file, session_listing
from .formatting import session_table
from . import baseline


def session_ids(value):
    parts = [part.strip() for part in value.split(",")]
    if any(not part for part in parts):
        raise argparse.ArgumentTypeError("Use comma-separated session IDs, for example id1,id2")
    return list(dict.fromkeys(parts))


def selection_options(parser, required=False):
    selection = parser.add_mutually_exclusive_group(required=required)
    selection.add_argument("--session-id", dest="session", type=session_ids, default=[], metavar="ID[,ID...]", help="Show selected sessions and their descendants")
    selection.add_argument("--all-sessions", action="store_true", help="Show all recorded sessions (default)")
    parser.add_argument("--db", type=Path, help="Hermes database; defaults to the active profile")


def history_options(parser):
    selection_options(parser)
    parser.add_argument("--compare-with-trusted-paths", type=Path,
                        help="Highlight connections absent from an editable trusted-paths baseline")
    parser.add_argument("--json", action="store_true", help="Write metadata JSON instead of HTML")
    parser.add_argument("--out", type=Path, help="Output path; defaults to a unique timestamped report")
    parser.add_argument("--force", action="store_true", help="Replace the output report")
    parser.add_argument("--open", action="store_true", help="Open the HTML in your browser")
    parser.add_argument("--graph", nargs="?", const="text", choices=("text", "mermaid", "none"), default="text")


def setup_parser(parser):
    history_options(parser)
    parser.set_defaults(source=None)
    sub = parser.add_subparsers(dest="flow_command")
    sessions = sub.add_parser("sessions", help="List recorded Hermes sessions")
    sessions.add_argument("--db", type=Path)
    sessions.add_argument("--limit", type=int, default=20)
    baselines = sub.add_parser("baseline", help="Create editable trusted connections")
    actions = baselines.add_subparsers(dest="baseline_command", required=True)
    create = actions.add_parser("create", help="Collect candidate connections from recorded sessions")
    selection_options(create, required=True)
    create.add_argument("--out", required=True, type=Path,
                        help="New editable baseline JSON (for example trusted-paths.json); not an HTML report")
    examples = sub.add_parser("examples", help="List the included demonstrations and their requirements")
    show = sub.add_parser("show", help="Also accepts exported JSON/JSONL; optional for database viewing")
    history_options(show)
    show.add_argument("source", nargs="?", type=Path, help="JSON/JSONL input; defaults to local Hermes database")
    show.add_argument("--session", dest="legacy_sessions", action="append", default=[], help=argparse.SUPPRESS)
    demo = sub.add_parser("demo", help="Show a synthetic flow without launching agents")
    prototype = sub.add_parser("prototype", help="Execute five deterministic process agents and show their captured flow")
    prototype.add_argument("--directory", type=Path, help="New directory for events, receipts, artifacts, and HTML")
    for command in (demo,):
        command.add_argument("--out", type=Path, help="Output path; defaults to a unique timestamped report")
        command.add_argument("--force", action="store_true", help="Replace the output report")
    for command in (demo, prototype):
        command.add_argument("--open", action="store_true", help="Open the HTML in your browser")
        command.add_argument("--graph", nargs="?", const="text", choices=("text", "mermaid", "none"), default="text")


def default_db():
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / "state.db"


def observe(rows):
    return {**normalize(rows), "note": "Recorded lineage, handoffs, and tool calls."}


def demo_flow():
    rows = []
    for sid, parent, tools in (("orchestrator", None, ["delegate_task", "summarize"]),
                               ("researcher", "orchestrator", ["read_file", "delegate_task"]),
                               ("writer", "researcher", ["write_file"]),
                               ("auditor", "orchestrator", ["read_file"])):
        for i, tool in enumerate(tools):
            rows.append({"session_id": sid, "agent_role": sid, "parent_session_id": parent,
                         "tool": tool, "call_id": f"{sid}-{i}", "timestamp": 1792843200 + len(rows), "status": "ok"})
    return {**observe(rows), "demo": True}


def render(report):
    payload = json.dumps(report, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = Path(__file__).with_name("report.html").read_text(encoding="utf-8")
    script = Path(__file__).with_name("report_graph.js").read_text(encoding="utf-8")
    app = Path(__file__).with_name("report_app.js").read_text(encoding="utf-8")
    return template.replace("__FLOW_APP__", app).replace("__FLOW_GRAPH__", script).replace("__FLOW_DATA__", payload)


def run(args):
    if args.flow_command is None:
        args.flow_command = "show"
    args.session = list(dict.fromkeys(args.session + getattr(args, "legacy_sessions", [])))
    if args.all_sessions and args.session:
        raise ValueError("Choose --session-id or --all-sessions")
    if args.flow_command == "sessions":
        if args.limit < 1:
            raise ValueError("--limit must be positive")
        with database(args.db or default_db()) as conn:
            rows = session_listing(conn, args.limit)
        print(session_table(rows))
        return 0
    if args.flow_command == "examples":
        print("Hermes Flow examples")
        print("\n  demo\n    Safe synthetic flow; no authentication, model calls, or session changes.")
        print("    hermes flow demo --open")
        print("\n  live_interactions\n    Four real Hermes agents with correlated delegation handoffs; requires authentication.")
        print("    python3 examples/live_interactions/run.py")
        print("\n  customer_review\n    Five real Hermes agents, local synthetic customer data, skills, and artifacts; requires authentication.")
        print("    python3 examples/customer_review/run.py")
        print("\n  prototype\n    Five deterministic local worker processes; no model calls or authentication.")
        print("    hermes flow prototype --open")
        return 0
    if args.flow_command == "baseline":
        source = args.db or default_db()
        if args.out.suffix.lower() in (".html", ".htm"):
            raise ValueError("Baseline create writes JSON, not HTML; use --out trusted-paths.json. "
                             "To view a report, run hermes flow --compare-with-trusted-paths trusted-paths.json --open")
        if source.expanduser().resolve() == args.out.expanduser().resolve():
            raise ValueError("Baseline output must not replace the database")
        value = baseline.create(observe(read_database(source, args.session, all_sessions=args.all_sessions)))
        baseline.write(args.out.expanduser(), value)
        count = sum(1 for _ in baseline.expanded(value))
        print(f"{count} candidate connections grouped under {len(value['connections'])} sources from {len(value['source_sessions'])} sessions\n{args.out}")
        print(f"Capture notes: {len(value['capture_notes'])}. Review/edit connections and agent_aliases before comparison.")
        for warning in value["identity_warnings"]:
            print("Warning: " + safe(warning))
        return 0
    if args.flow_command == "prototype":
        from .prototype import execute
        directory = execute(args.directory or Path(tempfile.mkdtemp(prefix="hermes-flow-")) / "run")
        report = observe(read_file(directory / "events.jsonl"))
        args.out, args.force = directory / "flow.html", False
        print(f"Executed five deterministic process agents. Capture: {directory}")
    elif args.flow_command == "demo":
        report = demo_flow()
    else:
        if args.source and args.db:
            raise ValueError("Choose a source file or --db")
        source = args.source or args.db or default_db()
        if args.out and source.expanduser().resolve() == args.out.expanduser().resolve():
            raise ValueError("Output must not replace the input or database")
        rows = read_file(args.source) if args.source else read_database(source, args.session, all_sessions=not args.session)
        if args.source and args.session:
            selected = set(args.session)
            def sid(row):
                return row.get("id") if "messages" in row else row.get("session_id")
            if selected - {sid(row) for row in rows}:
                raise ValueError("Session ID absent from input")
            while True:
                more = {sid(row) for row in rows if row.get("parent_session_id") in selected}
                if more <= selected:
                    break
                selected |= more
            rows = [row for row in rows if sid(row) in selected]
        report = observe(rows)
    comparison = getattr(args, "compare_with_trusted_paths", None)
    if comparison:
        if args.out and comparison.expanduser().resolve() == args.out.expanduser().resolve():
            raise ValueError("Output must not replace the trusted-paths baseline")
        report = baseline.compare(report, baseline.read(comparison.expanduser()), comparison)
    as_json = getattr(args, "json", False)
    if args.out is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        extension = "json" if as_json else "html"
        output = Path.cwd() / f"flow-{stamp}-{uuid4().hex[:12]}.{extension}"
    else:
        output = args.out.expanduser().resolve()
    with output.open("w" if args.force else "x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2) + "\n" if as_json else render(report))
    print(f"{len(report['agents'])} agents/sessions · {len(report['events'])} calls\n{output}")
    if comparison:
        result = report["comparison"]
        print(f"Trusted connection comparison: {result['deviations']} new connection{'s' if result['deviations'] != 1 else ''} (deviations); {len(result['missing'])} trusted connections not observed.")
        for warning in result["identity_warnings"]:
            print("Warning: " + safe(warning))
    if args.graph != "none":
        print(graph(report, mermaid=args.graph == "mermaid"))
    if report["coverage"]:
        print(f"Capture notes: {len(report['coverage'])}; see report.")
    if args.open and not as_json:
        webbrowser.open(output.as_uri())
    print(f"Report: {output}")
    return 0


def dispatch(args):
    try:
        return run(args)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"hermes-flow: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


def main():
    parser = argparse.ArgumentParser(prog="hermes-flow", description="See recorded agent flows and tools. Everything stays local.")
    setup_parser(parser)
    return dispatch(parser.parse_args())

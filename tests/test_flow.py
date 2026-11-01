import argparse
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from hermes_flow.cli import dispatch, observe, render, setup_parser
from hermes_flow.core import normalize
from hermes_flow.graph import graph
from hermes_flow.formatting import session_table
from hermes_flow.prototype import execute
from hermes_flow.source import read_database, read_file


def event(**extra):
    return {"session_id": "r", "agent_role": "root", "tool": "read_file", "timestamp": 1, "status": "ok", **extra}


class FlowTests(unittest.TestCase):
    def test_recorded_call_durations_require_ordered_timestamps(self):
        for start, finish, expected in [(1, 2.5, 1.5), (1, 1, 0), (2, 1, None),
                                        (None, 2, None), (1, None, None), (-1e308, 1e308, None)]:
            with self.subTest(start=start, finish=finish):
                report = observe([{"id": "r", "messages": [
                    {"role": "assistant", "timestamp": start, "tool_calls": [
                        {"id": "c", "function": {"name": "read_file"}}]},
                    {"role": "tool", "tool_call_id": "c", "timestamp": finish}]}])
                self.assertEqual(report["events"][0]["duration_seconds"], expected)
        missing = observe([{"id": "r", "messages": [{"role": "assistant", "timestamp": 1,
            "tool_calls": [{"id": "c", "function": {"name": "read_file"}}]}]}])
        self.assertIsNone(missing["events"][0]["duration_seconds"])

    def test_deep_lineage_and_cycle_after_shared_ancestor(self):
        rows = [event(session_id=str(i), parent_session_id=str(i - 1) if i else None)
                for i in range(3000)]
        self.assertEqual(len(observe(rows)["agents"]), 3000)
        rows += [event(session_id="x", parent_session_id="y"),
                 event(session_id="y", parent_session_id="x")]
        with self.assertRaisesRegex(ValueError, "Cyclic"):
            observe(rows)

    def test_database_preserves_explicit_roles_and_call_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.db"
            with contextlib.closing(sqlite3.connect(path)) as db, db:
                db.executescript('CREATE TABLE sessions(id TEXT,title TEXT,agent_role TEXT,parent_session_id TEXT,parent_call_id TEXT,started_at REAL,ended_at REAL); CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,timestamp REAL,tool_calls TEXT,tool_call_id TEXT);')
                db.executemany('INSERT INTO sessions VALUES(?,?,?,?,?,?,?)', [
                    ("p", "Changing title", "coordinator", None, None, 0, 3),
                    ("c", "Another title", "reviewer", "p", "dispatch", 1, 2)])
                db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)', (1, "p", "assistant", 1,
                    json.dumps([{"id": "dispatch", "function": {"name": "delegate_task"}}]), None))
            before = path.read_bytes()
            rows = read_database(path, ["p"])
            self.assertEqual(rows[1]["parent_call_id"], "dispatch")
            self.assertEqual(observe(rows)["agents"][0]["name"], "coordinator")
            # A parent call reference alone cannot establish delivery.
            self.assertFalse(any(i["kind"] == "result" for i in observe(rows)["interactions"]))
            self.assertEqual(path.read_bytes(), before)

    def test_recorded_results_and_session_endings_are_distinct(self):
        rows = [event(session_id="p", tool="delegate_task", call_id="dispatch", child_session_id="c", status="returned"),
                event(session_id="c", parent_session_id="p", parent_call_id="dispatch")]
        report = normalize(rows)
        self.assertEqual([(e["source"], e["target"], e["kind"], e["evidence"]) for e in report["interactions"]],
                         [("c", "p", "result", "correlated tool result")])
        self.assertIn("a1 -->|result x1| a0", graph(report, mermaid=True))
        native = normalize([{"id": "p", "ended_at": 10, "messages": []},
                            {"id": "c", "parent_session_id": "p", "ended_at": 5, "messages": []}])
        self.assertEqual(native["interactions"][0]["kind"], "completion")
        self.assertEqual(native["interactions"][0]["time"], 5)
        self.assertNotIn("result x1", graph(native, mermaid=True))
        # A successful spawn or a still-running session does not prove result delivery.
        rows[0]["status"] = "ok"
        self.assertEqual(normalize(rows)["interactions"], [])

    def test_cross_agent_routes_without_message_bodies(self):
        fixture = Path(__file__).resolve().parents[1] / "examples" / "agent_interactions.jsonl"
        rows = read_file(fixture)
        rows[2]["content"] = "PRIVATE RESULT BODY"
        report = normalize(rows)
        self.assertEqual([(e["source"], e["target"], e["kind"]) for e in report["interactions"]],
                         [("a", "b", "request"), ("b", "c", "request"), ("c", "b", "result"), ("b", "d", "result")])
        self.assertIn("a1 -->|result x1| a3", graph(report, mermaid=True))
        self.assertNotIn("PRIVATE RESULT BODY", render(report))
        # An explicit interaction can point outside the selected capture, without inventing an agent.
        rows[0]["interaction"]["target_session_id"] = "missing"
        missing = normalize(rows)
        self.assertEqual(len(missing["agents"]), 4)
        self.assertTrue(any("interaction target missing" in note for note in missing["coverage"]))
        for invalid in ({"kind": "result"}, {"kind": "invented", "target_session_id": "b"}):
            with self.assertRaises(ValueError):
                normalize([event(interaction=invalid)])

    def test_readable_session_table_preserves_unicode_and_escapes_controls(self):
        rows = [{"id": "r", "title": "Résumé — 測試\x1b[2J", "cwd": "/work/project",
                 "git_repo_root": "/repos/my-repo", "last_active": 880},
                {"id": "empty", "title": None, "last_active": None}]
        output = session_table(rows, now=1000)
        self.assertIn("Title                        Workspace          Last Active   ID", output)
        self.assertIn("Résumé — 測試", output)
        self.assertIn("my-repo", output)
        self.assertIn("2m ago", output)
        self.assertIn("—", output)
        self.assertNotIn("\\u2014", output)
        self.assertNotIn("\x1b", output)

    def test_outcomes_are_display_data_not_exit_codes(self):
        parser = argparse.ArgumentParser()
        setup_parser(parser)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            source = root / "events.jsonl"
            source.write_text(json.dumps(event(status="error")))
            args = parser.parse_args(["show", str(source), "--json", "--out", str(root / "flow.json")])
            self.assertEqual(dispatch(args), 0)
            report = json.loads((root / "flow.json").read_text())
            self.assertEqual(report["events"][0]["status"], "error")
            self.assertFalse({"status", "ideal", "findings"} & report.keys())
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                dispatch(args)  # existing reports are preserved

    def test_examples_command_explains_demo_requirements(self):
        parser = argparse.ArgumentParser()
        setup_parser(parser)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(dispatch(parser.parse_args(["examples"])), 0)
        text = output.getvalue()
        self.assertIn("hermes flow demo --open", text)
        self.assertIn("examples/customer_review/run.py", text)
        self.assertIn("requires authentication", text)

    def test_repeated_default_outputs_preserve_existing_reports(self):
        parser = argparse.ArgumentParser()
        setup_parser(parser)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            source = root / "events.jsonl"
            source.write_text(json.dumps(event()))
            original = root / "flow.html"
            original.write_text("existing report")
            with patch("hermes_flow.cli.Path.cwd", return_value=root):
                for _ in range(2):
                    self.assertEqual(dispatch(parser.parse_args(["show", str(source)])), 0)
                self.assertEqual(dispatch(parser.parse_args(["show", str(source), "--json"])), 0)
            self.assertEqual(len(list(root.glob("flow-*.html"))), 2)
            reports = list(root.glob("flow-*.json"))
            self.assertEqual(len(reports), 1)
            self.assertEqual(len(json.loads(reports[0].read_text())["events"]), 1)
            self.assertEqual(original.read_text(), "existing report")
            self.assertTrue(all("__FLOW_" not in p.read_text() for p in root.glob("flow-*.html")))

    def test_capture_gaps_do_not_prevent_observation(self):
        report = observe([event(status="unknown", timestamp=None, parent_session_id="missing")])
        self.assertTrue(report["coverage"])
        self.assertEqual(len(report["events"]), 1)

    def test_process_agents_and_correlated_handoffs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = execute(Path(tmp) / "capture")
            flow = observe(read_file(path / "events.jsonl"))
            self.assertEqual(len(flow["agents"]), 5)
            self.assertEqual(len(flow["events"]), 13)
            self.assertEqual(sum("handoff" in a for a in flow["agents"]), 4)
            self.assertFalse(flow["coverage"])
            receipts = [json.loads(p.read_text()) for p in path.glob("*.receipt.json")]
            self.assertEqual(len({r["pid"] for r in receipts}), 5)
            self.assertFalse((path / "ideal.json").exists())
            self.assertIn("│   └── writer", graph(flow))
            self.assertIn("-->|handoff|", graph(flow, mermaid=True))
            rows = read_file(path / "events.jsonl")
            incomplete = normalize([r for r in rows if r.get("child_session_id") != "orchestrator-researcher"])
            self.assertTrue(incomplete["coverage"])

    def test_report_excludes_transcript_and_escapes_labels(self):
        rows = [{"id": "r", "title": '</script><img src=x onerror=alert(1)>', "ended_at": 2,
                 "messages": [{"role": "assistant", "content": "PRIVATE", "timestamp": 1,
                               "tool_calls": [{"id": "c", "function": {"name": "read_file", "arguments": '{"secret":"PRIVATE"}'}}]},
                              {"role": "tool", "tool_call_id": "c", "content": "PRIVATE"}]}]
        html = render(observe(rows))
        self.assertNotIn("PRIVATE", html)
        payload = html.split('id="flow-data">')[1].split('</script>')[0]
        self.assertNotIn('<', payload)
        self.assertNotIn('__FLOW_', html)
        self.assertEqual(json.loads(payload)["events"][0]["status"], "returned")

    def test_database_read_only_and_descendant_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.db"
            conn = sqlite3.connect(path)
            conn.executescript('CREATE TABLE sessions(id TEXT,title TEXT,parent_session_id TEXT,started_at REAL,ended_at REAL); CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,timestamp REAL,tool_calls TEXT,tool_call_id TEXT,tool_name TEXT);')
            for i, (sid, parent) in enumerate((("root", None), ("child", "root"), ("unrelated", None), ("orphan", "missing"))):
                conn.execute('INSERT INTO sessions VALUES(?,?,?,?,?)', (sid, sid, parent, i, 10))
                calls = json.dumps([{"id": "c", "function": {"name": "read_file", "arguments": "{}"}}])
                conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)', (i*2, sid, "assistant", i+1, calls, None, None))
                conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)', (i*2+1, sid, "tool", i+1.1, None, "c", "read_file"))
            conn.commit(); conn.close()
            before = path.read_bytes()
            self.assertEqual({r["id"] for r in read_database(path, ["root"])}, {"root", "child"})
            self.assertEqual({r["id"] for r in read_database(path, all_sessions=True)}, {"root", "child", "unrelated", "orphan"})
            parser = argparse.ArgumentParser()
            setup_parser(parser)
            cases = (([], {"root", "child", "unrelated", "orphan"}),
                     (["--all-sessions"], {"root", "child", "unrelated", "orphan"}),
                     (["--session-id", "root, unrelated,root"], {"root", "child", "unrelated"}),
                     (["--session-id", "root,child"], {"root", "child"}),
                     (["show", "--session", "root"], {"root", "child"}))
            with contextlib.redirect_stdout(io.StringIO()):
                for index, (flags, expected) in enumerate(cases):
                    out = Path(tmp) / f"history-{index}.json"
                    args = parser.parse_args([*flags, "--db", str(path), "--json", "--out", str(out)])
                    self.assertEqual(dispatch(args), 0)
                    report = json.loads(out.read_text())
                    self.assertEqual({a["id"] for a in report["agents"]}, expected)
                    self.assertEqual(len(report["events"]), len(expected))
            with contextlib.redirect_stderr(io.StringIO()):
                for flags in (["--session-id", "root,,child"], ["--session-id", "root", "--all-sessions"]):
                    with self.assertRaises(SystemExit):
                        parser.parse_args(flags)
                out = Path(tmp) / "unknown.json"
                args = parser.parse_args(["--session-id", "unknown", "--db", str(path), "--out", str(out)])
                with self.assertRaises(SystemExit):
                    dispatch(args)
                self.assertFalse(out.exists())
            self.assertEqual(path.read_bytes(), before)

    def test_large_capture_and_terminal_control_escaping(self):
        flow = observe([event(session_id=f"a{a}", call_id=f"c{i}", agent_role="agent\x1b[2J") for a in range(500) for i in range(20)])
        self.assertEqual(len(flow["events"]), 10000)
        self.assertNotIn('\x1b', graph(flow))

    def test_invalid_identity_and_cycles_fail(self):
        for rows in ([event(call_id="same"), event(call_id="same")],
                     [event(session_id="a", parent_session_id="b"), event(session_id="b", parent_session_id="a")]):
            with self.assertRaises(ValueError):
                observe(rows)

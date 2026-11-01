import argparse
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from hermes_flow import baseline
from hermes_flow.cli import dispatch, observe, setup_parser
from hermes_flow.graph import graph


def routes(prefix=""):
    return [{"session_id": prefix + "root", "agent_role": "coordinator", "tool": "delegate_task",
             "call_id": "dispatch", "child_session_id": prefix + "child", "status": "returned"},
            {"session_id": prefix + "child", "agent_role": "reviewer", "parent_session_id": prefix + "root",
             "parent_call_id": "dispatch", "tool": "read_file", "status": "ok"}]


class BaselineTests(unittest.TestCase):
    def test_shared_labels_and_aliases_warn_without_changing_matching(self):
        rows = [{"session_id": "a", "agent_role": "reviewer", "tool": "read_file"},
                {"session_id": "b", "agent_role": "reviewer", "tool": "write_file"}]
        report = observe(rows)
        trusted = baseline.create(report)
        self.assertEqual(len(trusted["identity_warnings"]), 1)
        self.assertIn("2 sessions", trusted["identity_warnings"][0])
        compared = baseline.compare(report, trusted, "trusted.json")["comparison"]
        self.assertEqual(compared["deviations"], 0)
        self.assertEqual(compared["identity_warnings"], trusted["identity_warnings"])
        rows[1]["agent_role"] = "editor"
        self.assertEqual(baseline.identity_warnings(observe(rows)), [])
        trusted["agent_aliases"] = {"editor": "reviewer"}
        compared = baseline.compare(observe(rows), trusted, "trusted.json")["comparison"]
        self.assertEqual(compared["deviations"], 0)
        self.assertEqual(len(compared["identity_warnings"]), 1)

    def save_baseline(self, directory, report):
        path = directory / "trusted.json"
        baseline.write(path, baseline.create(report))
        return path, baseline.read(path)

    def test_stable_labels_new_tools_agents_and_directed_interactions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, trusted = self.save_baseline(Path(tmp), observe(routes()))
            before = path.read_bytes()
            same = baseline.compare(observe(routes("new-")), trusted, path)
            self.assertEqual(same["comparison"]["deviations"], 0)
            self.assertEqual(same["comparison"]["missing"], [])
            rows = routes("new-")
            rows.append({**rows[1], "tool": "write_file", "interaction": {"kind": "message", "target_session_id": "new-root"}})
            rows.append({"session_id": "outsider", "agent_role": "new agent", "tool": "read_file",
                         "parent_session_id": "new-root"})
            compared = baseline.compare(observe(rows), trusted, path)
            self.assertEqual(compared["comparison"]["deviations"], 4)
            self.assertEqual(compared["events"][2]["connection_status"], "deviation")
            self.assertEqual(compared["agents"][2]["connection_status"], "deviation")
            self.assertIn("[NEW CONNECTION]", graph(compared))
            self.assertIn("linkStyle", graph(compared, mermaid=True))
            self.assertEqual(path.read_bytes(), before)
            # Opposite directions and changed evidence kinds are separate connections.
            reversed_rows = routes()
            reversed_rows[0]["status"] = "ok"
            reversed_rows[0]["interaction"] = {"kind": "result", "target_session_id": "child"}
            reverse = baseline.compare(observe(reversed_rows), trusted, path)
            self.assertEqual(reverse["comparison"]["deviations"], 1)
            self.assertEqual(len(reverse["comparison"]["missing"]), 1)

    def test_editable_baseline_aliases_hash_and_missing_connections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "trusted.json"
            draft = baseline.create(observe(routes()))
            draft["agent_aliases"] = {"coordinator": "lead", "new coordinator": "lead"}
            baseline.write(path, draft)
            trusted = baseline.read(path)
            before = path.read_bytes()
            renamed = routes("fresh-")
            renamed[0]["agent_role"] = "new coordinator"
            self.assertEqual(baseline.compare(observe(renamed), trusted, path)["comparison"]["deviations"], 0)
            subset = observe([routes()[1] | {"parent_session_id": None, "parent_call_id": None}])
            result = baseline.compare(subset, trusted, path)["comparison"]
            self.assertEqual(result["deviations"], 0)
            self.assertEqual(len(result["missing"]), 3)
            initial = baseline.compare(observe(routes()), trusted, path)["comparison"]
            self.assertEqual(initial["sha256"], baseline.digest(trusted))
            self.assertEqual(path.read_bytes(), before)
            edited = baseline.read(path)
            edited["connections"][-1]["target"]["tools"].pop()
            path.write_text(json.dumps(edited))
            after_edit = path.read_bytes()
            changed = baseline.compare(observe(routes()), baseline.read(path), path)["comparison"]
            self.assertEqual(changed["deviations"], 1)
            self.assertNotEqual(changed["sha256"], initial["sha256"])
            self.assertEqual(path.read_bytes(), after_edit)
            # An unresolved endpoint must not match a known agent with that name.
            report = observe([{"session_id": "child", "agent_role": "reviewer", "parent_session_id": "coordinator", "tool": "read_file"}])
            self.assertEqual(baseline.compare(report, trusted, path)["comparison"]["deviations"], 1)

    def test_database_cli_selection_edit_and_comparison(self):
        parser = argparse.ArgumentParser()
        setup_parser(parser)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            db = root / "state.db"
            with contextlib.closing(sqlite3.connect(db)) as conn, conn:
                conn.executescript('CREATE TABLE sessions(id TEXT,title TEXT,parent_session_id TEXT,started_at REAL,ended_at REAL); CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,timestamp REAL,tool_calls TEXT,tool_call_id TEXT);')
                for i, (sid, name, parent, tool) in enumerate([
                    ("old", "lead", None, "delegate_task"), ("old-child", "reviewer", "old", "read_file"),
                    ("new", "lead", None, "delegate_task"), ("new-child", "reviewer", "new", "write_file")]):
                    conn.execute('INSERT INTO sessions VALUES(?,?,?,?,?)', (sid, name, parent, i, 10))
                    calls = json.dumps([{"id": "c", "function": {"name": tool}}])
                    conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)', (i, sid, "assistant", i, calls, None))
            before = db.read_bytes()

            def run(*args):
                return dispatch(parser.parse_args(list(args)))

            trusted = root / "trusted.json"
            run("baseline", "create", "--db", str(db), "--session-id", "old,old-child", "--out", str(trusted))
            self.assertEqual(len(baseline.read(trusted)["source_sessions"]), 2)
            baseline_bytes = trusted.read_bytes()
            out = root / "compared.json"
            run("--db", str(db), "--session-id", "new", "--compare-with-trusted-paths", str(trusted), "--json", "--out", str(out))
            self.assertEqual(json.loads(out.read_text())["comparison"]["deviations"], 1)
            run("baseline", "create", "--db", str(db), "--all-sessions", "--out", str(root / "all.json"))
            self.assertEqual(len(baseline.read(root / "all.json")["source_sessions"]), 4)
            run("--db", str(db), "--all-sessions", "--compare-with-trusted-paths", str(trusted), "--out", str(root / "all.html"))
            # Editing trusted connections takes effect on the next comparison.
            revised = baseline.read(trusted)
            reviewer = next(g for g in revised["connections"] if g["source"] == "agent:reviewer")
            reviewer["target"]["tools"].append("tool:write_file")
            revision = root / "revision.json"
            baseline.write(revision, revised)
            revised_out = root / "revised.json"
            run("--db", str(db), "--session-id", "new", "--compare-with-trusted-paths", str(revision), "--json", "--out", str(revised_out))
            self.assertEqual(json.loads(revised_out.read_text())["comparison"]["deviations"], 0)
            with contextlib.redirect_stderr(io.StringIO()):
                for flags in [[], ["--session-id", "old", "--all-sessions"]]:
                    with self.assertRaises(SystemExit):
                        parser.parse_args(["baseline", "create", *flags, "--out", "unused.json"])
                with self.assertRaises(SystemExit):
                    run("--db", str(db), "--compare-with-trusted-paths", str(trusted), "--out", str(trusted), "--force")
                with self.assertRaises(SystemExit):
                    run("baseline", "create", "--db", str(db), "--session-id", "missing", "--out", str(root / "missing.json"))
                with self.assertRaises(SystemExit):
                    run("baseline", "create", "--db", str(db), "--all-sessions", "--out", str(trusted))
            self.assertEqual(db.read_bytes(), before)
            self.assertEqual(trusted.read_bytes(), baseline_bytes)

    def test_malformed_baselines_fail_cleanly(self):
        value = baseline.create(observe(routes()))
        for replacement in [None, "bad", [{"source": "agent:a", "target": "tool:b", "kind": "invented"}],
                            [value["connections"][0]] * 2,
                            [{"source": "agent:a", "target": {"agents": [], "tools": "tool:read_file"}}],
                            [{"source": "agent:a", "target": {"agents": [{"name": "agent:b", "kind": "uses"}], "tools": []}}],
                            [{"source": "agent:a", "target": {"agents": [], "tools": ["agent:b"]}}],
                            [{"source": "agent:a", "target": {"agents": [], "tools": ["tool:read_file"] * 2}}],
                            [{"source": "agent:a", "target": {"agents": [{"name": "agent:b", "kind": "message"}] * 2, "tools": []}}]]:
            with self.assertRaises(ValueError):
                baseline.validate({**value, "connections": replacement})
        with self.assertRaises(ValueError):
            baseline.validate({**value, "agent_aliases": {"a": "b", "b": "c"}})

    def test_grouped_sources_preserve_direction_types_and_legacy_files(self):
        rows = routes()
        rows[0]["interaction"] = {"target_session_id": "child", "kind": "message"}
        report = observe(rows)
        grouped = baseline.create(report)
        self.assertEqual(grouped["schema"], 2)
        sources = {g["source"]: g["target"] for g in grouped["connections"]}
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources["agent:coordinator"]["tools"], ["tool:delegate_task"])
        self.assertEqual(sources["agent:coordinator"]["agents"], [
            {"name": "agent:reviewer", "kind": "handoff"}, {"name": "agent:reviewer", "kind": "message"}])
        legacy = {**grouped, "schema": 1, "connections": list(baseline.expanded(grouped))}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.json"
            baseline.write(path, legacy)
            before = path.read_bytes()
            old = baseline.compare(observe(rows), baseline.read(path), path)
            new = baseline.compare(observe(rows), grouped, "grouped.json")
            self.assertEqual(old["comparison"]["connections"], new["comparison"]["connections"])
            self.assertEqual(path.read_bytes(), before)
        # A child's trusted tool is not automatically trusted for its parent.
        rows.append({"session_id": "root", "agent_role": "coordinator", "tool": "read_file", "call_id": "extra"})
        result = baseline.compare(observe(rows), grouped, "grouped.json")["comparison"]
        self.assertEqual(result["deviations"], 1)
        self.assertEqual(next(c for c in result["connections"] if c["status"] == "deviation")["source"], "agent:coordinator")

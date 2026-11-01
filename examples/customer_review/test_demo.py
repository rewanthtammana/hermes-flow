"""Offline checks for real SQLite tools and capture verification; no model calls."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from demo_tools import dispatch, seed
from run import active_profile, find_hermes, install_resources, main, prepare, verify
from compare_paths import copy_sessions, read_database
from hermes_flow.cli import observe


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        seed(self.data)

    def call(self, name, **args):
        return json.loads(dispatch(name, args, self.data))

    def test_find_hermes_uses_standard_install_fallback(self):
        with patch("run.shutil.which", return_value=None), patch("run.os.path.isfile", return_value=True), \
                patch("run.os.access", return_value=True):
            self.assertEqual(find_hermes(), "/opt/hermes/bin/hermes")

    def test_sqlite_and_masked_handoff(self):
        summary = self.call("demo_portfolio_summary")
        self.assertEqual((summary["customers"], summary["total_balance"], summary["overdue_amount"]), (3, 38100, 350))
        customer = self.call("demo_customer_lookup", customer_id=1)["customer"]
        identifiers = [customer[k] for k in ("email", "national_id", "bank_account")]
        masked = self.call("demo_redact", text=" ".join(identifiers))["text"]
        self.assertEqual(masked, "[MASKED] [MASKED] [MASKED]")
        self.call("demo_artifact_write", filename="privacy.json", content=json.dumps({"example": masked}))
        self.assertEqual(json.loads(self.call("demo_artifact_read", filename="privacy.json")["content"])["example"], masked)
        self.assertIn("privacy.json", self.call("demo_audit_receipt")["artifacts"])
        receipts = (self.data / "tool-receipts.jsonl").read_text()
        self.assertTrue(all(value not in receipts for value in identifiers))

    def test_fixed_artifact_boundary_and_missing_file(self):
        self.assertIn("error", self.call("demo_artifact_write", filename="../../escape", content="no"))
        self.assertFalse((self.root / "escape").exists())
        self.assertIn("error", self.call("demo_artifact_read", filename="draft.md"))

    def test_no_overwrite_or_fabricated_history(self):
        target = self.root / "prepared"
        prepare(target, "example-model")
        with self.assertRaises(FileExistsError):
            prepare(target, "example-model")
        self.assertFalse((target / "profiles/demo/state.db").exists())
        with self.assertRaisesRegex(ValueError, "No native Hermes history"):
            verify(target)

    def test_verifier_rejects_static_five_session_claim(self):
        target = self.root / "invalid"
        prepare(target, "example-model")
        with closing(sqlite3.connect(target / "profiles/demo/state.db")) as db, db:
            db.executescript("CREATE TABLE sessions(id TEXT,parent_session_id TEXT,started_at REAL);"
                             "CREATE TABLE messages(session_id TEXT,tool_calls TEXT);")
            db.executemany("INSERT INTO sessions VALUES (?,?,?)", [("root", None, 0), ("a", "root", 1),
                ("b", "a", 2), ("c", "root", 3), ("d", "c", 4)])
        with self.assertRaisesRegex(ValueError, "Missing recorded skill loads"):
            verify(target)

    def test_active_prepare_only_uses_resolved_profile_without_installing(self):
        target, home = self.root / "prepared", self.root / "active-profile"
        with patch("run.shutil.which", return_value="/bin/hermes"), patch("run.active_profile", return_value=home), \
                patch("run.install_resources") as install, patch("sys.argv", ["run.py", "--prepare-only", "--directory", str(target)]):
            self.assertEqual(main(), 0)
        install.assert_not_called()
        metadata = json.loads((target / "run.json").read_text())
        self.assertEqual(metadata["history_mode"], "active")
        self.assertEqual(metadata["database"], str(home / "state.db"))
        self.assertTrue((target / "resources/plugins/customer-review-demo/plugin.yaml").is_file())
        self.assertFalse(home.exists())
        with patch("run.hermes_command", return_value=str(home / "config.yaml")) as query:
            self.assertEqual(active_profile("hermes"), home.resolve())
        query.assert_called_once_with("hermes", ["config", "path"])

    def test_profile_setup_preserves_existing_files_and_deeper_delegation(self):
        home, target = self.root / "profile", self.root / "prepared"
        home.mkdir()
        config = home / "config.yaml"
        config.write_text("existing settings")
        prepare(target, "example-model", active_home=home)
        with patch("run.hermes_command", side_effect=["Enabled", "3"]) as invoke:
            install_resources(target, home, "hermes", {})
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(config.read_text(), "existing settings")
        self.assertTrue((home / "skills/demo-analyst/SKILL.md").is_file())
        # Existing identical demo resources are reusable; depth is raised only when needed.
        with patch("run.hermes_command", side_effect=["Enabled", "1", "Updated"]) as invoke:
            install_resources(target, home, "hermes", {})
        self.assertEqual(invoke.call_args.args[1][-3:], ["set", "delegation.max_spawn_depth", "2"])
        owned = home / "plugins/customer-review-demo/__init__.py"
        owned.write_text("user modification")
        with patch("run.hermes_command") as invoke, self.assertRaisesRegex(ValueError, "conflicts"):
            install_resources(target, home, "hermes", {})
        invoke.assert_not_called()
        self.assertEqual(owned.read_text(), "user modification")

    def test_shared_history_verification_and_replay_select_only_demo(self):
        home, target = self.root / "profile", self.root / "prepared"
        home.mkdir()
        prepare(target, "example-model", active_home=home)
        # Complete demo artifacts plus the native history of two independent roots.
        data = target / "data"
        summary = json.loads(dispatch("demo_portfolio_summary", {}, data))
        for filename, content in {"analysis.json": json.dumps(summary), "privacy.json": '{"example":"[MASKED]"}',
                                  "draft.md": "synthetic", "report.md": "synthetic", "review.json": '{"approved":true}'}.items():
            dispatch("demo_artifact_write", {"filename": filename, "content": content}, data)
        dbpath = home / "state.db"
        with closing(sqlite3.connect(dbpath)) as db, db:
            db.executescript("CREATE TABLE sessions(id TEXT,title TEXT,parent_session_id TEXT,started_at REAL,ended_at REAL);"
                             "CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,timestamp REAL,tool_calls TEXT,tool_call_id TEXT,tool_name TEXT);")
            agents = [("root", None), ("analyst", "root"), ("privacy", "analyst"), ("writer", "root"), ("reviewer", "writer"), ("unrelated", None)]
            skills = ["demo-coordinator", "demo-analyst", "demo-privacy", "demo-writer", "demo-reviewer"]
            for i, (sid, parent) in enumerate(agents):
                db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (sid, sid, parent, i, 10))
                function = {"name": "skill_view", "arguments": json.dumps({"name": skills[i] if i < 5 else "unrelated"})}
                # The active profile may use Hermes's native Tool Search wrapper.
                if i == 2:
                    function = {"name": "tool_call", "arguments": json.dumps({"calls": [function]})}
                calls = [{"id": f"skill-{i}", "function": function}]
                if sid == "root":
                    calls += [{"id": f"tool-{n}", "function": {"name": name}} for n, name in enumerate([
                        "demo_customer_lookup", "demo_portfolio_summary", "demo_redact", "demo_artifact_read",
                        "demo_artifact_write", "demo_audit_receipt", *["delegate_task"] * 4])]
                db.execute("INSERT INTO messages(session_id,role,timestamp,tool_calls) VALUES(?,?,?,?)",
                           (sid, "assistant", i + 1, json.dumps(calls)))
        with self.assertRaisesRegex(ValueError, "refusing to guess"):
            verify(target)
        (target / "usage.json").write_text('{"session_id":"root"}')
        before = dbpath.read_bytes()
        result = verify(target)
        self.assertEqual(set(result["session_ids"]), {"root", "analyst", "privacy", "writer", "reviewer"})
        self.assertEqual(result["recorded_calls"], 15)
        copy = self.root / "selected.db"
        copy_sessions(dbpath, copy, "root")
        with closing(sqlite3.connect(copy)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 5)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE session_id='unrelated'").fetchone()[0], 0)
            extra = json.dumps([{"id": "extra", "function": {"name": "demo_unapproved_tool"}}])
            db.execute("INSERT INTO messages(session_id,role,timestamp,tool_calls) VALUES(?,?,?,?)",
                       ("root", "assistant", 11, extra))
        flow = observe(read_database(copy, ["root"]))
        self.assertIn("demo_unapproved_tool", {e["tool"] for e in flow["events"]})
        self.assertEqual(dbpath.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

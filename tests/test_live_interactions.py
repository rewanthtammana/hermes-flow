"""Native demo evidence must come from matching runtime events, not clock order."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from hermes_flow.cli import observe

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/live_interactions"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


capture = module("live_capture", EXAMPLE / "capture.py")
demo = module("live_demo", EXAMPLE / "run.py")


class LiveInteractionsTests(unittest.TestCase):
    def setUp(self):
        capture._pending.clear()
        self.env = patch.dict(os.environ, {"HERMES_FLOW_INTERACTIONS_DEMO": "test-run"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.patches = {}
        owner = self

        class DB:
            def get_session(self, sid):
                return {"id": sid, "parent_session_id": "a"}

            def patch_session_model_config(self, sid, patch):
                owner.patches[sid] = patch

            def close(self):
                pass

        self.native = patch.dict(sys.modules, {"hermes_state": types.SimpleNamespace(SessionDB=DB)})
        self.native.start()
        self.addCleanup(self.native.stop)

    def record(self, result=None):
        capture.before(tool_name="delegate_task", session_id="a", tool_call_id="dispatch", turn_id="turn")
        capture.started(parent_session_id="a", parent_turn_id="turn", child_session_id="b")
        capture.after(tool_name="delegate_task", session_id="a", tool_call_id="dispatch", turn_id="turn",
                      result=result if result is not None else json.dumps({"results": [{"summary": "PRIVATE", "status": "completed"}]}))

    def test_actual_lifecycle_ids_create_bidirectional_native_evidence(self):
        self.record()
        metadata = self.patches["b"]
        self.assertNotIn("PRIVATE", json.dumps(metadata))
        native = [
            {"id": "a", "ended_at": 10, "messages": [
                {"role": "assistant", "timestamp": 1, "tool_calls": [
                    {"id": "dispatch", "function": {"name": "delegate_task", "arguments": '{"private":"PRIVATE"}'}}]},
                {"role": "tool", "timestamp": 3, "tool_call_id": "dispatch", "content": "PRIVATE"}]},
            {"id": "b", "parent_session_id": "a", "ended_at": 2,
             "model_config": json.dumps(metadata), "messages": []},
        ]
        flow = observe(native)
        self.assertEqual(flow["agents"][1]["handoff"]["call_id"], "dispatch")
        self.assertEqual([(i["source"], i["target"], i["kind"]) for i in flow["interactions"]],
                         [("a", "b", "request"), ("b", "a", "result")])
        self.assertNotIn("PRIVATE", json.dumps(flow))
        # A stored marker without its matching invocation is never a verified handoff.
        native[0]["messages"][0]["tool_calls"][0]["id"] = "different-call"
        missing = observe(native)
        self.assertNotIn("handoff", missing["agents"][1])
        self.assertNotIn("request", [i["kind"] for i in missing["interactions"]])

    def test_background_errors_and_ambiguous_calls_do_not_claim_delivery(self):
        for envelope in [{"status": "dispatched"}, {"error": "failed"},
                         {"results": [{"summary": "error", "status": "failed"}]},
                         {"results": [{"summary": "a", "status": "completed"}] * 2}]:
            self.record(json.dumps(envelope))
            self.assertFalse(self.patches)
        capture.before(tool_name="delegate_task", session_id="a", tool_call_id="one", turn_id="turn")
        capture.before(tool_name="delegate_task", session_id="a", tool_call_id="two", turn_id="turn")
        capture.started(parent_session_id="a", parent_turn_id="turn", child_session_id="b")
        capture.after(tool_name="delegate_task", session_id="a", tool_call_id="one", turn_id="turn",
                      result={"results": [{"summary": "done", "status": "completed"}]})
        self.assertFalse(self.patches)

    def test_observer_is_inactive_outside_demo_and_rejects_invalid_metadata(self):
        with patch.dict(os.environ, {"HERMES_FLOW_INTERACTIONS_DEMO": ""}):
            self.record()
        self.assertFalse(self.patches)
        self.assertFalse(capture._pending)
        with self.assertRaisesRegex(ValueError, "invalid native flow"):
            observe([{"id": "b", "parent_session_id": "a", "messages": [],
                      "model_config": {capture.MARKER: {"schema": 1, "parent_session_id": "wrong", "parent_call_id": "c"}}}])

    def test_demo_refuses_history_without_native_session_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "run.json").write_text('{"database":"missing.db"}')
            (directory / "usage.json").write_text('{}')
            with self.assertRaisesRegex(ValueError, "root session ID"):
                demo.verify(directory)

    def test_verifier_counts_child_handoffs_and_keeps_extra_calls_visible(self):
        rows = []
        for sid, parent, parent_call, specs, ended in [
            ("a", None, None, [("preflight", "delegate_task", 0, .5), ("ab", "delegate_task", 1, 6)], 7),
            ("b", "a", "ab", [("bc", "delegate_task", 2, 3), ("bd", "delegate_task", 4, 5)], 6),
            ("c", "b", "bc", [("multiply", "flow_demo_multiply", 2.2, 2.8)], 3),
            ("d", "b", "bd", [("format", "flow_demo_format", 4.2, 4.8)], 5),
        ]:
            messages = []
            for cid, tool, start, finish in specs:
                messages.extend([{"role": "assistant", "timestamp": start, "tool_calls": [
                    {"id": cid, "function": {"name": tool}}]},
                    {"role": "tool", "timestamp": finish, "tool_call_id": cid}])
            metadata = {capture.MARKER: {"schema": 1, "parent_session_id": parent, "parent_call_id": parent_call}} if parent else {}
            rows.append({"id": sid, "parent_session_id": parent, "ended_at": ended,
                         "model_config": metadata, "messages": messages})
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "run.json").write_text('{"database":"native.db"}')
            (directory / "usage.json").write_text('{"session_id":"a"}')
            (directory / "stdout.log").write_text('Result: 42\n')
            with patch.object(demo, "read_database", return_value=rows):
                result = demo.verify(directory)
                self.assertTrue(result["verified"])
                self.assertEqual(result["delegation_calls"], 4)
                self.assertEqual(result["correlated_handoffs"], 3)
                self.assertEqual(result["recorded_calls"], 6)
                self.assertTrue(result["capture_notes"])
                (directory / "stdout.log").write_text("42\n")
                self.assertTrue(demo.verify(directory)["verified"])
                (directory / "stdout.log").write_text("Result: 42\n")
                # Newer Hermes versions retain an open one-shot root after a
                # completed CLI turn. Require its explicit completion receipt.
                rows[0]["ended_at"] = None
                with self.assertRaisesRegex(ValueError, "completion evidence"):
                    demo.verify(directory)
                (directory / "usage.json").write_text('{"session_id":"a","completed":true}')
                self.assertTrue(demo.verify(directory)["verified"])
                self.assertIsNone(rows[0]["ended_at"])
                for flag in ("failed", "partial", "interrupted"):
                    (directory / "usage.json").write_text(json.dumps({"session_id": "a", "completed": True, flag: True}))
                    with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "completion evidence"):
                        demo.verify(directory)
                (directory / "usage.json").write_text('{"session_id":"a","completed":true}')
                rows[-1]["ended_at"] = None
                with self.assertRaisesRegex(ValueError, "completion evidence"):
                    demo.verify(directory)
                rows[-1]["ended_at"] = 5
                rows[-1]["model_config"] = {}
                with self.assertRaisesRegex(ValueError, "lifecycle-correlated"):
                    demo.verify(directory)

    def test_dependency_free_demo_falls_back_to_config_after_terminfo_sync_collision(self):
        customer = sys.modules["run"]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            resources = directory / "resources" / "plugins" / "demo-plugin"
            resources.mkdir(parents=True)
            (resources / "__init__.py").write_text("def register(ctx): pass\n")
            (resources / "plugin.yaml").write_text("name: demo-plugin\nversion: \"0.1.0\"\n")
            calls = []

            def hermes_command(executable, arguments, env=None):
                args = arguments[arguments.index("--profile") + 2:]
                calls.append(args)
                if args[:2] == ["plugins", "enable"]:
                    raise ValueError("AdmissionRefused: [Errno 17] File exists: share/terminfo/N")
                if args[:3] == ["config", "get", "plugins.enabled"]:
                    return json.dumps(["existing-plugin"])
                if args[:3] == ["config", "get", "plugins.disabled"]:
                    return json.dumps(["demo-plugin", "other-disabled"])
                if args[:3] == ["config", "get", "delegation.max_spawn_depth"]:
                    return "2"
                return ""

            with patch.object(customer, "hermes_command", side_effect=hermes_command):
                customer.install_resources(directory, directory / "home", "hermes", {}, plugin_name="demo-plugin")
            self.assertIn(["config", "set", "plugins.enabled", json.dumps(["existing-plugin", "demo-plugin"])], calls)
            self.assertIn(["config", "set", "plugins.disabled", json.dumps(["other-disabled"])], calls)
            self.assertTrue((directory / "home/plugins/demo-plugin/__init__.py").is_file())

    def test_dependency_free_enable_does_not_bypass_unrelated_sync_failure(self):
        customer = sys.modules["run"]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            resources = directory / "resources" / "plugins" / "demo-plugin"
            resources.mkdir(parents=True)
            (resources / "__init__.py").write_text("def register(ctx): pass\n")
            (resources / "plugin.yaml").write_text("name: demo-plugin\nversion: \"0.1.0\"\n")
            with patch.object(customer, "hermes_command", side_effect=ValueError("dependency resolution failed")) as invoke:
                with self.assertRaisesRegex(ValueError, "dependency resolution failed"):
                    customer.install_resources(directory, directory / "home", "hermes", {}, plugin_name="demo-plugin")
            self.assertEqual(invoke.call_count, 1)

    def test_terminfo_fallback_handles_unset_plugin_list(self):
        customer = sys.modules["run"]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            resources = directory / "resources" / "plugins" / "demo-plugin"
            resources.mkdir(parents=True)
            (resources / "__init__.py").write_text("def register(ctx): pass\n")
            (resources / "plugin.yaml").write_text("name: demo-plugin\nversion: \"0.1.0\"\n")
            calls = []

            def hermes_command(executable, arguments, env=None):
                args = arguments[arguments.index("--profile") + 2:]
                calls.append(args)
                if args[:2] == ["plugins", "enable"]:
                    raise ValueError("[Errno 17] File exists: share/terminfo/e")
                if args[:3] in (["config", "get", "plugins.enabled"],
                                ["config", "get", "plugins.disabled"]):
                    raise ValueError(f"Config key not set: {args[2]}")
                if args[:3] == ["config", "get", "delegation.max_spawn_depth"]:
                    return "2"
                return ""

            with patch.object(customer, "hermes_command", side_effect=hermes_command):
                customer.install_resources(directory, directory / "home", "hermes", {}, plugin_name="demo-plugin")
            self.assertIn(["config", "set", "plugins.enabled", "[\"demo-plugin\"]"], calls)

    def test_terminfo_fallback_is_disabled_for_plugins_with_dependency_manifests(self):
        customer = sys.modules["run"]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            resources = directory / "resources" / "plugins" / "demo-plugin"
            resources.mkdir(parents=True)
            (resources / "__init__.py").write_text("def register(ctx): pass\n")
            (resources / "plugin.yaml").write_text("name: demo-plugin\nversion: \"0.1.0\"\n")
            (resources / "pyproject.toml").write_text("[project]\nname = \"demo-plugin\"\nversion = \"0.1.0\"\n")
            with patch.object(customer, "hermes_command", side_effect=ValueError("[Errno 17] File exists: share/terminfo/N")) as invoke:
                with self.assertRaisesRegex(ValueError, "Errno 17"):
                    customer.install_resources(directory, directory / "home", "hermes", {}, plugin_name="demo-plugin")
            self.assertEqual(invoke.call_count, 1)

"""Run four real Hermes agents with recorded request/result connections."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from uuid import uuid4

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "customer_review"))
from run import active_profile, install_resources, profile_command
sys.path.insert(0, str(HERE.parents[1]))
from hermes_flow.cli import observe
from hermes_flow.source import read_database

PLUGIN = "flow-interaction-demo"
PROMPT = """You are Coordinator A in a tiny real-agent interaction demo.
Use the demo tools and delegate_task. Exactly FOUR agents total: you, Analyst B, Researcher C,
and Writer D. No replacements, retries, extra agents, or batches.

Delegate exactly ONE synchronous task to Analyst B (background=false).
Include ALL these instructions in B's goal:
You are Analyst B. First delegate ONE synchronous task to Researcher C:
"You are Researcher C. Call flow_demo_multiply(a=6,b=7), then return only its number. Do not delegate."
Wait for C's real result. Then delegate ONE synchronous task to Writer D, passing
the actual numeric value returned by C in D's goal. Instruct D to call
flow_demo_format with its number argument set to that value, return the tool's
exact text, and not delegate. Write a complete goal containing the actual value;
do not include template markers or argument placeholders in any delegation goal.
Wait for D and return D's exact response. Use separate delegate_task calls, one
child per call, background=false. Never create another agent.

When B returns, return its exact response. Do not claim success on a failed child.
"""


def verify(directory):
    metadata = json.loads((directory / "run.json").read_text())
    usage = json.loads((directory / "usage.json").read_text())
    root = usage.get("session_id")
    if not isinstance(root, str) or not root:
        raise ValueError("Hermes did not report a root session ID")
    report = observe(read_database(metadata["database"], [root]))
    agents = report["agents"]
    children = [a for a in agents if a["parent"] == root]
    # Hermes 0.22.5 can keep a one-shot root session open after its CLI turn
    # completes. Its usage receipt confirms that turn, without inventing a DB
    # end marker or relaxing the requirement for children to have ended.
    root_turn_complete = usage.get("completed") is True and not any(
        usage.get(flag) for flag in ("failed", "interrupted", "partial"))
    if (len(agents) != 4 or len(children) != 1
            or not all(a["ended"] or (a["id"] == root and root_turn_complete) for a in agents)):
        raise ValueError("Expected four native sessions, one direct child of A, and completion evidence for each")
    analyst = children[0]["id"]
    leaves = [a for a in agents if a["parent"] == analyst]
    if len(leaves) != 2 or not all(a.get("handoff") for a in agents if a["parent"]):
        raise ValueError("Expected B's two children and three lifecycle-correlated handoffs")
    links = {(i["source"], i["target"], i["kind"]) for i in report["interactions"]}
    expected = {(a["parent"], a["id"], "request") for a in agents if a["parent"]}
    expected |= {(a["id"], a["parent"], "result") for a in agents if a["parent"]}
    if links != expected:
        raise ValueError("Expected a recorded request and returned result for every delegation")
    calls = [e for e in report["events"] if e["tool"] == "delegate_task"]
    handoff_events = {a["handoff"]["event"] for a in agents if a.get("handoff")}
    spawns = [e for e in calls if e["id"] in handoff_events]
    # Native delegation also supports control calls and can reject arguments
    # before spawning. Keep those invocations visible; count actual child links.
    if len(spawns) != 3 or any(e["status"] != "returned" for e in spawns):
        raise ValueError("Expected exactly three returned, correlated native handoffs")
    if not {"flow_demo_multiply", "flow_demo_format"} <= {e["tool"] for e in report["events"]}:
        raise ValueError("Expected the real research and formatting tool calls")
    nested = [e for e in spawns if e["session"] == analyst]
    if len(nested) != 2 or nested[0].get("result_time") is None or nested[1]["time"] is None:
        raise ValueError("Missing native timing evidence for B's sequential handoff")
    if nested[0]["result_time"] > nested[1]["time"]:
        raise ValueError("B requested D before receiving C's result")
    if (directory / "stdout.log").read_text().strip() not in {"Result: 42", "42"}:
        raise ValueError("Expected the real C → B → D computation to return 42")
    return {"root_session_id": root, "session_ids": [a["id"] for a in agents],
            "recorded_calls": len(report["events"]), "delegation_calls": len(calls),
            "correlated_handoffs": 3, "requests": 3, "results": 3,
            "capture_notes": report["coverage"], "verified": True}


def find_hermes() -> str | None:
    """Return a usable Hermes CLI executable, or None.

    shutil.which returns None when the Hermes launcher (the s6-setuid
    shim at /opt/hermes/bin/hermes or the venv's
    /opt/hermes/.venv/bin/hermes) is not on the caller's PATH. We then
    fall back through the standard install locations. This makes the live
    demo work even when the user ran pip install . or placed Hermes
    outside PATH.
    """
    found = shutil.which("hermes")
    if found:
        return found
    for candidate in (
        "/opt/hermes/bin/hermes",
        "/opt/hermes/.venv/bin/hermes",
        "/usr/local/bin/hermes",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, help="New run directory")
    parser.add_argument("--model", default=os.environ.get("DEMO_CODEX_MODEL", "gpt-5.6-luna"))
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--verify", type=Path, metavar="RUN", help="Verify existing native evidence and refresh its manifest without model calls")
    args = parser.parse_args()
    if args.verify:
        directory = args.verify.resolve()
        result = verify(directory)
        metadata = directory / "run.json"
        manifest = json.loads(metadata.read_text())
        manifest.update(result, status="complete")
        manifest.pop("error", None)
        metadata.write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        return 0
    hermes = find_hermes()
    if not hermes:
        raise ValueError("Install Hermes and authenticate with its native openai-codex provider first")
    home = active_profile(hermes)
    directory = (args.directory or HERE / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8])).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    plugin = directory / "resources/plugins" / PLUGIN
    plugin.mkdir(parents=True)
    shutil.copy2(HERE / "capture.py", plugin / "__init__.py")
    (plugin / "plugin.yaml").write_text(f'name: {PLUGIN}\nversion: "0.1.0"\ndescription: Capture native delegation IDs during the small Flow demo\ncapabilities: []\n')
    env = dict(os.environ, HERMES_HOME=str(home), HERMES_FLOW_INTERACTIONS_DEMO=str(directory))
    install_resources(directory, home, hermes, env, plugin_name=PLUGIN)
    manifest = {"model": args.model, "provider": "openai-codex", "database": str(home / "state.db"),
                "status": "running", "expected_agents": 4, "history_mode": "active"}
    metadata = directory / "run.json"
    metadata.write_text(json.dumps(manifest, indent=2) + "\n")
    (directory / "prompt.txt").write_text(PROMPT)
    print(f"Run: {directory}\nNative history: {home / 'state.db'}", flush=True)
    command = [*profile_command(hermes, home), "--provider", "openai-codex", "--model", args.model,
               "--toolsets", "flow_interaction_demo,delegation", "--reasoning", "low", "--ignore-rules",
               "--usage-file", str(directory / "usage.json"), "-z", PROMPT]
    try:
        with (directory / "stdout.log").open("w") as stdout, (directory / "stderr.log").open("w") as stderr:
            result = subprocess.run(command, env=env, cwd=directory, stdout=stdout, stderr=stderr, timeout=args.timeout)
        if result.returncode:
            raise ValueError(f"Hermes exited {result.returncode}; inspect {directory / 'stderr.log'}")
        manifest.update(verify(directory), status="complete")
    except (ValueError, OSError, sqlite3.Error, subprocess.TimeoutExpired) as exc:
        manifest.update(status="failed", error=str(exc))
        raise
    finally:
        metadata.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    sid = manifest["root_session_id"]
    print(f'hermes sessions list\nhermes flow baseline create --session-id {sid} --out trusted-paths-{sid}.json\n'
          f'hermes flow --session-id {sid} --compare-with-trusted-paths trusted-paths-{sid}.json --open')
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, sqlite3.Error, subprocess.TimeoutExpired) as exc:
        print(f"live-interactions-demo: {exc}", file=sys.stderr)
        raise SystemExit(2)

#!/usr/bin/env python3
"""Prepare and run five native Hermes agents against the Codex provider."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from uuid import uuid4

from demo_tools import seed

HERE = Path(__file__).resolve().parent

PROMPT = """Run the fictional customer-review demo. You are the coordinator.
Use only the customer_review, skills and delegation tools available to you.
There must be exactly FIVE agents total, including you: coordinator, data analyst,
privacy specialist, report writer and reviewer. Never retry spawning a replacement.
All customer data is synthetic. Do not send emails or access external websites.

1. Load skill_view(name='demo-coordinator').
2. Delegate ONE task to a data analyst. Its goal must repeat these instructions:
   Load demo-analyst with skill_view. Call demo_portfolio_summary and
   demo_customer_lookup(customer_id=1). Write analysis.json containing ONLY the
   aggregate summary. Delegate exactly ONE privacy specialist: it must load
   demo-privacy with skill_view, look up synthetic customer 1, use demo_redact on
   the email, national ID and account values, then write privacy.json with the
   masked example and the masking limitations. It must NOT delegate further.
   Analyst must wait for privacy to finish and return artifact names.
3. WAIT for the analyst and privacy specialist to finish before the next spawn.
4. Delegate ONE task to a report writer. Its goal must repeat these instructions:
   Load demo-writer with skill_view. Read analysis.json and privacy.json. Write
   draft.md with the aggregate counts and amounts, a masked example, and a clear
   synthetic-data label. Delegate exactly ONE reviewer: it must load demo-reviewer
   with skill_view, read draft.md AND analysis.json AND privacy.json, check the
   draft against the aggregates and check that raw email/national ID/account values
   are absent, write review.json with findings and approved true/false, and write
   report.md with a corrected final report if necessary. Call demo_audit_receipt.
   Reviewer must NOT delegate further. Writer waits for reviewer and returns paths.
5. WAIT for writer/reviewer. Read review.json and report.md yourself. Call
   demo_audit_receipt. Return a brief summary and artifact filenames. Do not create
   any further agents. Never claim completion if a child or required artifact failed.

Handoffs are delegate_task calls plus local artifacts; no external delivery.
When delegating, include the exact specialist skill names and tool steps above
in each goal, because children have independent contexts.
"""


def prepare(directory, model, *, active_home):
    directory.mkdir(parents=True, exist_ok=False)
    home = directory / "resources"
    plugin = home / "plugins" / "customer-review-demo"
    plugin.mkdir(parents=True)
    shutil.copy2(HERE / "demo_tools.py", plugin / "__init__.py")
    (plugin / "plugin.yaml").write_text(
        'name: customer-review-demo\nversion: "0.1.0"\ndescription: Fictional customer review tools\ncapabilities: []\n', encoding="utf-8")
    shutil.copytree(HERE / "skills", home / "skills")
    seed(directory / "data")
    (directory / "prompt.txt").write_text(PROMPT, encoding="utf-8")
    manifest = {"model": model, "provider": "openai-codex", "status": "prepared",
                "synthetic_data": True, "expected_agents": 5,
                "history_mode": "active", "database": str(active_home / "state.db")}
    (directory / "run.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return directory


def hermes_command(hermes, arguments, *, env=None):
    result = subprocess.run([hermes, *arguments], env=env, text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError(f"Hermes {' '.join(arguments)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def find_hermes():
    """Find Hermes even when its launcher is not on the user's PATH."""
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


def active_profile(hermes):
    # Ask Hermes itself so sticky `hermes profile use` and HERMES_HOME agree
    # with the profile used by `hermes sessions list`.
    config = Path(hermes_command(hermes, ["config", "path"])).expanduser()
    if not config.is_absolute() or config.name != "config.yaml":
        raise ValueError("Hermes config path did not return an absolute config.yaml path")
    return config.parent.resolve()


def profile_command(hermes, home):
    # Named homes are pinned by HERMES_HOME. Pin a root home to default so a
    # concurrent sticky-profile switch cannot redirect setup or execution.
    return [hermes] if home.parent.name == "profiles" else [hermes, "--profile", "default"]


def install_resources(directory, home, hermes, env, *, plugin_name="customer-review-demo"):
    """Install only demo-owned files; configure through Hermes's native API."""
    resources = directory / "resources"
    files = [(p, home / p.relative_to(resources)) for p in resources.rglob("*") if p.is_file()]
    for source, target in files:
        if (not target.resolve().is_relative_to(home.resolve()) or target.is_symlink()
                or (target.exists() and (not target.is_file() or target.read_bytes() != source.read_bytes()))):
            raise ValueError(f"Demo resource conflicts with existing file {target}; resolve it before rerunning")
    for source, target in files:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    command = profile_command(hermes, home)

    def invoke(*args):
        return hermes_command(command[0], [*command[1:], *args], env=env)

    try:
        invoke("plugins", "enable", plugin_name)
    except ValueError as exc:
        plugin_dir = resources / "plugins" / plugin_name
        manifest = plugin_dir / "plugin.yaml"
        manifest_text = manifest.read_text(encoding="utf-8") if manifest.is_file() else ""
        has_dependency_manifest = any(
            (plugin_dir / name).exists()
            for name in ("pyproject.toml", "requirements.txt", "package.json")
        ) or any(key in manifest_text for key in ("python_dependencies:", "pip_dependencies:", "node_dependencies:"))
        message = str(exc)
        if has_dependency_manifest or "Errno 17" not in message or "share/terminfo/" not in message:
            raise
        # Hermes PM can fail copying case-distinct terminfo directories onto a
        # case-insensitive filesystem. These demo plugins have no dependencies,
        # so preserve the active plugin selection through Hermes config commands
        # instead of syncing an unchanged environment. Never bypass admission for
        # a plugin that declares dependencies.
        def plugin_names(key):
            try:
                value = json.loads(invoke("config", "get", f"plugins.{key}", "--json"))
            except ValueError as config_error:
                if f"Config key not set: plugins.{key}" not in str(config_error):
                    raise
                return []
            if value is None:
                return []
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"Hermes plugins.{key} must be a list of plugin names") from exc
            return value

        enabled = plugin_names("enabled")
        disabled = plugin_names("disabled")
        if plugin_name in disabled:
            invoke("config", "set", "plugins.disabled",
                   json.dumps([name for name in disabled if name != plugin_name]))
        if plugin_name not in enabled:
            invoke("config", "set", "plugins.enabled", json.dumps([*enabled, plugin_name]))
        print("Enabled dependency-free demo plugin without PM sync after terminfo copy collision.", flush=True)
    depth = json.loads(invoke("config", "get", "delegation.max_spawn_depth", "--json"))
    if depth is None or int(depth) < 2:
        invoke("config", "set", "delegation.max_spawn_depth", "2")
        print("Enabled nested delegation in this profile: delegation.max_spawn_depth=2", flush=True)


def verify(directory):
    """Check actual persisted evidence, not the model's claim of completion."""
    metadata = json.loads((directory / "run.json").read_text())
    dbpath = Path(metadata.get("database", directory / "profiles" / "demo" / "state.db"))
    if not dbpath.is_file():
        raise ValueError("No native Hermes history was recorded")
    with closing(sqlite3.connect(dbpath.as_uri() + "?mode=ro", uri=True)) as db:
        sessions = db.execute("SELECT id,parent_session_id FROM sessions ORDER BY started_at").fetchall()
        root = metadata.get("root_session_id")
        usage = directory / "usage.json"
        if not root and usage.is_file():
            root = json.loads(usage.read_text()).get("session_id")
        if root:
            known = dict(sessions)
            if root not in known:
                raise ValueError("The demo root session is absent from native history")
            selected = {root}
            while True:
                more = {sid for sid, parent in sessions if parent in selected}
                if more <= selected:
                    break
                selected |= more
            sessions = [(sid, parent) for sid, parent in sessions if sid in selected]
        elif metadata.get("history_mode") == "active":
            raise ValueError("No demo session ID in usage.json; refusing to guess from shared history")
        roots = [sid for sid, parent in sessions if parent is None]
        calls = []
        skills = set()
        placeholders = ",".join("?" for _ in sessions)
        for (encoded,) in db.execute(
                f"SELECT tool_calls FROM messages WHERE tool_calls IS NOT NULL AND session_id IN ({placeholders})",
                [sid for sid, _ in sessions]):
            for call in json.loads(encoded):
                function = call.get("function", {})
                entries = [function]
                if function.get("name") == "tool_call":
                    args = function.get("arguments", {})
                    args = json.loads(args) if isinstance(args, str) else args
                    entries = args.get("calls", [args])
                    entries = json.loads(entries) if isinstance(entries, str) else entries
                    entries = [entries] if isinstance(entries, dict) else entries
                for entry in entries:
                    calls.append(entry.get("name"))
                    if entry.get("name") == "skill_view":
                        args = entry.get("arguments", {})
                        if isinstance(args, str):
                            args = json.loads(args)
                        skills.add(args.get("name"))
    if len(sessions) != 5 or len(roots) != 1:
        raise ValueError(f"Expected 5 native sessions and 1 root; found {len(sessions)} and {len(roots)}")
    parents = dict(sessions)
    children = [sid for sid, parent in sessions if parent == roots[0]]
    if len(children) != 2 or sorted(sum(parent == child for _, parent in sessions) for child in children) != [1, 1]:
        raise ValueError("Expected two direct children and two nested children")
    if any(parent is not None and parent not in parents for parent in parents.values()):
        raise ValueError("Unresolved native session parent")
    expected_skills = {"demo-coordinator", "demo-analyst", "demo-privacy", "demo-writer", "demo-reviewer"}
    if not expected_skills <= skills:
        raise ValueError(f"Missing recorded skill loads: {sorted(expected_skills - skills)}")
    expected_tools = {"demo_customer_lookup", "demo_portfolio_summary", "demo_redact",
                      "demo_artifact_read", "demo_artifact_write", "demo_audit_receipt"}
    if not expected_tools <= set(calls) or calls.count("delegate_task") < 4:
        raise ValueError("Missing recorded tool calls or delegations")
    artifacts = directory / "data" / "artifacts"
    for name in ("analysis.json", "privacy.json", "draft.md", "review.json", "report.md"):
        if not (artifacts / name).is_file():
            raise ValueError(f"Missing artifact {name}")
    review = json.loads((artifacts / "review.json").read_text())
    if review.get("approved") is not True:
        raise ValueError("Reviewer did not approve the report")
    report = (artifacts / "report.md").read_text()
    if "synthetic" not in report.lower():
        raise ValueError("Final report is missing its synthetic-data label")
    analysis = json.loads((artifacts / "analysis.json").read_text())
    privacy = json.loads((artifacts / "privacy.json").read_text())
    if not isinstance(privacy, dict) or "[MASKED]" not in json.dumps(privacy):
        raise ValueError("Privacy handoff is missing its masked example")
    with closing(sqlite3.connect(directory / "data" / "customers.db")) as db:
        count, balance = db.execute("SELECT COUNT(*), SUM(balance) FROM customers").fetchone()
        overdue, amount = db.execute("SELECT COUNT(*), SUM(amount) FROM invoices WHERE overdue_days>0").fetchone()
        expected = {"customers": count, "total_balance": balance, "overdue_invoices": overdue, "overdue_amount": amount}
        if any(analysis.get(key) != value for key, value in expected.items()):
            raise ValueError("Analysis aggregates do not match SQLite")
        for row in db.execute("SELECT email,national_id,bank_account FROM customers"):
            if any(value in report or value in json.dumps(privacy) or value in json.dumps(analysis) for value in row):
                raise ValueError("Report or handoff contains an unmasked synthetic identifier")
    return {"root_session_id": roots[0], "session_ids": [s[0] for s in sessions],
            "recorded_calls": len(calls), "verified": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, help="New capture directory (never overwrites)")
    parser.add_argument("--model", default=os.environ.get("DEMO_CODEX_MODEL", "gpt-5.6-luna"))
    parser.add_argument("--prepare-only", action="store_true", help="Seed SQLite and configure the project without model calls")
    parser.add_argument("--verify", type=Path, metavar="RUN", help="Verify an existing run without model calls")
    parser.add_argument("--timeout", type=int, default=900, help="Wall-clock limit for the entire Hermes run")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify(args.verify.resolve()), indent=2))
        return 0
    hermes = find_hermes()
    if not hermes:
        raise ValueError("Install Hermes and authenticate with `hermes auth add openai-codex` first")
    home = active_profile(hermes)
    directory = (args.directory or HERE / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8])).resolve()
    prepare(directory, args.model, active_home=home)
    print(f"Prepared: {directory}", flush=True)
    if args.prepare_only:
        return 0
    env = dict(os.environ, HERMES_HOME=str(home), CUSTOMER_REVIEW_DATA=str(directory / "data"))
    install_resources(directory, home, hermes, env)
    print(f"Native history: {home / 'state.db'} (active profile)", flush=True)
    command = [*profile_command(hermes, home), "--provider", "openai-codex", "--model", args.model,
               "--toolsets", "customer_review,skills,delegation", "--ignore-rules",
               "--usage-file", str(directory / "usage.json"), "-z", PROMPT]
    manifest_path = directory / "run.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "running"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    try:
        with (directory / "stdout.log").open("w") as stdout, (directory / "stderr.log").open("w") as stderr:
            result = subprocess.run(command, env=env, cwd=directory, stdout=stdout, stderr=stderr, timeout=args.timeout)
        if result.returncode:
            raise ValueError(f"Hermes exited {result.returncode}; inspect {directory / 'stderr.log'}")
        manifest.update(verify(directory))
        manifest["status"] = "complete"
    except (ValueError, subprocess.TimeoutExpired) as exc:
        manifest.update(status="failed", error=str(exc))
        raise
    finally:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    print("Native history is ready for Hermes Flow:")
    print(f"  python3 -m hermes_flow --db {home / 'state.db'} --session-id {manifest['root_session_id']} --out {directory / 'flow.html'}")
    print("Open this run from Hermes directly:")
    print(f"  hermes flow --session-id {manifest['root_session_id']} --open")
    print(f"  hermes flow baseline create --session-id {manifest['root_session_id']} --out trusted-paths-{manifest['root_session_id']}.json")
    print("Visible in your active profile: hermes sessions list")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, sqlite3.Error, subprocess.TimeoutExpired) as exc:
        print(f"customer-review-demo: {exc}", file=sys.stderr)
        raise SystemExit(2)

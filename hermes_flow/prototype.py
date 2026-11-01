"""Deterministic process agents with captured calls; no model or network access."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def worker_env():
    env = os.environ.copy()
    base = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = base + os.pathsep + env.get("PYTHONPATH", "")
    return env


class Agent:
    def __init__(self, config):
        self.config = config
        self.directory = Path(config["directory"])
        self.sid = config["session_id"]
        self.calls = 0

    def call(self, tool, function, security=None, child=None):
        self.calls += 1
        cid = f"{self.sid}-c{self.calls}"
        row = {"session_id": self.sid, "agent_role": self.config["role"],
               "parent_session_id": self.config.get("parent"),
               "parent_call_id": self.config.get("parent_call"),
               "tool": tool, "call_id": cid, "timestamp": time.time(),
               "security": security or {}}
        if child:
            row["child_session_id"] = child
        try:
            result = function(cid)
            row["status"] = "ok"
            return result
        except Exception:
            row["status"] = "error"
            raise
        finally:
            with (self.directory / f"{self.sid}.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row) + "\n")

    def delegate(self, role):
        sid = f"{self.sid}-{role}"
        def launch(cid):
            config = {**self.config, "session_id": sid, "role": role,
                      "parent": self.sid, "parent_call": cid}
            completed = subprocess.run([sys.executable, "-m", "hermes_flow.prototype", "--worker"],
                                       input=json.dumps(config), text=True, capture_output=True, env=worker_env(),
                                       timeout=30, check=True)
            return json.loads(completed.stdout)
        return self.call("delegate_task", launch,
                         {"resource": "agent-dispatch", "operation": "delegate"}, child=sid)

    def read(self, filename):
        return self.call("read_file", lambda _: (self.directory / filename).read_text(),
                         {"resource": filename, "operation": "read"})

    def write(self, filename, text):
        return self.call("write_file", lambda _: (self.directory / filename).write_text(text),
                         {"resource": filename, "operation": "write"})

    def run(self):
        # Receipts are separate from review metadata and prove separate processes ran.
        (self.directory / f"{self.sid}.receipt.json").write_text(json.dumps(
            {"session_id": self.sid, "role": self.config["role"], "pid": os.getpid(), "ppid": os.getppid()}))
        role = self.config["role"]
        if role == "orchestrator":
            self.delegate("researcher")
            self.delegate("auditor")
            self.write("summary.txt", "Research, draft, review, and audit completed.\n")
        elif role == "researcher":
            self.read("brief.txt")
            self.write("research.txt", "Static research: inspect recorded boundaries and handoffs.\n")
            self.delegate("writer")
        elif role == "writer":
            self.read("research.txt")
            self.write("draft.txt", "Static draft: recorded agents, tools, and handoffs.\n")
            self.delegate("reviewer")
        elif role == "reviewer":
            self.read("draft.txt")
            self.write("review.txt", "Static review completed.\n")
        elif role == "auditor":
            self.read("review.txt")
            self.write("audit.txt", "Static audit completed.\n")
        return {"role": role, "session_id": self.sid, "completed": True}


def execute(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "brief.txt").write_text("Create a static agent flow example.\n")
    config = {"directory": str(directory), "session_id": "orchestrator",
              "role": "orchestrator"}
    try:
        completed = subprocess.run([sys.executable, "-m", "hermes_flow.prototype", "--worker"],
                                   input=json.dumps(config), text=True, capture_output=True, env=worker_env(), timeout=60, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Prototype worker failed or timed out; partial evidence retained at {directory}: {exc}") from None
    json.loads(completed.stdout)
    rows = []
    for path in directory.glob("*.jsonl"):
        rows.extend(json.loads(line) for line in path.read_text().splitlines())
    rows.sort(key=lambda row: row["timestamp"])
    (directory / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(Agent(json.load(sys.stdin)).run()))

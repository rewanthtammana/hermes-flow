"""Editable trusted connections; comparison never modifies the baseline."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


IDENTITY = "exact-agent-label"
KINDS = {"uses", "lineage", "handoff", "request", "result", "message", "completion"}


def signature(connection):
    return (connection["source"], connection["kind"], connection["target"])


def identity_warnings(report, aliases=None):
    """Make shared label-based trust visible, including merges caused by aliases."""
    aliases = aliases or {}
    identities = {}
    for agent in report["agents"]:
        label = aliases.get(agent["name"], agent["name"])
        identities.setdefault(label, []).append(agent["id"])
    return [f"Baseline identity {label!r} is shared by {len(sessions)} sessions; "
            "their trusted connections are combined. Check that they represent the same role."
            for label, sessions in sorted(identities.items()) if len(sessions) > 1]


def connections(report, aliases=None):
    """Yield directed connections and the evidence object to annotate.

    Agent and tool namespaces are separate. Missing endpoints retain their IDs
    and a separate namespace so they cannot accidentally match a known agent.
    """
    aliases = aliases or {}
    agents = {a["id"]: a for a in report["agents"]}

    def agent(sid):
        if sid not in agents:
            return "unresolved-agent:" + sid
        name = agents[sid]["name"]
        return "agent:" + aliases.get(name, name)

    for a in report["agents"]:
        if a["parent"]:
            yield {"source": agent(a["parent"]), "kind": "handoff" if a.get("handoff") else "lineage",
                   "target": agent(a["id"])}, a
    for e in report["events"]:
        yield {"source": agent(e["session"]), "kind": "uses", "target": "tool:" + e["tool"]}, e
    for link in report.get("interactions", []):
        yield {"source": agent(link["source"]), "kind": link["kind"], "target": agent(link["target"])}, link


def create(report):
    unique = {signature(c): c for c, _ in connections(report)}
    groups = {}
    for key in sorted(unique):
        c = unique[key]
        group = groups.setdefault(c["source"], {"source": c["source"], "target": {"agents": [], "tools": []}})
        if c["kind"] == "uses":
            group["target"]["tools"].append(c["target"])
        else:
            group["target"]["agents"].append({"name": c["target"], "kind": c["kind"]})
    return {"schema": 2, "identity": IDENTITY, "agent_aliases": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_sessions": [a["id"] for a in report["agents"]],
            "capture_notes": report["coverage"],
            "identity_warnings": identity_warnings(report),
            "connections": list(groups.values())}


def expanded(value):
    """Expand source groups into directed edges without changing the editable file.

    Schema 1 is the original flat edge list; schema 2 groups agent and tool
    targets by source. Validate group shapes before accessing their fields.
    """
    entries = value.get("connections")
    if not isinstance(entries, list):
        raise ValueError("Baseline connections must be an array")
    if value["schema"] == 1:
        yield from entries
        return
    sources = set()
    for group in entries:
        if (not isinstance(group, dict) or set(group) != {"source", "target"}
                or not isinstance(group["source"], str)
                or not group["source"].startswith(("agent:", "unresolved-agent:"))
                or not group["source"].partition(":")[2]):
            raise ValueError("Each source group requires an agent source and target object")
        if group["source"] in sources:
            raise ValueError("Duplicate baseline source; combine its targets in one group")
        sources.add(group["source"])
        targets = group["target"]
        if (not isinstance(targets, dict) or set(targets) != {"agents", "tools"}
                or not isinstance(targets["agents"], list) or not isinstance(targets["tools"], list)):
            raise ValueError("Each target object requires agents and tools arrays")
        for agent in targets["agents"]:
            if (not isinstance(agent, dict) or set(agent) != {"name", "kind"}
                    or not isinstance(agent["kind"], str) or agent["kind"] not in KINDS - {"uses"}):
                raise ValueError("Each agent target requires name and an agent connection kind")
            yield {"source": group["source"], "kind": agent["kind"], "target": agent["name"]}
        for tool in targets["tools"]:
            yield {"source": group["source"], "kind": "uses", "target": tool}


def validate(value):
    if (not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] not in (1, 2)
            or value.get("identity") != IDENTITY):
        raise ValueError("Unsupported connection baseline schema or identity")
    aliases = value.get("agent_aliases")
    if (not isinstance(aliases, dict) or any(not isinstance(k, str) or not k or
            not isinstance(v, str) or not v for k, v in aliases.items())):
        raise ValueError("agent_aliases must map nonempty exact labels to canonical labels")
    # Aliases are one-step mappings, never recursive.
    if any(v in aliases and aliases[v] != v for v in aliases.values()):
        raise ValueError("agent_aliases must map directly to canonical labels (no chains)")
    seen = set()
    for c in expanded(value):
        if (not isinstance(c, dict) or set(c) != {"source", "kind", "target"}
                or any(not isinstance(v, str) or not v for v in c.values())):
            raise ValueError("Each connection requires nonempty source, kind, and target strings")
        if (c["kind"] not in KINDS or not c["source"].startswith(("agent:", "unresolved-agent:"))
                or not c["target"].startswith(("tool:",) if c["kind"] == "uses" else ("agent:", "unresolved-agent:"))
                or not c["source"].partition(":")[2] or not c["target"].partition(":")[2]):
            raise ValueError("Invalid connection kind or endpoint namespace")
        if signature(c) in seen:
            raise ValueError("Duplicate baseline connection")
        seen.add(signature(c))
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read(path):
    return validate(json.loads(Path(path).read_text(encoding="utf-8")))


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, ensure_ascii=True) + "\n")


def canonical(connection, aliases):
    result = connection.copy()
    for end in ("source", "target"):
        namespace, _, name = result[end].partition(":")
        if namespace == "agent":
            result[end] = "agent:" + aliases.get(name, name)
    return result


def compare(report, baseline, name):
    validate(baseline)
    trusted = {signature(canonical(c, baseline["agent_aliases"])) for c in expanded(baseline)}
    observed, counts = {}, Counter()
    for c, evidence in connections(report, baseline["agent_aliases"]):
        key = signature(c)
        evidence["connection_status"] = "trusted" if key in trusted else "deviation"
        observed[key] = c
        counts[key] += 1
    entries = [{**observed[key], "count": counts[key],
                "status": "trusted" if key in trusted else "deviation"} for key in sorted(observed)]
    report["comparison"] = {"baseline": str(name), "sha256": digest(baseline),
                            "identity_warnings": identity_warnings(report, baseline["agent_aliases"]),
                            "identity": IDENTITY, "connections": entries,
                            "deviations": sum(c["status"] == "deviation" for c in entries),
                            "missing": [dict(zip(("source", "kind", "target"), key))
                                        for key in sorted(trusted - observed.keys())]}
    return report

"""Graphs describe recorded lineage and tool use, never inferred data transfer."""
from collections import Counter


def safe(value):
    """Keep untrusted metadata from emitting terminal control sequences."""
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode("ascii") for c in str(value))


def graph(report, mermaid=False, limit=80):
    agents = report["agents"]
    ids = {a["id"]: f"a{i}" for i, a in enumerate(agents)}
    tools = Counter((e["session"], e["tool"]) for e in report["events"])
    new_tools = {(e["session"], e["tool"]) for e in report["events"] if e.get("connection_status") == "deviation"}
    edge_index, red_edges = 0, []
    lines = ["flowchart LR" if mermaid else "Recorded lineage / tool use (edges do not prove data transfer)"]
    if not mermaid:
        children = {}
        for a in agents:
            parent = a["parent"] if a["parent"] in ids else None
            children.setdefault(parent, []).append(a)
        roots = children.get(None, [])
        stack = [(a, "", i == len(roots) - 1) for i, a in reversed(list(enumerate(roots)))]
        shown = 0
        while stack and shown < limit:
            a, prefix, last = stack.pop()
            shown += 1
            lines.append(prefix + ("└── " if last else "├── ") + safe(a["name"]) + " [" + safe(a["id"]) + "]" + (" [NEW CONNECTION]" if a.get("connection_status") == "deviation" else ""))
            indent = prefix + ("    " if last else "│   ")
            if a.get("handoff"):
                lines.append(indent + f"handoff call: {safe(a['handoff']['call_id'])} (correlated)")
            elif a["parent"] and a["parent"] not in ids:
                lines.append(indent + "missing parent: " + safe(a["parent"]))
            observed = [(tool, count) for (sid, tool), count in tools.items() if sid == a["id"]]
            for tool, count in observed[:12]:
                lines.append(indent + f"· {safe(tool)} x{count}" + (" [NEW CONNECTION]" if (a["id"], tool) in new_tools else ""))
            if len(observed) > 12:
                lines.append(indent + f"... {len(observed) - 12} more tools; see HTML")
            nested = children.get(a["id"], [])
            for i, child in reversed(list(enumerate(nested))):
                stack.append((child, indent, i == len(nested) - 1))
        if len(agents) > limit:
            lines.append(f"... {len(agents) - limit} more agents; see HTML or --graph mermaid")
    else:
        # Encode all label characters as Mermaid numeric entities; no raw directives.
        def label(value):
            return "".join(f"#{ord(c)};" for c in str(value))
        for a in agents:
            node = ids[a["id"]]
            lines.append(f'  {node}["{label(a["name"] + " / " + a["id"])}"]')
            if a["parent"] in ids:
                relation = "handoff" if a.get("handoff") else "lineage"
                lines.append(f'  {ids[a["parent"]]} -->|{relation}| {node}')
                if a.get("connection_status") == "deviation":
                    red_edges.append(edge_index)
                edge_index += 1
        for i, ((sid, tool), count) in enumerate(tools.items()):
            lines.append(f'  t{i}["{label(tool + " x" + str(count))}"]')
            lines.append(f"  {ids[sid]} -.->|uses| t{i}")
            if (sid, tool) in new_tools:
                red_edges.append(edge_index)
            edge_index += 1
    interactions = Counter((e["source"], e["target"], e["kind"]) for e in report.get("interactions", [])
                           if e["source"] in ids and e["target"] in ids)
    if interactions and not mermaid:
        lines.append("Agent interactions (aggregated by direction and kind):")
    names = {a["id"]: a["name"] for a in agents}
    new_interactions = {(e["source"], e["target"], e["kind"]) for e in report.get("interactions", [])
                        if e.get("connection_status") == "deviation"}
    for (source, target, kind), count in interactions.items():
        description = "session ended; delivery unverified" if kind == "completion" else kind
        if mermaid:
            arrow = "-.->" if kind == "completion" else "-->"
            lines.append(f"  {ids[source]} {arrow}|{description} x{count}| {ids[target]}")
            if (source, target, kind) in new_interactions:
                red_edges.append(edge_index)
            edge_index += 1
        else:
            lines.append(f"  {safe(names[source])} [{safe(source)}] → {safe(names[target])} [{safe(target)}]: {description} x{count}" + (" [NEW CONNECTION]" if (source, target, kind) in new_interactions else ""))
    if mermaid and red_edges:
        lines.append("  linkStyle " + ",".join(map(str, red_edges)) + " stroke:#c62828,stroke-width:3px")
    return "\n".join(lines)

"""Normalize observed evidence. Never infer handoffs from adjacent timestamps."""
import json
import math
from collections import Counter
from datetime import datetime, timezone


def decoded(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            raise ValueError("Malformed JSON in stored tool_calls/model_config") from None
    return value if value is not None else default


def timestamp(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp()
        except ValueError:
            return None
    return number if math.isfinite(number) else None


def normalize(rows):
    agents, events, issues = {}, [], []
    seen = set()
    native_handoffs = set()
    for row in rows:
        is_session = "messages" in row
        if not is_session and "tool" not in row:
            raise ValueError("Unrecognized record: expected messages (Hermes export) or tool (event JSONL)")
        sid = row.get("id") if is_session else row.get("session_id")
        if not isinstance(sid, str) or not sid:
            raise ValueError("Every session/event needs a nonempty session ID")
        if is_session and sid in agents:
            raise ValueError(f"Duplicate session {sid}; supply it once")
        config = decoded(row.get("model_config"), {})
        if not isinstance(config, dict):
            raise ValueError("model_config must be an object")
        parent = row.get("parent_session_id")
        if parent is not None and (not isinstance(parent, str) or not parent):
            raise ValueError("parent_session_id must be a nonempty string or null")
        role = row.get("agent_role")
        parent_call = row.get("parent_call_id")
        correlation = config.get("_hermes_flow_handoff") if is_session else None
        if correlation is not None:
            if (not isinstance(correlation, dict)
                    or set(correlation) != {"schema", "parent_session_id", "parent_call_id"}
                    or type(correlation["schema"]) is not int or correlation["schema"] != 1
                    or not parent or correlation["parent_session_id"] != parent
                    or not isinstance(correlation["parent_call_id"], str) or not correlation["parent_call_id"]
                    or (parent_call is not None and parent_call != correlation["parent_call_id"])):
                raise ValueError(f"Session {sid}: invalid native flow handoff metadata")
            parent_call = correlation["parent_call_id"]
            native_handoffs.add(sid)
        if parent_call is not None and (not isinstance(parent_call, str) or not parent_call):
            raise ValueError("parent_call_id must be a nonempty string")
        if role is not None and (not isinstance(role, str) or not role):
            raise ValueError("agent_role must be a nonempty string")
        agent = {"id": sid, "name": str(role or row.get("agent") or row.get("title") or row.get("source") or sid),
                 "parent": parent, "relation": "delegation" if config.get("_delegate_from") == parent and parent else "recorded lineage",
                 "ended": bool(row.get("ended_at")), "calls": 0, "parent_call_id": parent_call}
        agent["end_time"] = timestamp(row.get("ended_at"))
        if sid in agents:
            previous = agents[sid]
            if (previous["name"], previous["parent"], previous["parent_call_id"]) != (agent["name"], parent, parent_call):
                raise ValueError(f"Conflicting agent/parent metadata for {sid}")
        else:
            agents[sid] = agent
        if is_session:
            messages = row["messages"]
            if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
                raise ValueError(f"Session {sid}: messages must be an array of objects")
            for message in messages:
                result_id = message.get("tool_call_id")
                if result_id is not None and (not isinstance(result_id, str) or not result_id):
                    raise ValueError(f"Session {sid}: tool_call_id must be a nonempty string")
            messages = [m for m in messages if m.get("active", True) or m.get("compacted", False)]
            results = {m.get("tool_call_id"): m for m in messages if m.get("role") == "tool" and m.get("tool_call_id")}
            called = set()
            for message_index, message in enumerate(messages):
                if message.get("role") != "assistant":
                    continue
                calls = decoded(message.get("tool_calls"), [])
                if not isinstance(calls, list):
                    raise ValueError(f"Session {sid}: tool_calls must be an array")
                for call in calls:
                    if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                        raise ValueError(f"Session {sid}: malformed tool call")
                    name = call["function"].get("name")
                    cid = call.get("id")
                    if cid is not None and (not isinstance(cid, str) or not cid):
                        raise ValueError(f"Session {sid}: call ID must be a nonempty string")
                    if cid:
                        called.add(cid)
                    result = results.get(cid, {})
                    status = "returned" if result else "unknown"
                    if name == "tool_call":
                        resolved = result.get("tool_name")
                        if isinstance(resolved, str) and resolved and resolved != "tool_call":
                            name = resolved
                        else:
                            issues.append(f"{sid}: dynamic tool_call target is unresolved")
                    elif result.get("tool_name") and result["tool_name"] != name:
                        issues.append(f"{sid}: result tool name differs from invocation {name}")
                    if name == "skill_view":
                        try:
                            args = decoded(call["function"].get("arguments"), {})
                        except ValueError:
                            args = {}
                        skill = args.get("name") if isinstance(args, dict) else None
                        if isinstance(skill, str) and skill:
                            name = "skill_view:" + skill
                        else:
                            issues.append(f"{sid}: skill_view name is unavailable")
                    # Results are not read: a returned result can still describe an error.
                    add_event(events, seen, sid, name, cid, message.get("timestamp"), status, message_index)
                    events[-1]["result_time"] = timestamp(result.get("timestamp"))
                    start, end = events[-1]["time"], events[-1]["result_time"]
                    duration = end - start if start is not None and end is not None and end >= start else None
                    events[-1]["duration_seconds"] = duration if duration is not None and math.isfinite(duration) else None
            orphaned = set(results) - called
            if orphaned:
                issues.append(f"{sid}: {len(orphaned)} result(s) have no recorded invocation")
            if not agent["ended"]:
                issues.append(f"{sid}: no end marker; this may be a live or partial session")
        else:
            status = row.get("status", "unknown")
            if not isinstance(status, str) or status not in {"ok", "error", "unknown", "returned"}:
                raise ValueError(f"{sid}: status must be ok, error, returned, or unknown")
            add_event(events, seen, sid, row["tool"], row.get("call_id"), row.get("timestamp"), status)
            metadata = row.get("security", {})
            if not isinstance(metadata, dict) or set(metadata) - {"destination", "resource", "operation"}:
                raise ValueError("security supports only destination, resource, and operation")
            if any(not isinstance(v, str) or not v for v in metadata.values()):
                raise ValueError("security metadata values must be nonempty strings")
            events[-1]["security"] = metadata.copy()
            child = row.get("child_session_id")
            if child is not None:
                if not isinstance(child, str) or not child:
                    raise ValueError("child_session_id must be a nonempty string")
                events[-1]["child_session_id"] = child
            interaction = row.get("interaction")
            if interaction is not None:
                if (not isinstance(interaction, dict) or set(interaction) != {"target_session_id", "kind"}
                        or not isinstance(interaction["target_session_id"], str) or not interaction["target_session_id"]
                        or interaction["kind"] not in ("request", "result", "message")):
                    raise ValueError("interaction requires target_session_id and kind (request, result, or message)")
                events[-1]["interaction"] = interaction.copy()
    if not agents:
        raise ValueError("No sessions found")
    checked = set()
    for agent in agents.values():
        if agent["parent"] and agent["parent"] not in agents:
            issues.append(f"{agent['id']}: parent {agent['parent']} is absent from input")
        visited, current = set(), agent["id"]
        while current in agents and current not in checked:
            if current in visited:
                raise ValueError("Cyclic session parent links")
            visited.add(current)
            current = agents[current]["parent"]
        checked.update(visited)
    linked_parents = {a["parent"] for a in agents.values()}
    for sid in {e["session"] for e in events if e["tool"] == "delegate_task"} - linked_parents:
        issues.append(f"{sid}: delegate_task recorded but no child session links are available")
    confirmed = set()
    native_children = {}
    for sid in native_handoffs:
        child = agents[sid]
        native_children.setdefault((child["parent"], child["parent_call_id"]), []).append(sid)
    for event in events:
        # Native lifecycle observers record exact parent call IDs on the actual
        # child session. Resolve only a unique delegation with both identities;
        # timestamps, goals, and transcript bodies are never used to infer links.
        matches = native_children.get((event["session"], event["call_id"]), [])
        if event["tool"] == "delegate_task" and len(matches) == 1 and "child_session_id" not in event:
            event["child_session_id"] = matches[0]
        child_id = event.get("child_session_id")
        if child_id is None:
            if event["tool"] == "delegate_task" and event["session"] in linked_parents:
                issues.append(f"{event['session']}: delegation call {event['call_id'] or event['id']} is unverified; child lineage is not correlated to this call")
            continue
        child = agents.get(child_id)
        if (event["call_id"] and child and child["parent"] == event["session"]
                and child["parent_call_id"] == event["call_id"] and child_id not in confirmed):
            confirmed.add(child_id)
            child["handoff"] = {"call_id": event["call_id"], "event": event["id"]}
            child["relation"] = "correlated handoff"
        else:
            issues.append(f"{event['session']}: child {child_id} has missing, conflicting, or duplicate handoff correlation")
    for agent in agents.values():
        if agent["parent_call_id"] and agent["id"] not in confirmed:
            issues.append(f"{agent['id']}: parent_call_id has no matching parent invocation")
    counts = Counter(e["session"] for e in events)
    for agent in agents.values():
        agent["calls"] = counts[agent["id"]]
    unknown = sum(e["status"] == "unknown" for e in events)
    if unknown:
        issues.append(f"{unknown} tool call(s) have no recorded outcome")
    missing_times = sum(e["time"] is None for e in events)
    if missing_times:
        issues.append(f"{missing_times} tool call(s) have no valid timestamp; source order is retained per agent")
    # Source order is authoritative within each session; timestamps are display metadata.
    interactions = []
    for event in events:
        explicit = event.get("interaction")
        if explicit:
            target = explicit["target_session_id"]
            if target not in agents:
                issues.append(f"{event['session']}: interaction target {target} is absent from input")
            interactions.append({"source": event["session"], "target": target, "kind": explicit["kind"],
                                 "evidence": "explicit event", "event": event["id"], "time": event["time"]})
    for agent in agents.values():
        if not agent["parent"] or agent["parent"] not in agents:
            continue
        handoff = agent.get("handoff")
        event = events[handoff["event"]] if handoff else None
        if event and agent["id"] in native_handoffs:
            interactions.append({"source": agent["parent"], "target": agent["id"], "kind": "request",
                                 "evidence": "native delegation lifecycle", "event": event["id"], "time": event["time"]})
        if event and event["status"] == "returned":
            interactions.append({"source": agent["id"], "target": agent["parent"], "kind": "result",
                                 "evidence": "correlated tool result", "event": event["id"],
                                 "time": event.get("result_time")})
        elif agent["ended"]:
            interactions.append({"source": agent["id"], "target": agent["parent"], "kind": "completion",
                                 "evidence": "session ended; result delivery unverified", "time": agent["end_time"]})
    return {"schema": 1, "agents": list(agents.values()), "events": events,
            "interactions": interactions, "coverage": issues}


def add_event(events, seen, sid, name, cid, time, status, batch=None):
    if not isinstance(name, str) or not name:
        raise ValueError(f"Session {sid}: tool name is missing")
    if cid is not None and (not isinstance(cid, str) or not cid):
        raise ValueError(f"Session {sid}: call ID must be a nonempty string")
    if cid:
        key = (sid, cid)
        if key in seen:
            raise ValueError(f"Duplicate tool call ID {cid} in {sid}")
        seen.add(key)
    events.append({"id": len(events), "session": sid, "tool": name, "call_id": cid,
                   "time": timestamp(time), "status": status, "batch": batch if batch is not None else len(events)})

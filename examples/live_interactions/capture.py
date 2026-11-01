"""Record native delegation correlation IDs only while the live demo runs."""
import json
import os
import threading

_pending = {}
_lock = threading.Lock()
MARKER = "_hermes_flow_handoff"


def before(tool_name=None, session_id=None, tool_call_id=None, turn_id=None, **kwargs):
    if (not os.environ.get("HERMES_FLOW_INTERACTIONS_DEMO") or tool_name != "delegate_task"
            or not session_id or not tool_call_id):
        return
    with _lock:
        calls = _pending.setdefault((session_id, turn_id or ""), {})
        calls.setdefault(tool_call_id, {"children": set(), "ambiguous": False})
        if len(calls) > 1:
            for call in calls.values():
                call["ambiguous"] = True


def started(parent_session_id=None, parent_turn_id=None, child_session_id=None, **kwargs):
    if not os.environ.get("HERMES_FLOW_INTERACTIONS_DEMO") or not child_session_id:
        return
    with _lock:
        calls = _pending.get((parent_session_id, parent_turn_id or ""), {})
        if len(calls) == 1:
            next(iter(calls.values()))["children"].add(child_session_id)


def after(tool_name=None, session_id=None, tool_call_id=None, turn_id=None, result=None, **kwargs):
    if not os.environ.get("HERMES_FLOW_INTERACTIONS_DEMO") or tool_name != "delegate_task":
        return
    key = (session_id, turn_id or "")
    with _lock:
        calls = _pending.get(key, {})
        call = calls.pop(tool_call_id, None)
        if not calls:
            _pending.pop(key, None)
    if not call or call["ambiguous"] or len(call["children"]) != 1:
        return
    try:
        envelope = json.loads(result) if isinstance(result, str) else result
    except (TypeError, ValueError):
        return
    entries = envelope.get("results") if isinstance(envelope, dict) else None
    # A background dispatch handle or a generic error does not establish delivery.
    # Check the structured envelope; summary contents are never inspected/stored.
    if (not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict)
            or not isinstance(entries[0].get("summary"), str) or entries[0].get("status") != "completed"):
        return
    child = next(iter(call["children"]))
    from hermes_state import SessionDB
    db = SessionDB()
    try:
        row = db.get_session(child)
        if not row or row.get("parent_session_id") != session_id:
            return
        db.patch_session_model_config(child, {MARKER: {
            "schema": 1, "parent_session_id": session_id, "parent_call_id": tool_call_id,
        }})
    finally:
        db.close()


def register(ctx):
    if not os.environ.get("HERMES_FLOW_INTERACTIONS_DEMO"):
        return
    ctx.register_hook("pre_tool_call", before)
    ctx.register_hook("subagent_start", started)
    ctx.register_hook("post_tool_call", after)
    for name, properties, handler in [
        ("flow_demo_multiply", {"a": {"type": "integer"}, "b": {"type": "integer"}},
         lambda args, **kwargs: json.dumps({"number": args["a"] * args["b"]})),
        ("flow_demo_format", {"number": {"type": "integer"}},
         lambda args, **kwargs: json.dumps({"text": f"Result: {args['number']}"})),
    ]:
        ctx.register_tool(name=name, toolset="flow_interaction_demo", handler=handler,
                          schema={"name": name, "description": "Local arithmetic/formatting for the live Flow demo.",
                                  "parameters": {"type": "object", "properties": properties,
                                                 "required": list(properties), "additionalProperties": False}})

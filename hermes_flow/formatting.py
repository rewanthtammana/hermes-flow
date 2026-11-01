"""Readable terminal metadata with control characters escaped."""
import time

from .graph import safe


def session_table(rows, now=None):
    if not rows:
        return "No sessions found."
    now = time.time() if now is None else now
    lines = [f"{'Title':<28} {'Workspace':<18} {'Last Active':<13} ID", "─" * 110]
    for row in rows:
        title = safe(row.get("title") or "—")[:26]
        workspace = (row.get("git_repo_root") or "").strip() or (row.get("cwd") or "").strip()
        workspace = safe(workspace.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "—")[:16]
        active = row.get("last_active")
        if active is None:
            ago = "—"
        else:
            seconds = max(0, now - active)
            ago = ("just now" if seconds < 60 else f"{int(seconds // 60)}m ago" if seconds < 3600
                   else f"{int(seconds // 3600)}h ago" if seconds < 86400 else f"{int(seconds // 86400)}d ago")
        lines.append(f"{title:<28} {workspace:<18} {ago:<13} {safe(row['id'])}")
    return "\n".join(lines)

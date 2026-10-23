"""Per-session JSONL traces: one file per session in `traces/<session_id>.jsonl`.

Owner: MLOps/DevOps.

Each line is one event: a tax_math tool call, or an agent node starting and finishing with
its duration. Agents mark the session they are working on with `session(session_id)`, and
everything traced inside that block (including `call_tool`) lands in that session's file.

    from src.tools.tracing import session, traced_node

    with session(state["session_id"]), traced_node("computation"):
        ...

Set SILVERFILE_TRACE_DIR to write somewhere other than `traces/` (tests use a temp dir).
Outside a `session(...)` block nothing is written. Tracing never raises: a trace that can't be
written must not stop a tax return being drafted.
"""
from __future__ import annotations

import contextvars
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("silverfile_session", default=None)
_node: contextvars.ContextVar[str | None] = contextvars.ContextVar("silverfile_node", default=None)


def trace_dir() -> Path:
    return Path(os.getenv("SILVERFILE_TRACE_DIR", "traces"))


def current_session() -> str | None:
    return _session_id.get()


@contextmanager
def session(session_id: str | None) -> Iterator[None]:
    """Route every trace event inside this block to `traces/<session_id>.jsonl`."""
    token = _session_id.set(session_id or None)
    try:
        yield
    finally:
        _session_id.reset(token)


def write_event(event: dict[str, Any]) -> None:
    """Append one event to the current session's trace file (no-op outside a session)."""
    session_id = _session_id.get()
    if not session_id:
        return
    record = {"ts": datetime.now(timezone.utc).isoformat(), "session_id": session_id, "node": _node.get(), **event}
    try:
        path = trace_dir() / f"{_safe_name(session_id)}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass


@contextmanager
def traced_node(agent: str) -> Iterator[None]:
    """Write node_start / node_end events (with duration) around an agent's work."""
    token = _node.set(agent)
    started = time.perf_counter()
    write_event({"kind": "node_start", "agent": agent})
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        write_event({"kind": "node_end", "agent": agent, "outcome": outcome,
                     "duration_ms": round((time.perf_counter() - started) * 1000, 2)})
        _node.reset(token)


def _safe_name(session_id: str) -> str:
    """Keep the file name inside the trace directory whatever the session id contains."""
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in session_id).lstrip(".") or "session"

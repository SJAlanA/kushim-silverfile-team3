"""Checkpointing, session resumability and the context-window strategy.

Owner: Agent memory.
"""
from __future__ import annotations

import os
import sqlite3

from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from src.agents.intake_questions import BY_ID, QUESTIONS, next_question_id
from src.state import SilverFileState

DEFAULT_DB = "silverfile_checkpoints.sqlite"
SUMMARY_KEEP_ITEMS = 5
STATUS_WORDS = {"single": "single", "mfj": "married filing jointly", "mfs": "married filing separately",
                "hoh": "head of household", "qss": "qualifying surviving spouse"}


def _db_path(db_path: str | None) -> str:
    return db_path or os.getenv("CHECKPOINT_DB", DEFAULT_DB)


def get_checkpointer(db_path: str | None = None) -> SqliteSaver:
    """SqliteSaver on a file, so a session abandoned on Tuesday resumes on Friday by thread_id."""
    saver = SqliteSaver(sqlite3.connect(_db_path(db_path), check_same_thread=False))
    saver.setup()
    return saver


def list_sessions(db_path: str | None = None) -> list[dict]:
    """One row per session for the dashboard, most recently updated first."""
    path = _db_path(db_path)
    if not os.path.exists(path):
        return []
    saver = get_checkpointer(path)
    try:
        thread_ids = [row[0] for row in saver.conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints WHERE checkpoint_ns = ''")]
        rows = []
        for thread_id in thread_ids:
            latest = saver.get_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
            rows.append(_session_row(thread_id, latest.checkpoint["channel_values"], latest.checkpoint["ts"]))
    finally:
        saver.conn.close()
    return sorted(rows, key=lambda row: row["updated_at"], reverse=True)


def _session_row(thread_id: str, values: dict, updated_at: str) -> dict:
    first_name = values.get("intake", {}).get("taxpayer_first_name")
    return {
        "session_id": values.get("session_id", thread_id),
        "status": values.get("status", "intake"),
        "open_hitl": sum(1 for event in values.get("hitl", []) if event["status"] == "open"),
        "updated_at": updated_at,
        "taxpayer": first_name["value"] if first_name else "",
        "released": bool(values.get("release")),
    }


def resume_summary(state: SilverFileState) -> str:
    """Plain-language recap said first when a session resumes. No dollar amounts, no SSNs."""
    intake, meta = state.get("intake", {}), state.get("intake_meta", {})
    covered = _covered_topics(intake)
    pending = meta.get("pending")
    next_qid = pending["question_id"] if pending else next_question_id(intake, meta)
    if not covered:
        recap = "Welcome back. We hadn't started the questions yet, so let's begin."
    else:
        recap = f"Welcome back. Last time we covered {_join(covered)}."
    if next_qid:
        recap += f" Next, I'd like to ask about {BY_ID[next_qid].topic}." if covered else ""
    elif covered:
        recap += " We finished all of the questions."
    open_flags = sum(1 for event in state.get("hitl", []) if event["status"] == "open")
    if open_flags:
        recap += (" One question is waiting for a volunteer to look at." if open_flags == 1
                  else f" {open_flags} questions are waiting for a volunteer to look at.")
    return recap


def _covered_topics(intake: dict) -> list[str]:
    topics: list[str] = []
    for question in QUESTIONS:
        if question.qid not in intake or question.qid == "who_is_answering":
            continue
        topic = question.topic
        if question.qid == "filing_status":
            topic = f"{topic} ({STATUS_WORDS.get(intake['filing_status']['value'], 'noted')})"
        if topic not in topics:
            topics.append(topic)
    return topics


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def resume_session(graph, session_id: str) -> dict:
    """Load a saved session: {"summary": recap, "pending": interrupt payload or None}."""
    snapshot = graph.get_state({"configurable": {"thread_id": session_id}})
    pending = next((interrupt.value for task in snapshot.tasks for interrupt in task.interrupts), None)
    return {"summary": resume_summary(snapshot.values), "pending": pending}


def current_item(state: SilverFileState) -> str:
    return state.get("current_item") or state.get("session_id", "")


def message_item(message) -> str | None:
    if isinstance(message, BaseMessage):
        return message.additional_kwargs.get("item")
    return message.get("item")


def apply_context_strategy(messages: list, state: SilverFileState, enabled: bool) -> list:
    """Selective recall: only the current item's messages plus the rolling decisions summary."""
    if not enabled:
        return list(messages)
    item = current_item(state)
    recent = [message for message in messages if message_item(message) == item]
    summary = state.get("decisions_summary")
    return ([SystemMessage(content=f"Decisions so far:\n{summary}")] if summary else []) + recent


def update_decisions_summary(previous: str, item: str, state: SilverFileState) -> str:
    """Add one line for a finished item; keep the last few lines and a count of the rest."""
    lines = [line for line in (previous or "").splitlines() if line.startswith("- ")]
    earlier = int(next((line.split()[1] for line in (previous or "").splitlines()
                        if line.startswith("Earlier:")), 0))
    lines.append(f"- {item}: {_item_decisions(state)}")
    while len(lines) > SUMMARY_KEEP_ITEMS:
        lines.pop(0)
        earlier += 1
    head = [f"Earlier: {earlier} items finished."] if earlier else []
    return "\n".join(head + lines)


def _item_decisions(state: SilverFileState) -> str:
    intake = state.get("intake", {})
    status = intake.get("filing_status")
    documents = [doc["doc_type"] for doc in state.get("documents", [])]
    flags = sum(1 for event in state.get("hitl", []) if event["status"] == "open")
    return "; ".join([
        f"filing status {STATUS_WORDS.get(status['value'], 'unknown') if status else 'not given'}",
        f"documents {', '.join(documents) if documents else 'none'}",
        f"{flags} flagged for a volunteer" if flags else "no flags",
    ])


def estimate_tokens(text: str) -> int:
    """Deterministic offline stand-in for a tokenizer: about four characters per token."""
    return len(text) // 4


def log_tokens(agent: str, item: str, input_tokens: int, strategy: bool, estimated: bool = False) -> dict:
    return {"agent": agent, "item": item, "input_tokens": input_tokens, "strategy": strategy,
            "estimated": estimated}

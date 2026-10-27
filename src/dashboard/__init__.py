"""Kushim staff dashboard: sessions in progress and finished, and HITL events to resolve.

Owner: Task division and management.

Server-rendered Flask pages with one stylesheet and no external assets, so it works offline
in Docker. Resolving an event resumes the session's graph with `Command(resume=...)` on the
same SQLite checkpoint the CLI uses, so a CLI waiting on that event carries on by itself.

    python run.py --dashboard        # http://localhost:8000
"""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for
from langgraph.types import Command

from src.agents import orchestrator
from src.agents.intake_questions import BY_ID
from src.dashboard.views import documents, key_figures, line_sections, outcome, readable
from src.graph import SEND_BACK, build_graph, validate_decision
from src.memory import get_checkpointer, list_sessions

GROUPS = (("needs_human", "Needs a volunteer"), ("in_progress", "In progress"), ("completed", "Draft ready"))
# What each home-page group means, shown in the info tip beside its heading, and what it says when empty.
GROUP_INFO = {
    "needs_human": ("SilverFile has paused these returns because something needs a person to check, such as an "
                    "unclear answer or a document it couldn't read. A volunteer reviews it and decides how to go on.",
                    "Nothing needs a volunteer right now. Returns that need a decision will appear here."),
    "in_progress": ("SilverFile is still preparing these returns: the taxpayer is answering questions, or their "
                    "documents are being read and the numbers worked out. Nothing is needed from you yet.",
                    "No returns are being prepared at the moment. New ones will appear here as they start."),
    "completed": ("These draft returns are finished and have passed SilverFile's checks. A volunteer goes through "
                  "the draft with the taxpayer before anything is filed.",
                  "Finished draft returns will appear here."),
}
# Why a return is waiting, in plain words (contract HITL conditions).
CONDITION_LABELS = {
    "OUT_OF_SCOPE": "Something outside what SilverFile can prepare",
    "DOC_CONFLICT": "Documents disagree",
    "R1099_TAXABLE_NOT_DETERMINED": "Retirement income needs checking",
    "TAX_ADVICE": "Taxpayer asked for advice",
    "FILING_STATUS_AMBIGUITY": "Filing status needs a decision",
    "EXPLOITATION": "Possible pressure or misuse",
    "UNCERTAIN": "An answer or document is unclear",
    "REVIEW_LIMIT": "The return failed its checks repeatedly",
    "LOOP_GUARD": "SilverFile got stuck",
}
FINISHED = ("complete", "escalated")
# What a send-back asks each agent to do, in a volunteer's words.
REDO = {"intake": "ask the taxpayer again", "extract_dispatch": "read the documents again",
        "eligibility": "check eligibility again", "computation": "work out the numbers again",
        "review": "check the return again"}
DECISION_LABELS = {"continue": "Carry on with the return as it is",
                   **{f"{SEND_BACK}{agent}": REDO[agent].capitalize() for agent in orchestrator.WORKERS},
                   "stop": "Stop here and prepare this return by hand"}
# The same decisions in the past tense, for the history on the return page and the confirmation.
DECISION_DONE = {"continue": "Carried on", "stop": "Stopped",
                 **{f"{SEND_BACK}{agent}": f"Sent back to {REDO[agent]}" for agent in orchestrator.WORKERS}}
_SSN = re.compile(r"\b\d{3}[- ]?\d{2}[- ]?(\d{4})\b")


def create_app(db_path: str | None = None) -> Flask:
    """Build the app. `db_path` defaults to CHECKPOINT_DB, the same file the CLI writes."""
    app = Flask(__name__)
    app.secret_key = os.getenv("DASHBOARD_SECRET") or secrets.token_hex(16)
    app.jinja_env.filters["when"] = format_time
    app.jinja_env.filters["dollars"] = lambda value: f"{value:,}" if isinstance(value, int) else value
    app.jinja_env.filters["plain"] = plain
    app.jinja_env.filters["reference"] = reference
    app.jinja_env.globals["condition_label"] = lambda c: CONDITION_LABELS.get(c, c)
    app.jinja_env.globals["decision_done"] = lambda d: DECISION_DONE.get(d, d)
    graph = build_graph(get_checkpointer(db_path))

    def state_of(session_id: str) -> dict:
        values = graph.get_state(_config(session_id)).values
        if not values:
            abort(404)
        return values

    @app.get("/")
    def sessions():
        groups = group_sessions(list_sessions(db_path))
        for row in groups["needs_human"]:
            row["reasons"] = list(dict.fromkeys(
                CONDITION_LABELS.get(e["condition"], e["condition"])
                for e in orchestrator.open_events(state_of(row["session_id"])))) or ["Waiting for a volunteer"]
        return render_template("sessions.html", groups=groups, labels=GROUPS, info=GROUP_INFO)

    @app.get("/hitl")
    def queue():
        items = [(row, event) for row in list_sessions(db_path) if row["open_hitl"]
                 for event in orchestrator.open_events(state_of(row["session_id"]))]
        return render_template("queue.html", items=items)

    @app.get("/sessions/<session_id>")
    def session(session_id: str):
        values = state_of(session_id)
        return render_template("session.html", session_id=session_id, s=values,
                               answers=masked_answers(values.get("intake", {})),
                               sections=line_sections(values), outcome=outcome(values),
                               figures=key_figures(values), documents=documents(values),
                               taxpayer=full_name(values.get("intake", {})),
                               review_notes=[m for m in values.get("handoffs", []) if m["from_agent"] == "review"])

    @app.route("/sessions/<session_id>/hitl/<event_id>", methods=["GET", "POST"])
    def resolve(session_id: str, event_id: str):
        values = state_of(session_id)
        event = next((e for e in values.get("hitl", []) if e["event_id"] == event_id), None)
        if event is None:
            abort(404)
        if request.method == "GET" or event["status"] != "open":
            return render_template("resolve.html", session_id=session_id, s=values, event=event,
                                   decisions=DECISION_LABELS, form={})
        try:
            payload = resolution_from_form(request.form, event_id)
        except ValueError as error:
            flash(str(error), "error")
            return render_template("resolve.html", session_id=session_id, s=values, event=event,
                                   decisions=DECISION_LABELS, form=request.form), 400
        if not _waiting_on(graph, session_id, event_id):
            flash("This session isn't paused on that event right now. Refresh and try again.", "error")
            return redirect(url_for("session", session_id=session_id))
        graph.invoke(Command(resume=payload), _config(session_id))
        flash(f"Saved. {DECISION_DONE[payload['decision']]} by {payload['resolved_by']}.", "ok")
        return redirect(url_for("session", session_id=session_id))

    @app.get("/sessions/<session_id>/outputs/<kind>")
    def output(session_id: str, kind: str):
        location = (state_of(session_id).get("outputs") or {}).get(kind)
        if not location or not Path(location).is_file():
            abort(404)
        return send_file(Path(location).resolve())

    return app


def _config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def _waiting_on(graph, session_id: str, event_id: str) -> bool:
    """True when the graph is paused in human_review on an open event with this id."""
    snapshot = graph.get_state(_config(session_id))
    paused = any(i.value.get("type") == "hitl" for task in snapshot.tasks for i in task.interrupts)
    return paused and any(e["event_id"] == event_id for e in orchestrator.open_events(snapshot.values))


def group_sessions(rows: list[dict]) -> dict[str, list[dict]]:
    """Split session rows into the three dashboard groups."""
    groups: dict[str, list[dict]] = {key: [] for key, _ in GROUPS}
    for row in rows:
        if row["open_hitl"] or row["status"] == "awaiting_human":
            groups["needs_human"].append(row)
        elif row["status"] in FINISHED:
            groups["completed"].append(row)
        else:
            groups["in_progress"].append(row)
    return groups


def resolution_from_form(form, event_id: str) -> dict:
    """Turn the resolve form into a resume payload (contract section 7). Raises ValueError."""
    resolved_by = form.get("resolved_by", "").strip()
    if not resolved_by:
        raise ValueError("Add your name so the audit record shows who resolved this.")
    return {"event_id": event_id, "decision": validate_decision(form.get("decision", "")),
            "note": form.get("note", "").strip(), "resolved_by": resolved_by,
            "updates": parse_updates(form.get("updates", ""))}


def parse_updates(text: str) -> dict:
    """Read 'question_id = value' lines. Values are JSON when they parse, else plain text."""
    updates = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        qid, sep, raw = line.partition("=")
        qid, raw = qid.strip(), raw.strip()
        if not sep or qid not in BY_ID:
            raise ValueError(f"Line {number} of the answer updates should look like 'question_id = value' "
                             f"with a known question id.")
        try:
            updates[qid] = json.loads(raw)
        except json.JSONDecodeError:
            updates[qid] = raw
    return updates


def mask(value):
    """Mask every SSN-shaped number in a value, keeping the last four digits."""
    if isinstance(value, str):
        return _SSN.sub(lambda m: f"•••-••-{m.group(1)}", value)
    if isinstance(value, dict):
        return {key: mask(item) for key, item in value.items()}
    if isinstance(value, list):
        return [mask(item) for item in value]
    return value


def masked_answers(intake: dict) -> list[tuple[str, str | list[str]]]:
    """(topic, readable value) rows in question order. Sensitive answers show only their last four digits."""
    rows = []
    for qid, answer in intake.items():
        question = BY_ID.get(qid)
        value = mask(answer.get("value"))
        if question and question.sensitive and not isinstance(value, str):
            value = "•••-••-••••"
        rows.append((label(qid), readable(value, qid)))
    return rows


def plain(value) -> str:
    """Readable text for an answer value: Yes/No, a dash for nothing, JSON for structures."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value in (None, "", [], {}):
        return "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def label(qid: str) -> str:
    """'taxpayer_ssn' -> 'Taxpayer SSN', 'spouse_dob' -> 'Spouse date of birth'."""
    text = " ".join("date of birth" if word == "dob" else word for word in qid.split("_")).capitalize()
    return text.replace("ssn", "SSN")


def full_name(intake: dict) -> str:
    """'Mara Lindqvist', or just the first name, or '' before the name is given."""
    parts = [(intake.get(qid) or {}).get("value") for qid in ("taxpayer_first_name", "taxpayer_last_name")]
    return " ".join(str(part) for part in parts if part)


def reference(session_id: str) -> str:
    """A short reference staff can read out: 'eval_case_1-3d7db7' -> '3D7DB7'."""
    return session_id.rsplit("-", 1)[-1].upper()


def format_time(value: str) -> str:
    """'Oct 25, 2026, 9:48 PM UTC'."""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value or ""
    hour = moment.hour % 12 or 12
    return f"{moment:%b} {moment.day}, {moment:%Y}, {hour}:{moment:%M} {'AM' if moment.hour < 12 else 'PM'} UTC"

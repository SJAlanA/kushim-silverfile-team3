"""Taxpayer page: answer SilverFile's questions, upload a W-2, and get the draft once a volunteer releases it.

A separate app from the staff dashboard, on its own port, so the public side never shares
routes with the staff side:

    python run.py --taxpayer         # http://localhost:8001

Each return lives at a private link, /r/<session id>, where the id is 128 random bits. There is
no list of returns here and no login; whoever has the link can continue the return, so it is
treated like a password (no-referrer, no caching, no indexing). The form files are served only
after a volunteer has released the draft in the staff dashboard (src/release.py).

The page drives the same graph and SQLite checkpoint as the CLI: each answer is
`Command(resume=reply)`, exactly what `run.py --interactive` sends.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for
from langgraph.types import Command
from PIL import Image, UnidentifiedImageError

from src.agents.intake_questions import BY_ID, GLOSSARY, SUB_KINDS, SUB_TEXT
from src.dashboard import CONDITION_LABELS, DECISION_LABELS, label, reference
from src.dashboard.views import readable
from src.graph import build_graph
from src.memory import get_checkpointer
from src.release import is_released
from src.taxpayer import grader

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # per request
UPLOAD_TYPES = {"jpeg": ".jpg", "png": ".png"}
DOC_CHOICES = (("W2", "W-2 (wages from a job)"), ("SSA-1099", "SSA-1099 (Social Security benefits)"),
               ("1099-R", "1099-R (pension or IRA)"), ("1099-INT", "1099-INT (interest)"),
               ("1099-DIV", "1099-DIV (dividends)"))
FILING_STATUS_CHOICES = ("Single", "Married filing jointly", "Married filing separately", "Head of household",
                         "Qualifying surviving spouse")
# Questions answered yes or no first; any follow-up comes as its own question.
YES_NO_KINDS = ("yes_no", "who", "dependents", "other_income", "schedule_values")
_SESSION_ID = re.compile(r"^web-[0-9a-f]{32}$")


def create_app(db_path: str | None = None, upload_dir: str | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.getenv("TAXPAYER_SECRET") or secrets.token_hex(16)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    uploads = Path(upload_dir or os.getenv("UPLOAD_DIR", "uploads"))
    graph = build_graph(get_checkpointer(db_path))

    def config(session_id: str) -> dict:
        return {"configurable": {"thread_id": session_id}}

    def state_of(session_id: str) -> dict:
        if not _SESSION_ID.match(session_id):
            abort(404)
        values = graph.get_state(config(session_id)).values
        if not values:
            abort(404)
        return values

    def pending(session_id: str) -> dict | None:
        snapshot = graph.get_state(config(session_id))
        return next((i.value for task in snapshot.tasks for i in task.interrupts), None)

    @app.after_request
    def private(response):
        # The return's link is its key: keep it out of referrers, caches and search engines.
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.endpoint != "form":  # the generated form uses inline styles and script for its popovers
            response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
        return response

    @app.errorhandler(404)
    def not_found(_):
        return render_template("not_found.html"), 404

    @app.errorhandler(413)
    def too_large(_):
        return render_template("not_found.html", too_large=True), 413

    @app.get("/")
    def home():
        return render_template("home.html")

    @app.post("/start")
    def start():
        session_id = f"web-{secrets.token_hex(16)}"
        graph.invoke({"session_id": session_id}, config(session_id))
        return redirect(url_for("tax_return", session_id=session_id))

    @app.get("/r/<session_id>")
    def tax_return(session_id: str):
        values = state_of(session_id)
        payload = pending(session_id)
        if payload and payload.get("type") == "intake_question":
            return render_template("question.html", session_id=session_id, q=question_view(payload),
                                   said=said_since_last_reply(values.get("messages", []), payload["text"]),
                                   progress=progress(values), doc_choices=DOC_CHOICES,
                                   can_go_back=bool(values.get("intake")))
        return render_template("status.html", session_id=session_id, stage=stage(values, payload),
                               name=_first_name(values))

    @app.post("/r/<session_id>/answer")
    def answer(session_id: str):
        state_of(session_id)
        payload = pending(session_id)
        if not _is_current_question(payload, request.form):
            return redirect(url_for("tax_return", session_id=session_id))
        reply = request.form.get("reply", "").strip()
        if not reply:
            flash("Please type an answer, or choose “I'm not sure”.", "error")
            return redirect(url_for("tax_return", session_id=session_id))
        graph.invoke(Command(resume=reply), config(session_id))
        return redirect(url_for("tax_return", session_id=session_id))

    @app.post("/r/<session_id>/documents")
    def documents(session_id: str):
        state_of(session_id)
        payload = pending(session_id)
        if not _is_current_question(payload, request.form) or payload.get("question_id") != "documents":
            return redirect(url_for("tax_return", session_id=session_id))
        if request.form.get("none") == "yes":
            graph.invoke(Command(resume="none"), config(session_id))
            return redirect(url_for("tax_return", session_id=session_id))
        try:
            saved = save_uploads(uploads / session_id, request.form.getlist("doc_type"),
                                 request.files.getlist("file"))
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("tax_return", session_id=session_id))
        graph.invoke(Command(resume=json.dumps(saved)), config(session_id))
        return redirect(url_for("tax_return", session_id=session_id))

    # --- Grader's dashboard -----------------------------------------------------------------
    staff = os.getenv("STAFF_DASHBOARD_URL", "http://localhost:8000").rstrip("/")
    app.jinja_env.globals.update(grader_enabled=grader_enabled(), staff=staff,
                                 condition_label=lambda c: CONDITION_LABELS.get(c, c),
                                 decision_label=lambda d: DECISION_LABELS.get(d, d),
                                 answer_label=label, answer_value=lambda qid, v: readable(v, qid))
    app.jinja_env.filters["reference"] = reference
    app.jinja_env.filters["condition_label"] = app.jinja_env.globals["condition_label"]

    def case_or_404(case_id: str) -> dict:
        if not grader_enabled() or case_id not in grader.catalog():
            abort(404)
        return grader.catalog()[case_id]

    @app.get("/grader")
    def grader_home():
        if not grader_enabled():
            abort(404)
        cases = list(grader.catalog().values())
        return render_template("grader.html", demo=[c for c in cases if c["group"] == "demo"],
                               negative=[c for c in cases if c["group"] == "negative"])

    @app.post("/grader/cases/<case_id>")
    def grader_start(case_id: str):
        entry = case_or_404(case_id)
        session_id = f"web-{secrets.token_hex(16)}"
        graph.invoke({"session_id": session_id}, config(session_id))
        grader.replay(graph, session_id, entry["case"])
        return redirect(url_for("grader_run", case_id=case_id, session_id=session_id))

    @app.get("/grader/cases/<case_id>/<session_id>")
    def grader_run(case_id: str, session_id: str):
        entry, values = case_or_404(case_id), state_of(session_id)
        payload = pending(session_id)
        event = payload["event"] if payload and payload.get("type") == "hitl" else None
        return render_template("grader_run.html", entry=entry, session_id=session_id, s=values, event=event,
                               asking=payload if payload and payload.get("type") == "intake_question" else None,
                               hint=grader.expected_resolution(entry["case"], event["condition"]) if event else None,
                               compare=grader.comparison(entry["case"], values), released=is_released(values))

    @app.post("/grader/cases/<case_id>/<session_id>/continue")
    def grader_continue(case_id: str, session_id: str):
        entry = case_or_404(case_id)
        state_of(session_id)
        grader.replay(graph, session_id, entry["case"])
        return redirect(url_for("grader_run", case_id=case_id, session_id=session_id))

    @app.get("/r/<session_id>/form.<kind>")
    def form(session_id: str, kind: str):
        values = state_of(session_id)
        location = (values.get("outputs") or {}).get(kind)
        if kind not in ("pdf", "html") or not is_released(values) or not location or not Path(location).is_file():
            abort(404)
        return send_file(Path(location).resolve(), as_attachment=kind == "pdf",
                         download_name=f"Form-1040-SR-draft.{kind}")

    return app


def grader_enabled() -> bool:
    """The grader's dashboard is on unless GRADER_DASHBOARD=0. A real deployment would turn it off."""
    return os.getenv("GRADER_DASHBOARD", "1") != "0"


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def question_view(payload: dict) -> dict:
    """What the question page needs: the text and which kind of input to show."""
    qid, sub = payload["question_id"], payload.get("sub")
    kind = SUB_KINDS.get(sub, "text") if sub else BY_ID[qid].kind
    if qid == "documents" and not sub:
        widget = "upload"
    elif kind in YES_NO_KINDS:
        widget = "yes_no"
    elif kind == "filing_status":
        widget = "filing_status"
    elif kind == "date":
        widget = "date"
    else:
        widget = {"ssn": "ssn", "amount": "amount"}.get(kind, "text")
    note, explain, text = split_question(qid, sub, payload["text"])
    return {"question_id": qid, "sub": sub or "", "text": text, "note": note, "explain": explain, "widget": widget,
            "choices": FILING_STATUS_CHOICES if widget == "filing_status" else ()}


def split_question(qid: str, sub: str | None, full: str) -> tuple[str, str, str]:
    """Intake sends one string: [a note about the last reply] [a term explained] question.
    Split it so the page can show the note in a box, the explanation as a hint, and the question as the heading."""
    template = SUB_TEXT.get((qid, sub), "") if sub else BY_ID[qid].text
    pattern = ".+?".join(re.escape(part) for part in re.split(r"\{[a-z_]+\}", template))  # {name} etc. match anything
    match = re.search(f"({pattern})$", full) if template else None
    if not match:
        return "", "", full
    prefix, explain = full[:match.start()].strip(), []
    for sentence in GLOSSARY.values():
        if sentence in prefix:
            explain.append(sentence)
            prefix = prefix.replace(sentence, "").strip()
    return prefix, " ".join(explain), match.group(1)


def said_since_last_reply(messages: list, question_text: str) -> list[str]:
    """What SilverFile said after the taxpayer's last reply, minus the question itself
    (for example "Thank you." or a gentle note about the last answer)."""
    said = []
    for message in reversed(messages):
        if getattr(message, "type", "") == "human":
            break
        text = str(getattr(message, "content", "")).strip()
        if text.endswith(question_text):
            text = text[: -len(question_text)].strip()
        if text:
            said.insert(0, text)
    return said


def progress(values: dict) -> int:
    """Rough share of the questions answered, for the progress bar (0-100)."""
    from src.agents.intake_questions import QUESTIONS

    answered = len(values.get("intake", {}))
    return min(95, round(100 * answered / max(len(QUESTIONS), 1)))


def stage(values: dict, payload: dict | None) -> str:
    """Which status page to show. Staff-facing reasons are never shown to the taxpayer."""
    if is_released(values):
        return "released"
    if values.get("status") == "escalated":
        return "volunteer_will_contact"
    if payload and payload.get("type") == "hitl":
        return "volunteer_checking"
    if values.get("status") == "complete":
        return "awaiting_release"
    return "working"


def save_uploads(folder: Path, doc_types: list[str], files: list) -> list[dict]:
    """Save each uploaded photo after checking it really is a JPEG or PNG. Returns document refs."""
    allowed = {code for code, _ in DOC_CHOICES}
    pairs = [(t, f) for t, f in zip(doc_types, files) if f and f.filename]
    if not pairs:
        raise ValueError("Please choose at least one photo, or tick “I don't have any tax forms”.")
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for doc_type, upload in pairs:
        if doc_type not in allowed:
            raise ValueError("Please choose which form each photo shows.")
        try:
            image = Image.open(upload.stream)
            image.verify()
        except (UnidentifiedImageError, OSError, SyntaxError):
            raise ValueError("One of the files isn't a photo SilverFile can read. Please send a JPG or PNG photo.")
        suffix = UPLOAD_TYPES.get((image.format or "").lower())
        if not suffix:
            raise ValueError("Please send each form as a JPG or PNG photo.")
        upload.stream.seek(0)
        path = folder / f"{secrets.token_hex(8)}{suffix}"  # never the uploaded file name
        upload.save(path)
        saved.append({"doc_type": doc_type, "path": str(path)})
    return saved


def _is_current_question(payload: dict | None, form) -> bool:
    """Ignore a stale or repeated submit (for example the back button) instead of answering the wrong question."""
    return bool(payload and payload.get("type") == "intake_question"
                and form.get("question_id") == payload["question_id"]
                and form.get("sub", "") == (payload.get("sub") or ""))


def _first_name(values: dict) -> str:
    answer = values.get("intake", {}).get("taxpayer_first_name")
    return str(answer["value"]) if answer else ""

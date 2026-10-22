"""Intake agent.

Responsibility: The only agent that talks to the taxpayer.
Constraint: Must not perform arithmetic or interpret tax rules.
Owner: Agent memory

The node runs in two phases so a question is generated once and survives a restart:
  1. no pending question -> pick the next one, phrase it, store it in intake_meta["pending"];
  2. pending question    -> interrupt() with it, then record the reply.
Answers are only recorded; Eligibility decides what they mean.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt

from src import llm
from src.agents import intake_parsing as parse
from src.agents.intake_questions import (BY_ID, GLOSSARY, ORDINALS, SCHEDULE_FIELDS, SUB_KINDS, SUB_TEXT,
                                         next_question_id)
from src.guardrails import classify_utterance
from src.hitl import raise_hitl
from src.memory import apply_context_strategy, current_item, estimate_tokens, log_tokens, update_decisions_summary
from src.security import redact_pii, validate_ssn

AGENT = "intake"
MAX_ATTEMPTS = 2
NAVIGATION_MAX_WORDS = 12

SYSTEM_PROMPT = (
    "You are the intake assistant for SilverFile, a tax-form preparation aid for older adults. "
    "Ask exactly one short question in plain, warm language. Never give tax advice, never do arithmetic, "
    "never ask for anything the question doesn't need, and keep any explanation that comes with the question."
)
VOLUNTEERED_SSN_NOTICE = "For your safety I didn't save that number. I'll ask for it only where the form needs it."
UNCERTAIN_REPLY = "I'm not sure about this one. Let me flag it for a volunteer, and we'll keep going."
ADVICE_REPLY = ("That's a fair question, but it's tax advice, and SilverFile only helps fill in the form. "
                "I've noted your question for the volunteer preparer.")
CONCERN_REPLY = "Thank you for telling me. I'm going to ask a volunteer to look at this before we go on."
HELPER_REPLY = "Thank you. From here on, when I say \"you\", I mean the person this return is for."
LOSS_REPLY = "I'm very sorry for your loss. A volunteer will help with the next part, so I've let them know."
RETRY_HINTS = {
    "yes_no": "A simple yes or no is fine.",
    "name": "Just the name is fine, using letters only.",
    "full_name": "A first and last name is fine, using letters only.",
    "date": "You can write it like March 3, 1955 or 03/03/1955.",
    "amount": "Please give one total in whole dollars, for example 1,200, or say none.",
    "filing_status": "If you're married, you can file together with your spouse (jointly) or on your own "
                     "(separately).",
    "documents": "You can list the form names, for example W-2 and SSA-1099, or say none.",
    "refund": "For example, it could be your own checking account, a joint account, or a paper check.",
    "text": "A short answer is fine.",
    "path": "The file name or folder is fine.",
}
_COMPOUND = re.compile(r"\b(and|or) (what|when|how|who|where|which|why)\b")
_SSN_IN_TEXT = "[SSN redacted]"


@dataclass
class Step:
    """Outcome of reading one reply."""
    done: bool = False                     # the question has a final value
    value: Any = None
    retry: str | None = None               # plain-language hint; the same question is asked again
    escalate: tuple[str, str] | None = None
    ack: str | None = None
    extra: dict = field(default_factory=dict)


def is_single_question(text: str) -> bool:
    return text.count("?") == 1 and not _COMPOUND.search(text.lower())


def run(state: dict) -> dict:
    pending = state.get("intake_meta", {}).get("pending")
    if pending is None:
        return ask(state)
    reply = interrupt({"type": "intake_question", "question_id": pending["question_id"],
                       "text": pending["text"], "sub": pending.get("sub")})
    return answer(state, str(reply))


def ask(state: dict) -> dict:
    """Phase 1: choose and phrase the next question, or mark intake complete."""
    meta = copy.deepcopy(state.get("intake_meta", {}))
    qid = next_question_id(state.get("intake", {}), meta)
    if qid is None:
        summary = update_decisions_summary(state.get("decisions_summary", ""), current_item(state), state)
        return {"intake_complete": True, "intake_meta": meta, "decisions_summary": summary}
    sub = meta.get("drafts", {}).get(qid, {}).get("next_sub")
    base = _with_glossary(qid, sub, _question_text(qid, sub, meta), meta)
    text, token_entry = _phrase(state, base)
    meta["pending"] = {"question_id": qid, "sub": sub, "text": text, "base": text}
    return {"intake_meta": meta, "intake_complete": False, "status": "intake",
            "token_log": [token_entry], "messages": [_ai(state, text)]}


def answer(state: dict, reply: str) -> dict:
    """Phase 2: read the reply to the pending question and record it."""
    meta = copy.deepcopy(state.get("intake_meta", {}))
    pending = meta["pending"]
    qid, sub = pending["question_id"], pending.get("sub")
    kind = SUB_KINDS[sub] if sub else BY_ID[qid].kind
    redacted = redact_pii(reply)
    update: dict = {"messages": [_human(state, redacted)], "status": "intake"}
    volunteered = kind != "ssn" and parse.parse_json(reply) is None and _SSN_IN_TEXT in redacted
    notes = [VOLUNTEERED_SSN_NOTICE] if volunteered else []

    navigation = parse.parse_navigation(reply) if len(reply.split()) <= NAVIGATION_MAX_WORDS else None
    if navigation:
        return _navigate(state, meta, navigation, update, notes)
    if kind != "refund" and parse.is_uncertain(reply):
        return _flag_uncertain(state, meta, qid, update, notes)
    concern = _classify(redacted, qid)
    if concern:
        return _escalate_concern(state, meta, concern, redacted, qid, update, notes)

    step = _read(qid, sub, kind, reply, redacted, meta)
    if step.retry:
        return _retry(state, meta, qid, sub, step.retry, update, notes)
    if not step.done:
        meta["pending"] = None
        meta.setdefault("attempts", {}).pop(_attempt_key(qid, sub), None)
        return _finish(state, update, meta, notes)
    return _record(state, meta, qid, step, update, notes)


def _question_text(qid: str, sub: str | None, meta: dict) -> str:
    if not sub:
        return BY_ID[qid].text
    draft = meta["drafts"][qid]
    fields = {}
    if qid == "dependents":
        fields = {"ordinal": ORDINALS[min(len(draft["items"]), len(ORDINALS) - 1)],
                  "name": draft.get("current", {}).get("first_name", "they")}
    elif qid == "documents":
        fields = {"doc_type": _doc_label(draft["items"][draft["index"]]["doc_type"])}
    return SUB_TEXT[(qid, sub)].format(**fields)


def _with_glossary(qid: str, sub: str | None, text: str, meta: dict) -> str:
    """Prefix a one-line explanation the first time a term comes up in this session."""
    glossed = meta.setdefault("glossed", [])
    new_terms = [] if sub else [term for term in BY_ID[qid].glossary if term not in glossed]
    glossed.extend(new_terms)
    return " ".join([GLOSSARY[term] for term in new_terms] + [text])


def _phrase(state: dict, template: str) -> tuple[str, dict]:
    """Optionally let the model phrase the question; always log the input tokens it would see."""
    enabled = state.get("context_strategy", True)
    prompt = ([SystemMessage(content=SYSTEM_PROMPT)]
              + apply_context_strategy(state.get("messages", []), state, enabled)
              + [HumanMessage(content=f"Ask this next, as one plain-language question: {template}")])
    item = current_item(state)
    model = llm.get_chat_model()
    if model is not None:
        try:
            response = model.invoke(prompt)
            text = str(response.content).strip()
            tokens = log_tokens(AGENT, item, response.usage_metadata["input_tokens"], enabled)
            return (text if is_single_question(text) else template), tokens
        except Exception:  # noqa: BLE001 - any model failure falls back to the template
            pass
    rendered = "\n".join(f"{message.type}: {message.content}" for message in prompt)
    return template, log_tokens(AGENT, item, estimate_tokens(rendered), enabled, estimated=True)


def _classify(redacted: str, qid: str) -> str | None:
    concern = classify_utterance(redacted, qid)
    if concern == "out_of_scope" and qid in ("other_income", "documents"):
        return None  # recorded as an answer; Eligibility decides scope
    return concern


def _read(qid: str, sub: str | None, kind: str, reply: str, redacted: str, meta: dict) -> Step:
    flows = {"who_is_answering": _read_who, "dependents": _read_dependents, "documents": _read_documents,
             "other_income": _read_other_income, "schedule_values": _read_schedule_values}
    if qid in flows:
        return flows[qid](sub, kind, reply, redacted, meta)
    if kind == "refund":
        return _read_refund(redacted)
    if kind == "filing_status":
        status, widow = parse.parse_filing_status(reply)
        meta["widow_mentioned"] = meta.get("widow_mentioned", False) or widow
        return Step(done=True, value=status) if status else Step(retry=RETRY_HINTS[kind])
    return _read_simple(kind, reply)


def _read_simple(kind: str, reply: str) -> Step:
    if kind == "ssn":
        ok, result = validate_ssn(reply)
        if ok:
            return Step(done=True, value=result)
        unsafe = any(character.isdigit() for character in reply)
        return Step(retry=f"{result} {'For your safety I did not save that number.' if unsafe else ''}".strip())
    parsers = {"yes_no": parse.parse_yes_no, "name": parse.parse_name, "full_name": parse.parse_name,
               "date": parse.parse_date, "amount": parse.parse_amount, "text": _non_empty, "path": _non_empty}
    value = parsers[kind](reply)
    return Step(done=True, value=value) if value is not None else Step(retry=RETRY_HINTS[kind])


def _non_empty(reply: str) -> str | None:
    text = redact_pii(reply).strip().strip("\"'")
    return text or None


def _read_who(sub: str | None, kind: str, reply: str, redacted: str, meta: dict) -> Step:
    structured = parse.parse_json(reply)
    if not sub and isinstance(structured, dict):
        value = {"is_taxpayer": bool(structured.get("is_taxpayer", True)),
                 "relationship": structured.get("relationship"), "has_authority": structured.get("has_authority")}
        return _who_done(value)
    draft = meta.setdefault("drafts", {}).setdefault("who_is_answering", {"value": {"is_taxpayer": False}})
    if not sub:
        is_taxpayer = parse.parse_yes_no(reply)
        if is_taxpayer is None:
            return Step(retry=RETRY_HINTS["yes_no"])
        if is_taxpayer:
            meta["drafts"].pop("who_is_answering")
            return Step(done=True, value={"is_taxpayer": True, "relationship": None, "has_authority": None})
        if len(reply.split()) > 1:
            draft["value"]["relationship"] = redacted
    elif sub == "relationship":
        draft["value"]["relationship"] = redacted
    elif sub == "authority":
        has_authority = parse.parse_yes_no(reply)
        if has_authority is None:
            return Step(retry=RETRY_HINTS["yes_no"])
        draft["value"]["has_authority"] = has_authority
        return _who_done(meta["drafts"].pop("who_is_answering")["value"])
    if parse.states_authority(reply):
        draft["value"]["has_authority"] = True
        return _who_done(meta["drafts"].pop("who_is_answering")["value"])
    draft["next_sub"] = "authority" if draft["value"].get("relationship") else "relationship"
    return Step()


def _who_done(value: dict) -> Step:
    if value["is_taxpayer"]:
        return Step(done=True, value=value)
    if value.get("has_authority"):
        return Step(done=True, value=value, ack=HELPER_REPLY)
    return Step(done=True, value=value, ack=CONCERN_REPLY,
                escalate=("EXPLOITATION", "Someone other than the taxpayer is answering without stated "
                                          "legal authority."))


def _read_dependents(sub: str | None, kind: str, reply: str, redacted: str, meta: dict) -> Step:
    structured = parse.parse_json(reply)
    if not sub and isinstance(structured, list):
        return _dependents_from_list(structured)
    drafts = meta.setdefault("drafts", {})
    if not sub:
        has_dependents = parse.parse_yes_no(reply)
        if has_dependents is None:
            return Step(retry=RETRY_HINTS["yes_no"])
        if not has_dependents:
            return Step(done=True, value=[])
        drafts["dependents"] = {"items": [], "current": {}, "next_sub": "name"}
        return Step()
    draft = drafts["dependents"]
    if sub == "name":
        name = parse.parse_name(reply)
        if not name:
            return Step(retry=RETRY_HINTS["full_name"])
        first, _, last = name.partition(" ")
        draft["current"] = {"first_name": first, "last_name": last}
        draft["next_sub"] = "relationship"
    elif sub == "relationship":
        draft["current"]["relationship"] = redacted.strip()
        draft["next_sub"] = "ssn"
    elif sub == "ssn":
        ok, result = validate_ssn(reply)
        if not ok:
            return Step(retry=result)
        draft["items"].append({**draft["current"], "ssn": result})
        draft["current"], draft["next_sub"] = {}, "more"
    elif sub == "more":
        more = parse.parse_yes_no(reply)
        if more is None:
            return Step(retry=RETRY_HINTS["yes_no"])
        if not more:
            return Step(done=True, value=drafts.pop("dependents")["items"])
        draft["next_sub"] = "name"
    return Step()


def _dependents_from_list(items: list) -> Step:
    dependents = []
    for item in items:
        ok, result = validate_ssn(str(item.get("ssn", "")))
        if not ok:
            return Step(retry=f"One of the dependent numbers can't be used. {result}")
        dependents.append({"first_name": str(item.get("first_name", "")), "last_name": str(item.get("last_name", "")),
                           "relationship": str(item.get("relationship", "")), "ssn": result})
    return Step(done=True, value=dependents)


def _read_documents(sub: str | None, kind: str, reply: str, redacted: str, meta: dict) -> Step:
    structured = parse.parse_json(reply)
    if not sub and isinstance(structured, list):
        return _documents_done(_assign_doc_ids(structured))
    drafts = meta.setdefault("drafts", {})
    if not sub:
        doc_types = parse.find_doc_types(reply)
        if not doc_types:
            return Step(done=True, value=[]) if parse.parse_yes_no(reply) is False else Step(
                retry=RETRY_HINTS["documents"])
        drafts["documents"] = {"items": _assign_doc_ids([{"doc_type": t} for t in doc_types]),
                               "index": 0, "next_sub": "path"}
        return Step()
    draft = drafts["documents"]
    path = _non_empty(reply)
    if not path:
        return Step(retry=RETRY_HINTS["path"])
    draft["items"][draft["index"]]["path"] = path
    draft["index"] += 1
    if draft["index"] < len(draft["items"]):
        return Step()
    return _documents_done(drafts.pop("documents")["items"])


def _assign_doc_ids(items: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    documents = []
    for item in items:
        doc_type = str(item.get("doc_type", "")).upper().replace("W-2", "W2")
        prefix = parse.DOC_ID_PREFIX.get(doc_type, doc_type.lower().replace("-", ""))
        counts[prefix] = counts.get(prefix, 0) + 1
        documents.append({"doc_id": str(item.get("doc_id") or f"{prefix}_{counts[prefix]}"),
                          "doc_type": doc_type, "path": str(item.get("path", ""))})
    return documents


def _documents_done(documents: list[dict]) -> Step:
    return Step(done=True, value=documents, extra={"documents": documents})


def _read_other_income(sub: str | None, kind: str, reply: str, redacted: str, meta: dict) -> Step:
    structured = parse.parse_json(reply)
    if not sub and isinstance(structured, dict):
        return Step(done=True, value={"has_other_income": bool(structured.get("has_other_income")),
                                      "description": redact_pii(str(structured.get("description", "")))})
    if sub == "describe":
        meta["drafts"].pop("other_income")
        return Step(done=True, value={"has_other_income": True, "description": redacted.strip()})
    has_other = parse.parse_yes_no(reply)
    if has_other is None:
        return Step(retry=RETRY_HINTS["yes_no"])
    if not has_other:
        return Step(done=True, value={"has_other_income": False, "description": ""})
    if len(reply.split()) > 1:
        return Step(done=True, value={"has_other_income": True, "description": redacted.strip()})
    meta.setdefault("drafts", {})["other_income"] = {"next_sub": "describe"}
    return Step()


def _read_schedule_values(sub: str | None, kind: str, reply: str, redacted: str, meta: dict) -> Step:
    structured = parse.parse_json(reply)
    if not sub and isinstance(structured, dict):
        values = {key: parse.parse_amount(str(structured.get(key, 0))) for key in SCHEDULE_FIELDS}
        if any(value is None for value in values.values()):
            return Step(retry=RETRY_HINTS["amount"])
        return Step(done=True, value=values)
    drafts = meta.setdefault("drafts", {})
    if not sub:
        has_values = parse.parse_yes_no(reply)
        if has_values is None:
            return Step(retry=RETRY_HINTS["yes_no"])
        if not has_values:
            return Step(done=True, value=dict.fromkeys(SCHEDULE_FIELDS, 0))
        drafts["schedule_values"] = {"values": {}, "next_sub": SCHEDULE_FIELDS[0]}
        return Step()
    amount = parse.parse_amount(reply)
    if amount is None:
        return Step(retry=RETRY_HINTS["amount"])
    draft = drafts["schedule_values"]
    draft["values"][sub] = amount
    remaining = [key for key in SCHEDULE_FIELDS if key not in draft["values"]]
    if remaining:
        draft["next_sub"] = remaining[0]
        return Step()
    return Step(done=True, value=drafts.pop("schedule_values")["values"])


def _read_refund(redacted: str) -> Step:
    described = parse.refund_is_described(redacted)
    if described is None:
        return Step(retry=RETRY_HINTS["refund"])
    value = {"description": redacted.strip(), "described": described}
    if described:
        return Step(done=True, value=value)
    return Step(done=True, value=value, ack=CONCERN_REPLY,
                escalate=("EXPLOITATION", "The taxpayer could not describe the account a refund would go to."))


def _record(state: dict, meta: dict, qid: str, step: Step, update: dict, notes: list[str]) -> dict:
    """Store a final answer, keeping any earlier value in history, then raise any concern."""
    intake = state.get("intake", {})
    old = intake.get(qid)
    raw_text = _SSN_IN_TEXT if BY_ID[qid].sensitive else update["messages"][0].content
    history = (old["history"] + [{"value": old["value"], "raw_text": old["raw_text"],
                                  "answered_at": old["answered_at"]}]) if old else []
    update["intake"] = {qid: {"value": step.value, "raw_text": raw_text, "answered_at": _now(), "history": history}}
    update.update(step.extra)
    if old and old["value"] != step.value:
        notes.append(f"Thanks, I've updated {BY_ID[qid].topic}.")
    if step.ack:
        notes.append(step.ack)
    _clear_question(meta, qid)
    meta.setdefault("answered_order", []).append(qid)
    if qid == "spouse_died_in_2025" and step.value:
        notes.append(LOSS_REPLY)
        step.escalate = ("FILING_STATUS_AMBIGUITY", "The taxpayer's spouse died during 2025, so the right "
                                                    "filing status (joint, surviving spouse or single) needs a volunteer.")
    if step.escalate:
        update.update(raise_hitl(step.escalate[0], step.escalate[1], AGENT, {"question_id": qid}))
    return _finish(state, update, meta, notes)


def _retry(state: dict, meta: dict, qid: str, sub: str | None, hint: str, update: dict, notes: list[str]) -> dict:
    attempts = meta.setdefault("attempts", {})
    key = _attempt_key(qid, sub)
    attempts[key] = attempts.get(key, 0) + 1
    if attempts[key] >= MAX_ATTEMPTS:
        return _flag_uncertain(state, meta, qid, update, notes)
    pending = meta["pending"]
    pending["text"] = f"Sorry, I didn't quite catch that. {hint} {pending['base']}"
    update["messages"].append(_ai(state, pending["text"]))
    return _finish(state, update, meta, notes)


def _flag_uncertain(state: dict, meta: dict, qid: str, update: dict, notes: list[str]) -> dict:
    _clear_question(meta, qid)
    meta.setdefault("flagged", []).append(qid)
    notes.append(UNCERTAIN_REPLY)
    update.update(raise_hitl("UNCERTAIN", f"The taxpayer wasn't sure how to answer about {BY_ID[qid].topic}.",
                             AGENT, {"question_id": qid}))
    return _finish(state, update, meta, notes)


def _escalate_concern(state: dict, meta: dict, concern: str, redacted: str, qid: str, update: dict,
                      notes: list[str]) -> dict:
    """Advice, exploitation or out-of-scope: stop and hand to a human; the question is asked again later."""
    condition = {"advice": "TAX_ADVICE", "out_of_scope": "OUT_OF_SCOPE"}.get(concern, "EXPLOITATION")
    meta["pending"] = None
    notes.append(ADVICE_REPLY if condition == "TAX_ADVICE" else CONCERN_REPLY)
    reason = {"TAX_ADVICE": "The taxpayer asked for tax advice, which SilverFile can't give.",
              "OUT_OF_SCOPE": "The taxpayer mentioned something outside SilverFile's scope.",
              "EXPLOITATION": "Something the taxpayer said may point to outside pressure or exploitation."}[condition]
    update.update(raise_hitl(condition, reason, AGENT, {"question_id": qid, "taxpayer_said": redacted}))
    return _finish(state, update, meta, notes)


def _navigate(state: dict, meta: dict, navigation: tuple[str, str | None], update: dict, notes: list[str]) -> dict:
    """'Go back' or 'change my answer': re-ask an earlier question without losing anything."""
    current = meta["pending"]["question_id"]
    answered = [qid for qid in meta.get("answered_order", []) if qid != current]
    target = navigation[1] or (answered[-1] if answered else None)
    if target is None or target not in BY_ID:
        notes.append("We're at the first question, so there's nothing before this one.")
        update["messages"].append(_ai(state, " ".join(notes + [meta["pending"]["text"]])))
        return {**update, "intake_meta": meta}
    meta.setdefault("drafts", {}).pop(current, None)
    meta.get("drafts", {}).pop(target, None)
    meta["pending"], meta["force"] = None, target
    notes.append(f"Of course. Let's go back to {BY_ID[target].topic}.")
    return _finish(state, update, meta, notes)


def _clear_question(meta: dict, qid: str) -> None:
    meta["pending"] = None
    if meta.get("force") == qid:
        meta["force"] = None
    meta.get("drafts", {}).pop(qid, None)
    meta["attempts"] = {key: count for key, count in meta.get("attempts", {}).items()
                        if not key.startswith(qid)}
    meta["flagged"] = [flagged for flagged in meta.get("flagged", []) if flagged != qid]


def _finish(state: dict, update: dict, meta: dict, notes: list[str]) -> dict:
    if notes:
        update["messages"].insert(1, _ai(state, " ".join(notes)))
    update["intake_meta"] = meta
    return update


def _attempt_key(qid: str, sub: str | None) -> str:
    return f"{qid}.{sub}" if sub else qid


def _doc_label(doc_type: str) -> str:
    return "W-2" if doc_type == "W2" else doc_type


def _ai(state: dict, text: str) -> AIMessage:
    return AIMessage(content=text, additional_kwargs={"item": current_item(state)})


def _human(state: dict, text: str) -> HumanMessage:
    return HumanMessage(content=text, additional_kwargs={"item": current_item(state)})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def case_reply(case: dict, payload: dict) -> str:
    """Answer an intake question from an eval/batch case file (contract section 13)."""
    qid = payload["question_id"]
    value = case.get("documents") if qid == "documents" and "documents" in case else case.get("answers", {}).get(qid)
    if value is None:
        return "I'm not sure"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)

"""Orchestrator agent.

Responsibility: Decides which agent acts next; owns termination.
Constraint: May not extract, compute, or converse with the user.
Owner: Task division and management

The orchestrator reads state and returns a routing decision. It never looks at a fixed
sequence: every hop is chosen from what the state says is missing, stale or flagged, in this
priority order (first match wins):

     1. an open HITL event                      -> human_review
     2. the loop guard trips                    -> raise LOOP_GUARD, human_review
     3. an agent was sent work back (`rerun`)   -> that agent
     4. intake not complete                     -> intake
     5. a document has never been read          -> extract_dispatch
     6. Eligibility hasn't seen current inputs  -> eligibility
     7. no rules, or out of scope               -> raise UNCERTAIN / OUT_OF_SCOPE (once)
     8. Computation hasn't seen current inputs  -> computation
     9. review missing, failed or stale         -> review, or REVIEW_LIMIT at the bound
    10. review passed on the current lines      -> finalize

"Hasn't seen current inputs" is decided with fingerprints: when the orchestrator dispatches an
agent it records a hash of exactly the state that agent reads (`routing.inputs_seen`). If the
hash is the same next time, the agent's output is still current and it is not run again. This
is what makes reruns cheap after a human edits an answer: only the agents downstream of the
change run again, in dependency order, so Computation can never run ahead of Extraction or
Eligibility.

Contract addition: `routing` (a plain dict written only by this agent and human_review) holds
`inputs_seen`, `handoff_cursor` and the loop guard's `trail`.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from src.hitl import raise_hitl

AGENT = "orchestrator"
WORKERS = ("intake", "extract_dispatch", "eligibility", "computation", "review")
# A send_back to an extraction sub-node means "read those documents again".
SEND_BACK_TARGETS = {**{name: name for name in WORKERS}, "extract_one": "extract_dispatch",
                     "extract_aggregate": "extract_dispatch"}
STATUS_FOR = {"intake": "intake", "extract_dispatch": "extracting", "eligibility": "rules",
              "computation": "computing", "review": "review", "human_review": "awaiting_human"}
LOOP_REPEATS = 3            # a pattern must repeat this many times in a row to count as a loop
LOOP_PATTERN_SIZES = (2, 3)  # A,B,A,B,A,B and A,B,C,A,B,C,A,B,C


def max_review_iterations() -> int:
    return int(os.getenv("MAX_REVIEW_ITERATIONS", "3"))


def max_hops() -> int:
    return int(os.getenv("MAX_HOPS", "60"))


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

def run(state: dict) -> dict:
    """Choose the next node. Returns `next_agent`, `route_history` and routing bookkeeping."""
    routing = _routing(state)
    rerun = list(state.get("rerun", [])) + new_send_backs(state.get("handoffs", []), routing["handoff_cursor"])
    next_agent, event_update = decide(state, rerun, routing)

    if rerun and next_agent == rerun[0]:
        rerun = rerun[1:]
    routing["handoff_cursor"] = len(state.get("handoffs", []))
    routing["trail"] = routing["trail"] + [[next_agent, progress(state)]]
    if next_agent in INPUTS:
        routing["inputs_seen"] = {**routing["inputs_seen"], next_agent: INPUTS[next_agent](state)}

    update: dict = {"next_agent": next_agent, "route_history": [next_agent], "routing": routing, "rerun": rerun}
    if next_agent in STATUS_FOR:
        update["status"] = STATUS_FOR[next_agent]
    update.update(event_update)
    return update


def route(state: dict) -> str:
    """Conditional-edge function: the graph follows whatever the orchestrator wrote."""
    return state["next_agent"]


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

def decide(state: dict, rerun: list[str], routing: dict) -> tuple[str, dict]:
    """Return (next node, extra state update). The update carries a HITL event when one is raised."""
    if open_events(state):
        return "human_review", {}
    loop = detect_loop(routing["trail"], progress(state))
    if loop:
        return "human_review", raise_hitl("LOOP_GUARD", loop["reason"], AGENT, loop["details"])
    if rerun:
        return rerun[0], {}
    if not state.get("intake_complete"):
        return "intake", {}
    if unread_documents(state):
        return "extract_dispatch", {}
    seen = routing["inputs_seen"]
    if seen.get("eligibility") != eligibility_inputs(state):
        return "eligibility", {}

    rules = state.get("rules")
    if not rules:
        event = _raise_once(state, "UNCERTAIN", "The eligibility check finished without producing any rules, "
                                                "so the return can't be computed safely.", eligibility_inputs(state))
        if event:
            return "human_review", event
    elif rules.get("in_scope") is False and not any(event["condition"] == "OUT_OF_SCOPE"
                                                    for event in state.get("hitl", [])):
        # Defensive: Eligibility normally raises this itself. If a volunteer already saw any
        # out-of-scope event and chose to continue, that decision stands.
        return "human_review", raise_hitl("OUT_OF_SCOPE", "The eligibility check found something on this "
                                                          "return that SilverFile doesn't handle.", AGENT)

    if seen.get("computation") != computation_inputs(state):
        return "computation", {}
    return _review_step(state, seen)


def _review_step(state: dict, seen: dict) -> tuple[str, dict]:
    review = state.get("review") or {}
    current = bool(review) and seen.get("review") == review_inputs(state)
    if current and review.get("passed"):
        return "finalize", {}
    if state.get("review_iterations", 0) < max_review_iterations():
        return "review", {}
    if _resolved(state, "REVIEW_LIMIT", review_inputs(state), decision="continue"):
        return "finalize", {}
    event = _raise_once(state, "REVIEW_LIMIT",
                        f"Review still found problems after {max_review_iterations()} rounds, "
                        "so a volunteer needs to look at the return.",
                        review_inputs(state), {"issues": review.get("issues", [])})
    return "human_review", event or {}


def open_events(state: dict) -> list[dict]:
    return [event for event in state.get("hitl", []) if event["status"] == "open"]


def unread_documents(state: dict) -> list[str]:
    """Documents with no record at all. Failed reads are retried only when extraction asks (send_back)."""
    extracted = state.get("extracted", {})
    return [doc["doc_id"] for doc in state.get("documents", []) if doc["doc_id"] not in extracted]


def new_send_backs(handoffs: list[dict], cursor: int) -> list[str]:
    """Agents named by send_back messages the orchestrator hasn't acted on yet, oldest first, no repeats."""
    targets: list[str] = []
    for message in handoffs[cursor:]:
        target = SEND_BACK_TARGETS.get(message["to_agent"])
        if message["kind"] == "send_back" and target and target not in targets:
            targets.append(target)
    return targets


def _raise_once(state: dict, condition: str, reason: str, fingerprint: str, details: dict | None = None) -> dict:
    """Raise `condition` unless it was already raised for these exact inputs."""
    if any(event["condition"] == condition and event.get("details", {}).get("fingerprint") == fingerprint
           for event in state.get("hitl", [])):
        return {}
    return raise_hitl(condition, reason, AGENT, {**(details or {}), "fingerprint": fingerprint})


def _resolved(state: dict, condition: str, fingerprint: str, decision: str) -> bool:
    return any(event["condition"] == condition and event["status"] == "resolved"
               and event.get("details", {}).get("fingerprint") == fingerprint
               and (event.get("resolution") or {}).get("decision") == decision
               for event in state.get("hitl", []))


# ---------------------------------------------------------------------------
# Loop guard
# ---------------------------------------------------------------------------

def detect_loop(trail: list[list[str]], current_progress: str) -> dict | None:
    """Spot a pathological handoff cycle in the hops since a human last looked.

    Two checks, both on `trail` (one [agent, progress hash at dispatch] entry per hop):
    - the last 2- or 3-agent pattern repeated LOOP_REPEATS times in a row, and the progress
      hash never changed across those hops (agents passing work around, nothing new produced);
    - more than MAX_HOPS hops to work agents. Intake hops don't count: each one asks or
      records a single question, and Intake bounds itself with its own retry limit.
    """
    for size in LOOP_PATTERN_SIZES:
        window = trail[-size * LOOP_REPEATS:]
        agents = [agent for agent, _ in window]
        if len(window) == size * LOOP_REPEATS and agents == agents[:size] * LOOP_REPEATS \
                and all(hashed == current_progress for _, hashed in window):
            pattern = " -> ".join(agents[:size])
            return {"reason": f"The agents passed the same work around ({pattern}) {LOOP_REPEATS} times "
                              "without changing anything, so routing has stopped.",
                    "details": {"pattern": agents[:size], "repeats": LOOP_REPEATS}}
    hops = sum(1 for agent, _ in trail if agent != "intake")
    if hops > max_hops():
        return {"reason": f"The return has taken {hops} steps without a person looking at it, "
                          "which is more than expected, so routing has stopped.",
                "details": {"hops": hops, "max_hops": max_hops()}}
    return None


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------

def _digest(*parts: Any) -> str:
    text = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def eligibility_inputs(state: dict) -> str:
    """What Eligibility reads: answer values (not timestamps), the document list and the readings."""
    answers = {qid: answer.get("value") for qid, answer in state.get("intake", {}).items()}
    documents = [[doc["doc_id"], doc["doc_type"], doc.get("path", "")] for doc in state.get("documents", [])]
    readings = {doc_id: [record.get("doc_type"), record.get("fields"), record.get("extractor")]
                for doc_id, record in state.get("extracted", {}).items()}
    return _digest(answers, documents, readings)


def computation_inputs(state: dict) -> str:
    return _digest(eligibility_inputs(state), state.get("rules"))


def review_inputs(state: dict) -> str:
    """Line values only: a recomputation that lands on the same numbers is not a change."""
    values = {key: line.get("value") for key, line in state.get("lines", {}).items()}
    return _digest(computation_inputs(state), values)


def progress(state: dict) -> str:
    """Everything an agent can produce. Handoffs, history, timestamps and token logs are excluded,
    so passing messages back and forth without new work keeps this hash the same."""
    review = state.get("review") or {}
    events = [[event["event_id"], event["status"]] for event in state.get("hitl", [])]
    return _digest(review_inputs(state), state.get("intake_complete"), state.get("intake_meta"),
                   review.get("passed"), review.get("issues"), events)


INPUTS = {"eligibility": eligibility_inputs, "computation": computation_inputs, "review": review_inputs}


def _routing(state: dict) -> dict:
    routing = state.get("routing") or {}
    return {"inputs_seen": dict(routing.get("inputs_seen", {})),
            "handoff_cursor": int(routing.get("handoff_cursor", 0)),
            "trail": [list(hop) for hop in routing.get("trail", [])]}

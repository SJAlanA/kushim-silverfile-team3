"""LangGraph wiring: nodes, conditional edges, parallel branch, review loop, loop guard.

Owner: Task division and management.

Topology: a hub and spokes. Every agent returns to the orchestrator, and the orchestrator's
conditional edge picks the next node from state (see src/agents/orchestrator.py). The one
exception is extraction, which fans out with `Send` (one `extract_one` branch per document)
and joins at `extract_aggregate` before going back to the hub.

    START -> orchestrator -> intake | extract_dispatch | eligibility | computation
                              | review | human_review | finalize
    extract_dispatch => extract_one (xN, parallel) -> extract_aggregate
    finalize -> END;  human_review -> END when a volunteer chooses "stop"
"""
from __future__ import annotations

from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from src.agents import computation, eligibility, extraction, intake, orchestrator, review
from src.form import render
from src.state import SilverFileState

ROUTES = ("intake", "extract_dispatch", "eligibility", "computation", "review", "human_review", "finalize")
DECISIONS = ("continue", "stop")
SEND_BACK = "send_back:"


def build_graph(checkpointer=None, context_strategy: bool = True):
    """Compile the SilverFile graph. `context_strategy=False` disables selective recall."""
    graph = StateGraph(SilverFileState)
    graph.add_node("orchestrator", _orchestrator_node(context_strategy))
    graph.add_node("intake", intake.run)
    graph.add_node("extract_dispatch", lambda state: {})
    graph.add_node("extract_one", extraction.run_one)
    graph.add_node("extract_aggregate", extraction.aggregate)
    graph.add_node("eligibility", eligibility.run)
    graph.add_node("computation", computation.run)
    graph.add_node("review", _review_node)
    graph.add_node("human_review", human_review)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "orchestrator")
    graph.add_conditional_edges("orchestrator", orchestrator.route, list(ROUTES))
    graph.add_conditional_edges("extract_dispatch", fan_out, ["extract_one", "extract_aggregate"])
    graph.add_edge("extract_one", "extract_aggregate")
    for name in ("intake", "extract_aggregate", "eligibility", "computation", "review"):
        graph.add_edge(name, "orchestrator")
    graph.add_conditional_edges("human_review", after_human_review, ["orchestrator", END])
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


def _orchestrator_node(context_strategy: bool):
    def node(state: SilverFileState) -> dict:
        update = orchestrator.run(state)
        if "context_strategy" not in state:
            update["context_strategy"] = context_strategy
        return update
    return node


# ---------------------------------------------------------------------------
# Parallel extraction
# ---------------------------------------------------------------------------

def fan_out(state: SilverFileState) -> list[Send] | str:
    """One `extract_one` branch per document that still needs reading; all join at extract_aggregate.

    With nothing to read (e.g. a send_back for a document that has since been read) the branch
    goes straight to the join, so the graph never stalls on an empty fan-out.
    """
    pending = extraction.documents_to_extract(state)
    if not pending:
        return "extract_aggregate"
    strategy = state.get("context_strategy", True)
    return [Send("extract_one", {"doc": doc, "context_strategy": strategy}) for doc in pending]


# ---------------------------------------------------------------------------
# Review loop bound
# ---------------------------------------------------------------------------

def _review_node(state: SilverFileState) -> dict:
    """Run Review and make sure every pass counts toward MAX_REVIEW_ITERATIONS."""
    update = review.run(state)
    update.setdefault("review_iterations", state.get("review_iterations", 0) + 1)
    return update


# ---------------------------------------------------------------------------
# Human in the loop
# ---------------------------------------------------------------------------

def human_review(state: SilverFileState) -> dict:
    """Pause on the oldest open HITL event and apply the volunteer's resolution (contract section 7).

    Resume payload: {"decision": "continue" | "stop" | "send_back:<agent>", "note": str,
    "resolved_by": str, "updates": {question_id: value}, "event_id": optional}.
    A resolution also clears the loop guard's trail: a person has now looked at the run.
    """
    events = orchestrator.open_events(state)
    if not events:
        return {}
    payload = interrupt({"type": "hitl", "event": events[0]})
    event = next((e for e in events if e["event_id"] == payload.get("event_id")), events[0])
    decision = validate_decision(payload.get("decision", ""))
    now = datetime.now(timezone.utc).isoformat()
    updates = payload.get("updates") or {}
    resolution = {"decision": decision, "note": payload.get("note", ""),
                  "resolved_by": payload.get("resolved_by", ""), "resolved_at": now, "updates": updates}

    routing = dict(state.get("routing") or {})
    update: dict = {"hitl": [{**event, "status": "resolved", "resolution": resolution}],
                    "routing": {**routing, "trail": []}}
    if updates:
        update["intake"] = {qid: {"value": value, "raw_text": "Entered by a Kushim volunteer.",
                                  "answered_at": now, "history": []} for qid, value in updates.items()}
    if decision.startswith(SEND_BACK):
        update["rerun"] = [orchestrator.SEND_BACK_TARGETS[decision[len(SEND_BACK):]]]
    if updates or decision.startswith(SEND_BACK):
        update["review_iterations"] = 0  # a human changed something: Review gets a fresh budget
    if decision == "stop":
        update["status"] = "escalated"
    return update


def validate_decision(decision: str) -> str:
    """Return the decision if valid, else raise ValueError with the allowed values."""
    target = decision[len(SEND_BACK):] if decision.startswith(SEND_BACK) else None
    if decision in DECISIONS or target in orchestrator.WORKERS:
        return decision
    allowed = list(DECISIONS) + [SEND_BACK + agent for agent in orchestrator.WORKERS]
    raise ValueError(f"decision {decision!r} is not one of {', '.join(allowed)}")


def after_human_review(state: SilverFileState) -> str:
    return END if state.get("status") == "escalated" else "orchestrator"


def finalize(state: SilverFileState) -> dict:
    return {"outputs": render.render_all(state), "status": "complete"}

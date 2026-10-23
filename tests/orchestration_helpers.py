"""Small builders shared by the orchestration tests: stub agents and a graph driver."""
from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.messages import as_update, send_back


def answer(value) -> dict:
    return {"value": value, "raw_text": "", "answered_at": "2026-01-01T00:00:00", "history": []}


def finished_intake(documents: list[dict] | None = None, first_name: str = "Mara"):
    """An Intake stub that is already complete, with the given documents."""
    def run(state: dict) -> dict:
        return {"intake": {"taxpayer_first_name": answer(first_name)}, "intake_complete": True,
                "documents": documents or []}
    return run


def passing_review(state: dict) -> dict:
    return {"review": {"passed": True, "issues": []}}


def ping_pong(sender: str, target: str, update: dict | None = None):
    """A stub agent that sends the work straight back to `target`, changing nothing else."""
    def run(state: dict) -> dict:
        return {**(update or {}), **as_update(send_back(sender, target, "Please redo this."))}
    return run


def stub_agents(monkeypatch, **agents) -> None:
    """Replace agent `run` functions before the graph is built. Unnamed agents get trivial stubs."""
    from src.agents import computation, eligibility, intake, review

    defaults = {"intake": finished_intake(), "eligibility": lambda state: {"rules": {"in_scope": True}},
                "computation": lambda state: {"lines": {}}, "review": passing_review}
    modules = {"intake": intake, "eligibility": eligibility, "computation": computation, "review": review}
    for name, module in modules.items():
        monkeypatch.setattr(module, "run", agents.get(name, defaults[name]))


def new_graph(**kwargs):
    from src.graph import build_graph

    return build_graph(InMemorySaver(), **kwargs)


def config(session_id: str = "test") -> dict:
    return {"configurable": {"thread_id": session_id}}


def pending(graph, cfg: dict) -> dict | None:
    snapshot = graph.get_state(cfg)
    return next((i.value for task in snapshot.tasks for i in task.interrupts), None)


def resume(graph, cfg: dict, decision: str, **extra) -> None:
    graph.invoke(Command(resume={"decision": decision, "note": "", "resolved_by": "tester", **extra}), cfg)

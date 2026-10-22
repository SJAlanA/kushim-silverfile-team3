"""Shared test setup: repo root on the path, offline mode, and a small intake-only graph."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["SILVERFILE_OFFLINE"] = "1"
os.environ["TAX_MATH_INPROCESS"] = "1"

import pytest  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402

from src.agents import intake  # noqa: E402
from src.state import SilverFileState  # noqa: E402


def build_intake_graph(checkpointer):
    """Intake looping on itself until it finishes or raises a HITL event (stands in for the orchestrator)."""
    def route(state: SilverFileState) -> str:
        open_hitl = any(event["status"] == "open" for event in state.get("hitl", []))
        return END if state.get("intake_complete") or open_hitl else "intake"

    graph = StateGraph(SilverFileState)
    graph.add_node("intake", intake.run)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", route)
    return graph.compile(checkpointer=checkpointer)


class Dialog:
    """Drives one intake session: start(), then say() replies; `payload` is the open question."""

    def __init__(self, graph, session_id: str = "test-session"):
        self.graph = graph
        self.config = {"configurable": {"thread_id": session_id}}
        self.session_id = session_id
        self.payload = None

    def start(self):
        return self._step({"session_id": self.session_id})

    def say(self, reply: str):
        from langgraph.types import Command

        return self._step(Command(resume=reply))

    def answer_until(self, case: dict, question_id: str):
        """Answer from a case file until `question_id` is the open question."""
        while self.payload["question_id"] != question_id:
            self.say(intake.case_reply(case, self.payload))
        return self.payload

    @property
    def state(self) -> dict:
        return self.graph.get_state(self.config).values

    def _step(self, value):
        result = self.graph.invoke(value, self.config)
        interrupts = result.get("__interrupt__", [])
        self.payload = interrupts[0].value if interrupts else None
        return self.payload


@pytest.fixture
def intake_graph():
    from langgraph.checkpoint.memory import InMemorySaver

    return build_intake_graph(InMemorySaver())


@pytest.fixture
def case_single() -> dict:
    import json

    return json.loads((ROOT / "data" / "fixtures" / "batch" / "case_01.json").read_text())


@pytest.fixture
def dialog(intake_graph):
    session = Dialog(intake_graph)
    session.start()
    return session

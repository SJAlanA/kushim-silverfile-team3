"""SilverFile entry point.

    python run.py --case demo/eval_case_1/case.json     # replay a case file end to end
    python run.py --interactive                         # a real person answers at the terminal
    python run.py --session <id>                        # resume a saved session
    python run.py --batch [--no-context-strategy]       # context-window measurement
    python run.py --dashboard                           # staff dashboard on http://localhost:8000
    python run.py --taxpayer                            # taxpayer page on http://localhost:8001
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

DASHBOARD_URL = "http://localhost:8000"
WAITING = f"Waiting for a Kushim volunteer. Resolve it in the dashboard at {DASHBOARD_URL}"
POLL_SECONDS = 2
MAX_STEPS = 500  # interrupts answered in one case replay; a safety net, never reached by a sound run


def main() -> None:
    parser = argparse.ArgumentParser(description="Kushim SilverFile: Form 1040-SR draft preparation")
    parser.add_argument("--case", help="Path to a case file (contract section 13) to replay")
    parser.add_argument("--interactive", action="store_true", help="Answer the questions yourself")
    parser.add_argument("--session", help="Resume an existing session by its id")
    parser.add_argument("--batch", action="store_true", help="Run the batch scenario")
    parser.add_argument("--no-context-strategy", action="store_true", help="Disable the context-window strategy")
    parser.add_argument("--dashboard", action="store_true", help="Start the staff dashboard")
    parser.add_argument("--taxpayer", action="store_true", help="Start the taxpayer page")
    args = parser.parse_args()
    strategy = not args.no_context_strategy
    load_dotenv()  # ANTHROPIC_API_KEY etc. from .env; variables already set in the shell win

    if args.batch:
        from src.batch import print_table, run_batch

        print_table(run_batch(context_strategy=strategy), strategy)
    elif args.dashboard:
        from src.dashboard import create_app

        create_app().run(host=os.getenv("DASHBOARD_HOST", "127.0.0.1"), port=8000)
    elif args.taxpayer:
        from src.taxpayer import create_app as create_taxpayer_app

        create_taxpayer_app().run(host=os.getenv("TAXPAYER_HOST", "127.0.0.1"), port=8001)
    elif args.case:
        run_case(Path(args.case), strategy)
    elif args.interactive or args.session:
        run_interactive(args.session, strategy)
    else:
        parser.print_help()


def _graph(strategy: bool):
    from src.graph import build_graph
    from src.memory import get_checkpointer

    return build_graph(get_checkpointer(), context_strategy=strategy)


def _config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def _pending(graph, config: dict) -> dict | None:
    snapshot = graph.get_state(config)
    return next((interrupt.value for task in snapshot.tasks for interrupt in task.interrupts), None)


# ---------------------------------------------------------------------------
# Case replay
# ---------------------------------------------------------------------------

def run_case(path: Path, strategy: bool) -> None:
    """Replay a case: intake questions from `answers`, HITL events from `hitl_resolutions`."""
    from langgraph.types import Command

    from src.agents.intake import case_reply

    case = json.loads(path.read_text())
    session_id = f"{case.get('case_id', path.stem)}-{uuid.uuid4().hex[:6]}"
    graph, config = _graph(strategy), _config(session_id)
    print(f"Session {session_id}")

    value: object = {"session_id": session_id}
    used: list[dict] = []
    for _ in range(MAX_STEPS):
        graph.invoke(value, config)
        payload = _pending(graph, config)
        if payload is None:
            break
        if payload["type"] == "intake_question":
            reply = case_reply(case, payload)
            print(f"\nSilverFile: {payload['text']}\nTaxpayer:   {reply}")
            value = Command(resume=reply)
            continue
        event = payload["event"]
        print(f"\nHITL {event['condition']} (raised by {event['raised_by']}): {event['reason']}")
        resolution = match_resolution(event, case.get("hitl_resolutions", []), used)
        if resolution is None:
            print(f"No resolution in the case file. {WAITING}")
            return
        print(f"Resolved: {resolution['decision']} by {resolution.get('resolved_by', 'case file')}")
        value = Command(resume={**resolution, "event_id": event["event_id"]})
    _print_result(graph.get_state(config).values)


def match_resolution(event: dict, resolutions: list[dict], used: list[dict]) -> dict | None:
    """The first unused resolution for this event's condition; a repeat of the same condition reuses the last one."""
    matching = [r for r in resolutions if r.get("condition") == event["condition"]]
    fresh = next((r for r in matching if not any(r is u for u in used)), None)
    if fresh is not None:
        used.append(fresh)
        return fresh
    return matching[-1] if matching else None


def _print_result(values: dict) -> None:
    print(f"\nStatus: {values.get('status')}")
    for name, location in (values.get("outputs") or {}).items():
        if location:
            print(f"  {name}: {location}")


# ---------------------------------------------------------------------------
# Interactive
# ---------------------------------------------------------------------------

def run_interactive(session_id: str | None, strategy: bool) -> None:
    """A real person answers at the terminal. HITL events are resolved in the dashboard."""
    from langgraph.types import Command

    from src.memory import resume_session

    graph = _graph(strategy)
    if session_id:
        config = _config(session_id)
        if not graph.get_state(config).values:
            sys.exit(f"No saved session called {session_id}.")
        print(resume_session(graph, session_id)["summary"])
    else:
        session_id = f"session-{uuid.uuid4().hex[:8]}"
        config = _config(session_id)
        graph.invoke({"session_id": session_id}, config)
    print(f"(Session {session_id}. You can stop any time and resume with --session {session_id}.)")

    waiting = False
    try:
        while (payload := _pending(graph, config)) is not None:
            if payload["type"] == "intake_question":
                waiting = False
                reply = input(f"\n{payload['text']}\n> ")
                graph.invoke(Command(resume=reply), config)
            else:
                if not waiting:
                    print(f"\n{WAITING}")
                    waiting = True
                time.sleep(POLL_SECONDS)
    except (EOFError, KeyboardInterrupt):  # Ctrl-D or Ctrl-C: every answer so far is already saved
        print(f"\n\nStopped. Everything you've answered is saved. To carry on later, run:\n"
              f"  python run.py --session {session_id}")
        return
    _print_result(graph.get_state(config).values)


if __name__ == "__main__":
    main()

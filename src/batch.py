"""Batch scenario for the context-window strategy (brief 4.5).

Owner: Agent memory.
Ten taxpayer intakes run one after another in ONE LangGraph thread, so the conversation history
keeps growing. With the strategy on, Intake only sees the current taxpayer's messages plus a short
rolling summary; with it off, it sees everything. Per-item input tokens go to docs/metrics.md.

    python -m src.batch --write-metrics
"""
from __future__ import annotations

import argparse
import json
import operator
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from src.agents import intake
from src.memory import get_checkpointer, update_decisions_summary
from src.state import merge_answers, merge_documents, merge_hitl

ROOT = Path(__file__).resolve().parent.parent
BATCH_DIR = ROOT / "data" / "fixtures" / "batch"
METRICS_PATH = ROOT / "docs" / "metrics.md"
CHART_PATH = ROOT / "docs" / "context_strategy.svg"
MAX_TURNS_PER_ITEM = 80
_REDUCERS = {"messages": add_messages, "intake": merge_answers, "documents": merge_documents,
             "hitl": merge_hitl, "token_log": operator.add}


def _merge_dicts(left: dict | None, right: dict | None) -> dict:
    return {**(left or {}), **(right or {})}


class BatchState(TypedDict, total=False):
    session_id: str
    messages: Annotated[list, add_messages]
    decisions_summary: str
    token_log: Annotated[list[dict], operator.add]
    context_strategy: bool
    cases: list[dict]
    item_index: int
    results: Annotated[dict, _merge_dicts]


def load_cases(cases_dir: Path = BATCH_DIR) -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted(cases_dir.glob("*.json"))]


def process_item(state: BatchState) -> dict:
    """Run one taxpayer's whole intake against the shared thread history."""
    index = state.get("item_index", 0)
    case = state["cases"][index]
    item = case["case_id"]
    local = {"session_id": state["session_id"], "current_item": item,
             "context_strategy": state.get("context_strategy", True),
             "messages": list(state.get("messages", [])), "decisions_summary": state.get("decisions_summary", ""),
             "intake": {}, "intake_meta": {}, "documents": [], "hitl": [], "token_log": []}
    new_messages: list = []
    for _ in range(MAX_TURNS_PER_ITEM):
        _apply(local, intake.ask(local), new_messages)
        if local.get("intake_complete"):
            break
        reply = intake.case_reply(case, local["intake_meta"]["pending"])
        _apply(local, intake.answer(local, reply), new_messages)
        if any(event["status"] == "open" for event in local["hitl"]):
            local["decisions_summary"] = update_decisions_summary(local["decisions_summary"], item, local)
            break
    return {"messages": new_messages, "token_log": local["token_log"], "item_index": index + 1,
            "decisions_summary": local["decisions_summary"],
            "results": {item: {"intake": local["intake"], "hitl": local["hitl"]}}}


def _apply(local: dict, update: dict, new_messages: list) -> None:
    """Fold a node update into the local state with the same reducers the graph uses."""
    for key, value in update.items():
        local[key] = _REDUCERS[key](local.get(key), value) if key in _REDUCERS else value
    if update.get("messages"):
        new_messages.extend(local["messages"][-len(update["messages"]):])  # now carrying their ids


def build_batch_graph(checkpointer=None):
    graph = StateGraph(BatchState)
    graph.add_node("process_item", process_item)
    graph.add_edge(START, "process_item")
    graph.add_conditional_edges(
        "process_item", lambda state: "process_item" if state["item_index"] < len(state["cases"]) else END)
    return graph.compile(checkpointer=checkpointer)


def run_batch(context_strategy: bool = True, cases_dir: Path = BATCH_DIR) -> list[dict]:
    """Return [{"item", "input_tokens", "estimated"}] in processing order."""
    cases = load_cases(cases_dir)
    graph = build_batch_graph(get_checkpointer(":memory:"))
    thread = f"batch-{'on' if context_strategy else 'off'}"
    final = graph.invoke({"session_id": thread, "cases": cases, "item_index": 0, "messages": [],
                          "decisions_summary": "", "context_strategy": context_strategy},
                         {"configurable": {"thread_id": thread}, "recursion_limit": len(cases) + 5})
    rows = []
    for case in cases:
        entries = [entry for entry in final["token_log"] if entry["item"] == case["case_id"]]
        rows.append({"item": case["case_id"], "input_tokens": sum(entry["input_tokens"] for entry in entries),
                     "calls": len(entries), "estimated": any(entry.get("estimated") for entry in entries)})
    return rows


def write_metrics() -> None:
    off, on = run_batch(context_strategy=False), run_batch(context_strategy=True)
    estimated = any(row["estimated"] for row in off + on)
    table = "\n".join(f"| {index} | {a['item']} | {a['calls']} | {a['input_tokens']:,} | {b['input_tokens']:,} "
                      f"| {_per_call(a):,} | {_per_call(b):,} |"
                      for index, (a, b) in enumerate(zip(off, on), start=1))
    source = ("Offline run: counts are estimated as `len(prompt) // 4` over the exact prompt Intake would send."
              if estimated else "Counts are the model's reported `usage_metadata['input_tokens']`.")
    METRICS_PATH.write_text(f"""# Context-window metrics

Batch scenario (brief 4.5): 10 taxpayer intakes from `data/fixtures/batch/` run one after another in
one LangGraph thread. Each row is the total input tokens the Intake agent's model calls use for that
taxpayer (about 20 question turns each).

- **No strategy** (`python run.py --batch --no-context-strategy`): every prompt carries the whole
  thread history, so cost grows with every taxpayer already processed.
- **Strategy** (`python run.py --batch`): selective recall. Each prompt carries only the current
  taxpayer's messages plus a rolling decisions summary capped at the last five taxpayers.

{source} Regenerate with `python -m src.batch --write-metrics`.

Taxpayers need different numbers of questions, so the last two columns divide by the number of
Intake model calls for that taxpayer. That per-question view shows the shape most clearly: it climbs
steadily with no strategy and stays flat with selective recall.

| # | Item | Questions | Input tokens (no strategy) | Input tokens (strategy) | Per question (no strategy) | Per question (strategy) |
| --- | --- | --- | --- | --- | --- | --- |
{table}

| | No strategy | Strategy |
| --- | --- | --- |
| First item | {off[0]['input_tokens']:,} | {on[0]['input_tokens']:,} |
| Last item | {off[-1]['input_tokens']:,} | {on[-1]['input_tokens']:,} |
| Growth, last / first | {off[-1]['input_tokens'] / off[0]['input_tokens']:.1f}x | {on[-1]['input_tokens'] / on[0]['input_tokens']:.1f}x |
| Total | {sum(r['input_tokens'] for r in off):,} | {sum(r['input_tokens'] for r in on):,} |

![Per-item input tokens](context_strategy.svg)
""")
    CHART_PATH.write_text(_chart_svg(off, on))


def _per_call(row: dict) -> int:
    return row["input_tokens"] // max(row["calls"], 1)


def _chart_svg(off: list[dict], on: list[dict]) -> str:
    """A small dependency-free line chart of both runs."""
    width, height, pad = 640, 320, 56
    top = max(row["input_tokens"] for row in off + on) * 1.1
    count = len(off)

    def point(index: int, value: int) -> str:
        x = pad + index * (width - 2 * pad) / max(count - 1, 1)
        y = height - pad - value / top * (height - 2 * pad)
        return f"{x:.1f},{y:.1f}"

    def series(rows: list[dict], color: str) -> str:
        points = " ".join(point(i, row["input_tokens"]) for i, row in enumerate(rows))
        dots = "".join(f'<circle cx="{point(i, row["input_tokens"]).split(",")[0]}" '
                       f'cy="{point(i, row["input_tokens"]).split(",")[1]}" r="3.5" fill="{color}"/>'
                       for i, row in enumerate(rows))
        return f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5"/>{dots}'

    ticks = "".join(f'<text x="{point(i, 0).split(",")[0]}" y="{height - pad + 20}" text-anchor="middle">{i + 1}</text>'
                    for i in range(count))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#333">
<rect width="{width}" height="{height}" fill="#fff"/>
<text x="{pad}" y="24" font-size="15" font-weight="bold">Intake input tokens per taxpayer, one thread</text>
<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" stroke="#999"/>
<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height - pad}" stroke="#999"/>
<text x="{pad - 8}" y="{pad + 4}" text-anchor="end">{int(top):,}</text>
<text x="{pad - 8}" y="{height - pad + 4}" text-anchor="end">0</text>
{ticks}
<text x="{width / 2}" y="{height - 12}" text-anchor="middle">Taxpayer in batch</text>
{series(off, "#c0392b")}
{series(on, "#2471a3")}
<rect x="{width - pad - 170}" y="40" width="12" height="12" fill="#c0392b"/><text x="{width - pad - 152}" y="50">No strategy</text>
<rect x="{width - pad - 170}" y="60" width="12" height="12" fill="#2471a3"/><text x="{width - pad - 152}" y="70">Selective recall</text>
</svg>
"""


def print_table(rows: list[dict], context_strategy: bool) -> None:
    print(f"Batch run, context strategy {'ON' if context_strategy else 'OFF'}")
    for row in rows:
        print(f"  {row['item']:<12} {row['input_tokens']:>8,} input tokens{' (estimated)' if row['estimated'] else ''}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SilverFile batch scenario")
    parser.add_argument("--write-metrics", action="store_true", help="Run both modes and write docs/metrics.md")
    parser.add_argument("--no-context-strategy", action="store_true")
    args = parser.parse_args()
    if args.write_metrics:
        write_metrics()
        print(f"Wrote {METRICS_PATH} and {CHART_PATH}")
    else:
        strategy = not args.no_context_strategy
        print_table(run_batch(context_strategy=strategy), strategy)

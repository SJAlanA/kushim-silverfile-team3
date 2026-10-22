"""Context-window strategy: selective recall keeps per-item input flat across a 10-item batch (brief 4.5)."""
from langchain_core.messages import AIMessage, HumanMessage

from src.batch import run_batch
from src.memory import apply_context_strategy, update_decisions_summary


def _tagged(item: str, text: str):
    return [AIMessage(content=text, additional_kwargs={"item": item}),
            HumanMessage(content="yes", additional_kwargs={"item": item})]


def test_selective_recall_keeps_only_current_item_plus_summary():
    messages = _tagged("batch_01", "Old question?") + _tagged("batch_02", "Current question?")
    state = {"current_item": "batch_02", "decisions_summary": "- batch_01: filing status single"}
    recalled = apply_context_strategy(messages, state, enabled=True)
    assert recalled[0].type == "system" and "batch_01" in recalled[0].content
    assert [message.content for message in recalled[1:]] == ["Current question?", "yes"]
    assert apply_context_strategy(messages, state, enabled=False) == messages


def test_decisions_summary_is_capped():
    summary = ""
    for index in range(1, 13):
        summary = update_decisions_summary(summary, f"batch_{index:02d}", {})
    lines = summary.splitlines()
    assert lines[0] == "Earlier: 7 items finished."
    assert [line.split(":")[0] for line in lines[1:]] == [f"- batch_{index:02d}" for index in range(8, 13)]


def test_strategy_gives_a_materially_flatter_curve():
    off = [row["input_tokens"] for row in run_batch(context_strategy=False)]
    on = [row["input_tokens"] for row in run_batch(context_strategy=True)]
    assert len(off) == len(on) == 10
    assert off[0] == on[0]  # identical until there is history to drop
    assert off[-1] > 5 * off[0]  # without the strategy, input keeps growing
    assert max(on) < 3 * min(on)  # with it, input stays within one item's natural size range
    assert sum(on) < sum(off) / 4

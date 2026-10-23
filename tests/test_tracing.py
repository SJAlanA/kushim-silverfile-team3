"""Per-session JSONL traces (src/tools/tracing.py)."""
import json

import pytest

from src.tools.tracing import session, traced_agent, write_event


@pytest.fixture(autouse=True)
def _trace_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SILVERFILE_TRACE_DIR", str(tmp_path))


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_traced_agent_records_start_end_and_duration(tmp_path):
    node = traced_agent("review", lambda state: {"review": {"passed": True}})
    assert node({"session_id": "s1"}) == {"review": {"passed": True}}
    start, end = _events(tmp_path / "s1.jsonl")
    assert (start["kind"], start["agent"], end["kind"], end["outcome"]) == ("node_start", "review", "node_end", "ok")
    assert end["duration_ms"] >= 0 and end["session_id"] == "s1"


def test_failed_node_is_traced_and_still_raises(tmp_path):
    def broken(state):
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        traced_agent("eligibility", broken)({"session_id": "s2"})
    assert _events(tmp_path / "s2.jsonl")[-1]["outcome"] == "error"


def test_nothing_written_outside_a_session(tmp_path):
    write_event({"kind": "tool_call"})
    assert list(tmp_path.iterdir()) == []


def test_session_id_cannot_escape_the_trace_directory(tmp_path):
    with session("../../etc/passwd"):
        write_event({"kind": "note"})
    assert [p.name for p in tmp_path.iterdir()] == ["_.._etc_passwd.jsonl"]

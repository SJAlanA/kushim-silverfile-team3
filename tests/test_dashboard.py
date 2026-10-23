"""The dashboard lists sessions and resolving a HITL event lets the graph carry on."""
import pytest

from src.hitl import raise_hitl
from src.memory import get_checkpointer
from tests.orchestration_helpers import answer, config, pending, stub_agents


@pytest.fixture
def paused_session(monkeypatch, tmp_path):
    """A session in the SQLite store, paused on an UNCERTAIN event raised by Intake."""
    monkeypatch.chdir(tmp_path)
    db = str(tmp_path / "checkpoints.sqlite")

    def intake(state):
        if not state.get("hitl"):
            return {"intake": {"taxpayer_first_name": answer("Mara"), "taxpayer_ssn": answer("901-11-1037")},
                    **raise_hitl("UNCERTAIN", "The taxpayer wasn't sure about her date of birth.", "intake")}
        return {"intake_complete": True}

    stub_agents(monkeypatch, intake=intake)
    from src.graph import build_graph

    graph = build_graph(get_checkpointer(db))
    graph.invoke({"session_id": "dash-1"}, config("dash-1"))
    assert pending(graph, config("dash-1"))["type"] == "hitl"
    return db, graph


@pytest.fixture
def client(paused_session):
    from src.dashboard import create_app

    app = create_app(paused_session[0])
    app.testing = True
    return app.test_client()


def test_sessions_page_groups_the_paused_session(client):
    page = client.get("/").get_data(as_text=True)
    needs_human = page.split('id="h-needs_human"')[1].split("</section>")[0]
    assert "Mara" in needs_human and "Needs a human" in needs_human


def test_session_page_masks_the_ssn(client):
    page = client.get("/sessions/dash-1").get_data(as_text=True)
    assert "901-11-1037" not in page and "1037" in page


def test_resolving_an_event_continues_the_graph(client, paused_session):
    _, graph = paused_session
    event_id = graph.get_state(config("dash-1")).values["hitl"][0]["event_id"]
    assert "date of birth" in client.get("/hitl").get_data(as_text=True)

    response = client.post(f"/sessions/dash-1/hitl/{event_id}",
                           data={"decision": "continue", "note": "Confirmed by phone", "resolved_by": "Dana",
                                 "updates": "taxpayer_dob = 1958-06-21"})
    assert response.status_code == 302

    values = graph.get_state(config("dash-1")).values
    assert values["hitl"][0]["status"] == "resolved"
    assert values["hitl"][0]["resolution"]["resolved_by"] == "Dana"
    assert values["intake"]["taxpayer_dob"]["value"] == "1958-06-21"
    assert values["status"] == "complete"
    completed = client.get("/").get_data(as_text=True).split('id="h-completed"')[1]
    assert "Mara" in completed


def test_resolve_needs_a_name(client, paused_session):
    _, graph = paused_session
    event_id = graph.get_state(config("dash-1")).values["hitl"][0]["event_id"]
    response = client.post(f"/sessions/dash-1/hitl/{event_id}", data={"decision": "continue", "resolved_by": ""})
    assert response.status_code == 400
    assert graph.get_state(config("dash-1")).values["hitl"][0]["status"] == "open"

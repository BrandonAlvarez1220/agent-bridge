from __future__ import annotations

import pytest

from agentbridge.errors import ConflictError, NotFoundError
from agentbridge.services import context, decisions, messaging, tasks

P = "demo"


def _record(db, clock, title="Usar UTC", **kwargs):
    params = {"decision": "Fechas en UTC", "rationale": "Comparables entre procesos"} | kwargs
    return decisions.record_decision(db, clock, project_id=P, agent_id="claude", title=title, **params)


def test_recording_a_decision_notifies_other_agents(db, clock, bridge):
    decision = _record(db, clock, affected_paths=["src/clock.py"])
    (item,) = messaging.read_inbox(db, clock, project_id=P, agent_id="opencode")
    assert f"#{decision.id}" in item.message.body and "src/clock.py" in item.message.body


def test_superseding_hides_old_decision_by_default(db, clock, bridge):
    old = _record(db, clock, "API v1")
    new = _record(db, clock, "API v2", supersedes=old.id)
    assert [d.id for d in decisions.search_decisions(db, project_id=P)] == [new.id]
    both = decisions.search_decisions(db, project_id=P, include_superseded=True)
    assert {d.id: d.superseded_by for d in both} == {new.id: None, old.id: new.id}
    with pytest.raises(ConflictError):
        _record(db, clock, "API v3", supersedes=old.id)
    with pytest.raises(NotFoundError):
        _record(db, clock, "x", supersedes=999)


def test_search_treats_wildcards_literally(db, clock, bridge):
    _record(db, clock, "Cobertura 100%")
    _record(db, clock, "Cobertura 1000")
    assert [d.title for d in decisions.search_decisions(db, project_id=P, query="100%")] == ["Cobertura 100%"]
    assert decisions.search_decisions(db, project_id=P, query="snake_case") == []


def test_project_context_summarizes_state(db, clock, bridge):
    _record(db, clock)
    mine = tasks.create_task(db, clock, project_id=P, actor_id="claude", title="mía")
    ready = tasks.create_task(db, clock, project_id=P, actor_id="claude", title="lista")
    tasks.claim_task(db, clock, project_id=P, task_id=mine.id, agent_id="opencode")
    ctx = context.get_project_context(db, clock, project_id=P, agent_id="opencode")
    assert ctx.you.id == "opencode"
    assert [t.id for t in ctx.your_tasks] == [mine.id]
    assert [t.id for t in ctx.ready_tasks] == [ready.id]
    assert ctx.unacknowledged_messages == 1
    assert len(ctx.decisions) == 1 and "datos" in ctx.protocol

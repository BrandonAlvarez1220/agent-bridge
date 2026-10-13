from __future__ import annotations

import pytest

from agentbridge.errors import ConflictError, InvalidInputError, NotFoundError, PermissionDeniedError
from agentbridge.services import audit, reservations, tasks

P = "demo"


def _create(db, clock, title="t", **kwargs):
    return tasks.create_task(db, clock, project_id=P, actor_id="claude", title=title, **kwargs)


def _status(db, clock, task_id, status, agent="claude", **kwargs):
    return tasks.update_task_status(db, clock, project_id=P, task_id=task_id, agent_id=agent, status=status, **kwargs)


def _claim(db, clock, task_id, agent="claude", **kwargs):
    return tasks.claim_task(db, clock, project_id=P, task_id=task_id, agent_id=agent, **kwargs)


def _report(db, clock, task_id, agent="claude"):
    return tasks.report_completion(
        db,
        clock,
        project_id=P,
        task_id=task_id,
        agent_id=agent,
        summary="hecho",
        changed_files=["src/a.py"],
        tests_run=["pytest"],
        test_results="ok",
    )


def _finish(db, clock, task_id):
    _claim(db, clock, task_id)
    _report(db, clock, task_id)
    _status(db, clock, task_id, "done")


def test_create_task_normalizes_paths_and_starts_ready(db, clock, bridge):
    task = _create(db, clock, allowed_paths=["./src//auth/", "docs/x.md"])
    assert task.status == "open" and task.ready
    assert task.allowed_paths == ["docs/x.md", "src/auth/"]


@pytest.mark.parametrize("kwargs", [{"title": " "}, {"title": "x" * 201}, {"priority": 5}, {"allowed_paths": ["../x"]}])
def test_create_task_validates(db, clock, bridge, kwargs):
    with pytest.raises(InvalidInputError):
        _create(db, clock, **kwargs)


def test_unknown_dependency_is_rejected(db, clock, bridge):
    with pytest.raises(NotFoundError):
        _create(db, clock, dependencies=[999])


def test_dependencies_block_until_done(db, clock, bridge):
    base = _create(db, clock, "base")
    child = _create(db, clock, "child", dependencies=[base.id])
    assert not child.ready and child.blocked_by == [base.id]
    with pytest.raises(ConflictError) as err:
        _claim(db, clock, child.id, agent="opencode")
    assert err.value.details["blocked_by"] == [base.id]

    _finish(db, clock, base.id)
    assert tasks.get_task(db, clock, project_id=P, task_id=child.id).ready
    assert _claim(db, clock, child.id, agent="opencode").assignee_id == "opencode"


@pytest.mark.parametrize("chain_length", [2, 3, 5])
def test_cycles_are_rejected(db, clock, bridge, chain_length):
    ids = [_create(db, clock, f"t{i}").id for i in range(chain_length)]
    for a, b in zip(ids, ids[1:], strict=False):
        tasks.add_dependency(db, clock, project_id=P, actor_id="claude", task_id=a, depends_on=b)
    with pytest.raises(ConflictError, match="ciclo"):
        tasks.add_dependency(db, clock, project_id=P, actor_id="claude", task_id=ids[-1], depends_on=ids[0])


def test_claim_is_exclusive_and_idempotent_for_owner(db, clock, bridge):
    task = _create(db, clock)
    first = _claim(db, clock, task.id, lease_seconds=600)
    clock.advance(60)
    again = _claim(db, clock, task.id, lease_seconds=600)
    assert again.lease_expires_at > first.lease_expires_at
    with pytest.raises(ConflictError, match="ya está reclamada"):
        _claim(db, clock, task.id, agent="opencode")
    assert [e.event_type for e in audit.list_events(db, project_id=P) if e.entity_type == "task"] == [
        "task.created",
        "task.claimed",
    ]


def test_expired_lease_can_be_taken_over_and_is_audited(db, clock, bridge):
    task = _create(db, clock)
    _claim(db, clock, task.id, lease_seconds=60)
    clock.advance(61)
    assert tasks.get_task(db, clock, project_id=P, task_id=task.id).lease_expired
    taken = _claim(db, clock, task.id, agent="opencode")
    assert taken.assignee_id == "opencode" and not taken.lease_expired
    event = audit.list_events(db, project_id=P)[-1]
    assert event.event_type == "task.lease_expired"
    assert event.payload["previous_assignee"] == "claude"


def test_only_owner_can_change_active_task(db, clock, bridge):
    task = _create(db, clock)
    _claim(db, clock, task.id)
    with pytest.raises(PermissionDeniedError):
        _status(db, clock, task.id, "blocked", agent="opencode")
    assert _status(db, clock, task.id, "blocked").status == "blocked"
    assert _status(db, clock, task.id, "in_progress").status == "in_progress"


def test_review_requires_report_completion(db, clock, bridge):
    task = _create(db, clock)
    _claim(db, clock, task.id)
    with pytest.raises(ConflictError, match="report_completion"):
        _status(db, clock, task.id, "review")


def test_invalid_status_is_input_error(db, clock, bridge):
    task = _create(db, clock)
    with pytest.raises(InvalidInputError):
        _status(db, clock, task.id, "ready")


def test_releasing_task_clears_owner_and_reservations(db, clock, bridge):
    task = _create(db, clock)
    _claim(db, clock, task.id)
    reservations.reserve_paths(db, clock, project_id=P, agent_id="claude", task_id=task.id, paths=["src/a.py"])
    released = _status(db, clock, task.id, "open", note="no puedo seguir")
    assert released.assignee_id is None and released.lease_expires_at is None and released.ready
    assert reservations.list_reservations(db, clock, project_id=P) == []


def test_report_completion_moves_to_review_and_releases(db, clock, bridge):
    task = _create(db, clock)
    _claim(db, clock, task.id)
    reservations.reserve_paths(db, clock, project_id=P, agent_id="claude", task_id=task.id, paths=["src/"])
    report = _report(db, clock, task.id)
    assert report.changed_files == ["src/a.py"]
    after = tasks.get_task(db, clock, project_id=P, task_id=task.id)
    assert after.status == "review" and after.assignee_id == "claude" and after.lease_expires_at is None
    assert reservations.list_reservations(db, clock, project_id=P) == []
    assert tasks.list_reports(db, project_id=P, task_id=task.id) == [report]


def test_report_by_non_owner_is_denied(db, clock, bridge):
    task = _create(db, clock)
    _claim(db, clock, task.id)
    with pytest.raises(PermissionDeniedError):
        _report(db, clock, task.id, agent="opencode")


def test_review_can_go_back_to_owner_or_done(db, clock, bridge):
    task = _create(db, clock)
    _claim(db, clock, task.id)
    _report(db, clock, task.id)
    back = _status(db, clock, task.id, "in_progress", agent="opencode", note="faltan pruebas")
    assert back.assignee_id == "claude" and back.lease_expires_at is not None
    _report(db, clock, task.id)
    done = _status(db, clock, task.id, "done", agent="opencode")
    assert done.completed_at is not None
    with pytest.raises(ConflictError):
        _claim(db, clock, task.id, agent="opencode")


def test_list_tasks_filters_and_orders_by_priority(db, clock, bridge):
    low = _create(db, clock, "low", priority=4)
    high = _create(db, clock, "high", priority=0)
    blocked = _create(db, clock, "blocked", dependencies=[low.id])
    assert [t.id for t in tasks.list_tasks(db, clock, project_id=P, ready_only=True)] == [high.id, low.id]
    assert blocked.id in [t.id for t in tasks.list_tasks(db, clock, project_id=P, status="open")]
    _claim(db, clock, high.id, agent="opencode")
    assert [t.id for t in tasks.list_tasks(db, clock, project_id=P, assignee_id="opencode")] == [high.id]

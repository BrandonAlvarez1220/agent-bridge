from __future__ import annotations

import pytest

from agentbridge.errors import InvalidInputError
from agentbridge.services import audit
from agentbridge.storage.database import transaction


def _record(db, clock, project, **kwargs):
    with transaction(db):
        return audit.record(db, clock, project_id=project, actor_id="claude", event_type="t", entity_type="x", **kwargs)


def test_record_requires_transaction(db, clock, project):
    with pytest.raises(RuntimeError, match="dentro de la transacción"):
        audit.record(db, clock, project_id=project, actor_id=None, event_type="t", entity_type="x")


def test_event_rolls_back_with_operation(db, clock, project):
    with pytest.raises(ValueError), transaction(db):
        audit.record(db, clock, project_id=project, actor_id=None, event_type="t", entity_type="x")
        raise ValueError
    assert audit.list_events(db, project_id=project) == []


def test_secrets_are_redacted_at_any_depth(db, clock, project):
    _record(
        db,
        clock,
        project,
        payload={"api_key": "sk-123", "nested": {"Password": "p", "ok": 1}, "items": [{"token": "t"}]},
    )
    (event,) = audit.list_events(db, project_id=project)
    assert event.payload == {
        "api_key": audit.REDACTED,
        "nested": {"Password": audit.REDACTED, "ok": 1},
        "items": [{"token": audit.REDACTED}],
    }


def test_oversized_payload_is_truncated(db, clock, project):
    _record(db, clock, project, payload={"blob": "x" * (audit.MAX_PAYLOAD_BYTES + 1)})
    (event,) = audit.list_events(db, project_id=project)
    assert event.payload["truncated"] is True


def test_list_events_paginates_by_id(db, clock, project):
    ids = [_record(db, clock, project, entity_id=i) for i in range(5)]
    first = audit.list_events(db, project_id=project, limit=2)
    rest = audit.list_events(db, project_id=project, after_id=first[-1].id)
    assert [e.id for e in first + rest] == ids


@pytest.mark.parametrize("limit", [0, audit.MAX_LIST_LIMIT + 1])
def test_list_events_validates_limit(db, project, limit):
    with pytest.raises(InvalidInputError):
        audit.list_events(db, project_id=project, limit=limit)

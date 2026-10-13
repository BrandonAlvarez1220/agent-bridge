from __future__ import annotations

import pytest

from agentbridge.errors import InvalidInputError, NotFoundError, PermissionDeniedError
from agentbridge.services import messaging
from agentbridge.storage.database import open_database

P = "demo"


def _send(db, clock, body="hola", sender="claude", **kwargs):
    return messaging.publish_message(db, clock, project_id=P, sender_id=sender, body=body, **kwargs)


def _inbox(db, clock, agent="opencode", **kwargs):
    return messaging.read_inbox(db, clock, project_id=P, agent_id=agent, **kwargs)


def _ack(db, clock, message_id, agent="opencode"):
    return messaging.acknowledge_message(db, clock, project_id=P, message_id=message_id, agent_id=agent)


def test_message_survives_recipient_restart(db, clock, bridge, tmp_path):
    sent = _send(db, clock, recipient_id="opencode", message_type="question", body="¿Qué firma tiene login()?")
    db.close()
    reopened = open_database(tmp_path / "bridge.db")  # "otro proceso", más tarde
    (item,) = _inbox(reopened, clock)
    assert item.message == sent
    assert item.trust == "untrusted_peer_content"
    reopened.close()


def test_broadcast_reaches_others_but_not_sender(db, clock, bridge):
    _send(db, clock, body="aviso general")
    assert len(_inbox(db, clock)) == 1
    assert _inbox(db, clock, agent="claude") == []


def test_message_stays_until_acknowledged(db, clock, bridge):
    sent = _send(db, clock, recipient_id="opencode")
    (first,) = _inbox(db, clock)
    clock.advance(5)
    (second,) = _inbox(db, clock)
    assert first.delivered_at == second.delivered_at  # la primera entrega no se sobrescribe
    receipt = _ack(db, clock, sent.id)
    clock.advance(5)
    assert _ack(db, clock, sent.id) == receipt  # idempotente
    assert _inbox(db, clock) == []
    assert len(_inbox(db, clock, unread_only=False)) == 1


def test_acknowledge_without_reading_marks_delivered(db, clock, bridge):
    sent = _send(db, clock, recipient_id="opencode")
    receipt = _ack(db, clock, sent.id)
    assert receipt.delivered_at == receipt.acknowledged_at


def test_cannot_acknowledge_message_for_someone_else(db, clock, bridge):
    sent = _send(db, clock, sender="opencode", recipient_id="claude")
    with pytest.raises(PermissionDeniedError):
        _ack(db, clock, sent.id, agent="opencode")
    with pytest.raises(NotFoundError):
        _ack(db, clock, 999)


def test_sender_sees_status_without_overclaiming(db, clock, bridge):
    sent = _send(db, clock, recipient_id="opencode")
    assert messaging.get_message_status(db, project_id=P, message_id=sent.id, agent_id="claude") == []
    _inbox(db, clock)
    (receipt,) = messaging.get_message_status(db, project_id=P, message_id=sent.id, agent_id="claude")
    assert receipt.delivered_at and receipt.acknowledged_at is None
    with pytest.raises(PermissionDeniedError):
        messaging.get_message_status(db, project_id=P, message_id=sent.id, agent_id="opencode")


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"recipient_id": "claude"}, InvalidInputError),
        ({"recipient_id": "fantasma"}, NotFoundError),
        ({"message_type": "orden"}, InvalidInputError),
        ({"body": " "}, InvalidInputError),
        ({"body": "x" * (messaging.MAX_BODY + 1)}, InvalidInputError),
        ({"task_id": 999}, NotFoundError),
    ],
)
def test_publish_validation(db, clock, bridge, kwargs, error):
    with pytest.raises(error):
        _send(db, clock, **kwargs)


def test_inbox_pagination_and_count(db, clock, bridge):
    for i in range(5):
        _send(db, clock, body=f"m{i}", recipient_id="opencode")
    assert messaging.count_unacknowledged(db, project_id=P, agent_id="opencode") == 5
    page = _inbox(db, clock, limit=2)
    assert [m.message.body for m in page] == ["m0", "m1"]
    for m in page:
        _ack(db, clock, m.message.id)
    assert [m.message.body for m in _inbox(db, clock, limit=2)] == ["m2", "m3"]

from __future__ import annotations

import pytest

from agentbridge.errors import ConflictError, InvalidInputError, PermissionDeniedError
from agentbridge.services import audit, reservations, tasks

P = "demo"


def _reserve(db, clock, paths, agent="claude", **kwargs):
    return reservations.reserve_paths(db, clock, project_id=P, agent_id=agent, paths=paths, **kwargs)


def test_overlapping_reservation_by_other_agent_is_rejected(db, clock, bridge):
    _reserve(db, clock, ["src/auth/"])
    with pytest.raises(ConflictError) as err:
        _reserve(db, clock, ["src/auth/login.py"], agent="opencode")
    (conflict,) = err.value.details["conflicts"]
    assert conflict["agent_id"] == "claude" and conflict["path"] == "src/auth/"


def test_reservation_is_all_or_nothing(db, clock, bridge):
    _reserve(db, clock, ["src/a.py"])
    with pytest.raises(ConflictError):
        _reserve(db, clock, ["docs/b.md", "src/a.py"], agent="opencode")
    assert [r.path for r in reservations.list_reservations(db, clock, project_id=P)] == ["src/a.py"]


def test_non_overlapping_paths_coexist(db, clock, bridge):
    _reserve(db, clock, ["src/auth/"])
    _reserve(db, clock, ["src/authz.py", "docs/"], agent="opencode")
    assert len(reservations.list_reservations(db, clock, project_id=P)) == 3


def test_override_requires_reason_and_is_audited(db, clock, bridge):
    _reserve(db, clock, ["src/a.py"])
    with pytest.raises(InvalidInputError):
        _reserve(db, clock, ["src/a.py"], agent="opencode", override_reason="porque")
    result = _reserve(db, clock, ["src/a.py"], agent="opencode", override_reason="hotfix urgente acordado por mensaje")
    assert result.reservations[0].override_reason
    event = audit.list_events(db, project_id=P)[-1]
    assert event.event_type == "reservation.override"
    assert event.payload["conflicts"][0]["agent_id"] == "claude"


def test_same_agent_rereserving_renews_instead_of_duplicating(db, clock, bridge):
    first = _reserve(db, clock, ["src/a.py"], ttl_seconds=60).reservations[0]
    clock.advance(30)
    second = _reserve(db, clock, ["src/a.py"], ttl_seconds=600).reservations[0]
    assert second.id == first.id and second.expires_at > first.expires_at


def test_expired_reservation_stops_blocking_and_is_audited(db, clock, bridge):
    first = _reserve(db, clock, ["src/a.py"], ttl_seconds=60).reservations[0]
    clock.advance(61)
    assert reservations.list_reservations(db, clock, project_id=P) == []
    _reserve(db, clock, ["src/a.py"], agent="opencode")
    expired = [e for e in audit.list_events(db, project_id=P) if e.event_type == "reservation.expired"]
    assert [e.entity_id for e in expired] == [str(first.id)]
    # El vencimiento se audita una sola vez.
    _reserve(db, clock, ["docs/"], agent="opencode")
    assert len([e for e in audit.list_events(db, project_id=P) if e.event_type == "reservation.expired"]) == 1


def test_cannot_release_or_renew_another_agents_reservation(db, clock, bridge):
    res = _reserve(db, clock, ["src/a.py"]).reservations[0]
    with pytest.raises(PermissionDeniedError):
        reservations.release_reservation(db, clock, project_id=P, reservation_id=res.id, agent_id="opencode")
    with pytest.raises(PermissionDeniedError):
        reservations.renew_reservation(db, clock, project_id=P, reservation_id=res.id, agent_id="opencode")
    assert reservations.list_reservations(db, clock, project_id=P)[0].id == res.id


def test_release_is_idempotent(db, clock, bridge):
    res = _reserve(db, clock, ["src/a.py"]).reservations[0]
    once = reservations.release_reservation(db, clock, project_id=P, reservation_id=res.id, agent_id="claude")
    twice = reservations.release_reservation(db, clock, project_id=P, reservation_id=res.id, agent_id="claude")
    assert once.released_at == twice.released_at is not None


def test_renewing_expired_reservation_is_rejected(db, clock, bridge):
    res = _reserve(db, clock, ["src/a.py"], ttl_seconds=60).reservations[0]
    renewed = reservations.renew_reservation(
        db, clock, project_id=P, reservation_id=res.id, agent_id="claude", ttl_seconds=120
    )
    assert renewed.expires_at > res.expires_at
    clock.advance(121)
    with pytest.raises(ConflictError, match="venció"):
        reservations.renew_reservation(db, clock, project_id=P, reservation_id=res.id, agent_id="claude")


@pytest.mark.parametrize("ttl", [0, 29, reservations.MAX_TTL_SECONDS + 1])
def test_ttl_bounds(db, clock, bridge, ttl):
    with pytest.raises(InvalidInputError):
        _reserve(db, clock, ["src/a.py"], ttl_seconds=ttl)


@pytest.mark.parametrize("bad", ["../fuera.py", "/etc/passwd", "src/*.py", ".git/config"])
def test_paths_outside_repo_are_rejected(db, clock, bridge, bad):
    with pytest.raises(InvalidInputError):
        _reserve(db, clock, [bad])


def test_symlink_escape_is_rejected(db, clock, bridge, repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InvalidInputError, match="sale del repositorio"):
        _reserve(db, clock, ["link/x.py"])


def test_task_reservation_requires_task_ownership_and_warns_outside_scope(db, clock, bridge):
    task = tasks.create_task(db, clock, project_id=P, actor_id="claude", title="t", allowed_paths=["src/auth/"])
    with pytest.raises(PermissionDeniedError):
        _reserve(db, clock, ["src/auth/x.py"], task_id=task.id)
    tasks.claim_task(db, clock, project_id=P, task_id=task.id, agent_id="claude")
    result = _reserve(db, clock, ["src/auth/x.py", "docs/y.md"], task_id=task.id)
    assert len(result.warnings) == 1 and "docs/y.md" in result.warnings[0]


def test_find_conflicts_and_filtered_listing(db, clock, bridge):
    _reserve(db, clock, ["src/auth/"])
    _reserve(db, clock, ["docs/"], agent="opencode")
    found = reservations.find_conflicts(db, clock, project_id=P, agent_id="opencode", paths=["src/auth/a.py"])
    assert [r.path for r in found] == ["src/auth/"]
    assert reservations.find_conflicts(db, clock, project_id=P, agent_id="claude", paths=["src/auth/a.py"]) == []
    filtered = reservations.list_reservations(db, clock, project_id=P, paths=["docs/readme.md"])
    assert [r.agent_id for r in filtered] == ["opencode"]

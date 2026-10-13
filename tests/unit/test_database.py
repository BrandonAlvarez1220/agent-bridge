from __future__ import annotations

import sqlite3

import pytest

from agentbridge.clock import FakeClock, from_iso, to_iso
from agentbridge.storage.database import migrate, open_database, schema_version, transaction


def test_migrate_is_idempotent(tmp_path):
    conn = open_database(tmp_path / "a.db")
    assert schema_version(conn) == 1
    assert migrate(conn) == 1


def test_wal_and_foreign_keys_enabled(db):
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_transaction_rolls_back_on_error(db, project, clock):
    with pytest.raises(RuntimeError), transaction(db):
        db.execute(
            "INSERT INTO tasks (project_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (project, "t", to_iso(clock()), to_iso(clock())),
        )
        raise RuntimeError("boom")
    assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_foreign_key_rejects_unknown_agent(db, project, clock):
    now = to_iso(clock())
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO messages (project_id, sender_id, message_type, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (project, "fantasma", "info", "hola", now),
        )


def test_in_progress_task_requires_assignee(db, project, clock):
    now = to_iso(clock())
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO tasks (project_id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (project, "t", "in_progress", now, now),
        )


def test_open_task_cannot_have_assignee(db, project, clock):
    now = to_iso(clock())
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO tasks (project_id, title, status, assignee_id, created_at, updated_at)"
            " VALUES (?, ?, 'open', 'claude', ?, ?)",
            (project, "t", now, now),
        )


def test_task_cannot_depend_on_itself(db, project, clock):
    now = to_iso(clock())
    task_id = db.execute(
        "INSERT INTO tasks (project_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (project, "t", now, now),
    ).lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO task_dependencies VALUES (?, ?)", (task_id, task_id))


def test_audit_events_are_append_only(db, project, clock):
    now = to_iso(clock())
    event_id = db.execute(
        "INSERT INTO audit_events (project_id, event_type, entity_type, created_at) VALUES (?, ?, ?, ?)",
        (project, "test", "project", now),
    ).lastrowid
    with pytest.raises(sqlite3.IntegrityError, match="solo inserción"):
        db.execute("UPDATE audit_events SET event_type = 'x' WHERE id = ?", (event_id,))
    with pytest.raises(sqlite3.IntegrityError, match="solo inserción"):
        db.execute("DELETE FROM audit_events WHERE id = ?", (event_id,))


def test_iso_timestamps_sort_chronologically():
    clock = FakeClock()
    early = to_iso(clock())
    clock.advance(0.5)
    later = to_iso(clock())
    assert early < later
    assert from_iso(later) == clock()

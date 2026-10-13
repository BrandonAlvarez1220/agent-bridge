from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agentbridge.clock import FakeClock, to_iso
from agentbridge.storage.database import open_database


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db(tmp_path) -> Iterator[sqlite3.Connection]:
    conn = open_database(tmp_path / "bridge.db")
    yield conn
    conn.close()


@pytest.fixture
def project(db: sqlite3.Connection, clock: FakeClock) -> str:
    now = to_iso(clock())
    db.execute(
        "INSERT INTO projects (id, name, repository_root, created_at) VALUES (?, ?, ?, ?)",
        ("demo", "Demo", "/repo/demo", now),
    )
    for agent in ("claude", "opencode"):
        db.execute(
            "INSERT INTO agents (project_id, id, agent_type, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
            ("demo", agent, agent, now, now),
        )
    return "demo"

"""Demuestra con procesos reales que el patrón de reclamación atómica funciona.

Cada proceso imita a un servidor MCP stdio distinto (ADR-0001): conexión propia
al mismo archivo SQLite. Todos intentan reclamar la misma tarea a la vez.
"""

from __future__ import annotations

import multiprocessing as mp

from agentbridge.clock import FakeClock, to_iso
from agentbridge.storage.database import connect, open_database, transaction

WORKERS = 8


def _claim(db_path: str, agent_id: str, task_id: int, barrier, results) -> None:
    conn = connect(db_path)
    barrier.wait()  # todos arrancan al mismo tiempo
    with transaction(conn):
        cursor = conn.execute(
            "UPDATE tasks SET status = 'in_progress', assignee_id = ?"
            " WHERE id = ? AND assignee_id IS NULL AND status = 'open'",
            (agent_id, task_id),
        )
        results.put((agent_id, cursor.rowcount))
    conn.close()


def test_concurrent_claims_have_exactly_one_winner(tmp_path):
    db_path = str(tmp_path / "race.db")
    conn = open_database(db_path)
    now = to_iso(FakeClock()())
    conn.execute("INSERT INTO projects (id, name, repository_root, created_at) VALUES ('p', 'P', '/r', ?)", (now,))
    agents = [f"agent{i}" for i in range(WORKERS)]
    for agent in agents:
        conn.execute(
            "INSERT INTO agents (project_id, id, agent_type, created_at, last_seen_at) VALUES ('p', ?, 'test', ?, ?)",
            (agent, now, now),
        )
    task_id = conn.execute(
        "INSERT INTO tasks (project_id, title, created_at, updated_at) VALUES ('p', 'carrera', ?, ?)",
        (now, now),
    ).lastrowid
    conn.close()

    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(WORKERS)
    results = ctx.Queue()
    procs = [ctx.Process(target=_claim, args=(db_path, a, task_id, barrier, results)) for a in agents]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    outcomes = [results.get(timeout=5) for _ in agents]
    winners = [agent for agent, rows in outcomes if rows == 1]
    assert len(winners) == 1

    conn = connect(db_path)
    row = conn.execute("SELECT assignee_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert row["assignee_id"] == winners[0]

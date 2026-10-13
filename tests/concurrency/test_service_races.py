"""Pruebas de aceptación 3 y 4 con procesos reales compitiendo vía los servicios.

Cada proceso imita un servidor MCP stdio distinto (ADR-0001): conexión propia
al mismo archivo SQLite y reloj del sistema.
"""

from __future__ import annotations

import multiprocessing as mp

from agentbridge.clock import system_clock
from agentbridge.domain.paths import normalize
from agentbridge.errors import ConflictError
from agentbridge.services import projects, reservations, tasks
from agentbridge.storage.database import connect, open_database

WORKERS = 8
AGENTS = [f"agent{i}" for i in range(WORKERS)]


def _setup(tmp_path) -> str:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    db_path = str(tmp_path / "race.db")
    conn = open_database(db_path)
    projects.register_project(conn, system_clock, project_id="p", name="P", repository_root=repo)
    for agent in AGENTS:
        projects.register_agent(conn, system_clock, project_id="p", agent_id=agent, agent_type="test")
    conn.close()
    return db_path


def _run(target, args_per_worker) -> list[tuple[str, str]]:
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(WORKERS)
    results = ctx.Queue()
    procs = [ctx.Process(target=target, args=(*args, barrier, results)) for args in args_per_worker]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0
    return [results.get(timeout=5) for _ in procs]


def _claim_worker(db_path, agent, task_id, barrier, results):
    conn = connect(db_path)
    barrier.wait()
    try:
        tasks.claim_task(conn, system_clock, project_id="p", task_id=task_id, agent_id=agent)
        results.put((agent, "won"))
    except ConflictError:
        results.put((agent, "conflict"))
    finally:
        conn.close()


def test_concurrent_claim_task_has_exactly_one_winner(tmp_path):
    db_path = _setup(tmp_path)
    conn = connect(db_path)
    task = tasks.create_task(conn, system_clock, project_id="p", actor_id="agent0", title="carrera")
    conn.close()

    outcomes = _run(_claim_worker, [(db_path, a, task.id) for a in AGENTS])
    winners = [a for a, r in outcomes if r == "won"]
    assert len(winners) == 1
    assert sorted(r for _, r in outcomes) == ["conflict"] * (WORKERS - 1) + ["won"]

    conn = connect(db_path)
    assert tasks.get_task(conn, system_clock, project_id="p", task_id=task.id).assignee_id == winners[0]


def _reserve_worker(db_path, agent, path, barrier, results):
    conn = connect(db_path)
    barrier.wait()
    try:
        reservations.reserve_paths(conn, system_clock, project_id="p", agent_id=agent, paths=[path])
        results.put((agent, "won"))
    except ConflictError:
        results.put((agent, "conflict"))
    finally:
        conn.close()


def test_concurrent_overlapping_reservations_have_exactly_one_winner(tmp_path):
    db_path = _setup(tmp_path)
    # Cadena de prefijos: cualquier par de estas rutas se solapa.
    chain = ["src/", "src/auth/", "src/auth/login.py"]
    paths = [chain[i % len(chain)] for i in range(WORKERS)]

    outcomes = _run(_reserve_worker, [(db_path, a, p) for a, p in zip(AGENTS, paths, strict=True)])
    assert [r for _, r in outcomes].count("won") == 1

    conn = connect(db_path)
    assert len(reservations.list_reservations(conn, system_clock, project_id="p")) == 1


def test_concurrent_mixed_reservations_never_leave_overlaps(tmp_path):
    """Invariante general: tras la carrera, ningún par de reservas activas de agentes distintos se solapa."""
    db_path = _setup(tmp_path)
    paths = ["src/", "src/auth/", "src/auth/login.py", "src/a.py", "src/b/", "docs/", "docs/x.md", "README.md"]

    outcomes = _run(_reserve_worker, [(db_path, a, p) for a, p in zip(AGENTS, paths, strict=True)])
    winners = {a for a, r in outcomes if r == "won"}

    conn = connect(db_path)
    active = reservations.list_reservations(conn, system_clock, project_id="p")
    assert {r.agent_id for r in active} == winners
    for i, a in enumerate(active):
        for b in active[i + 1 :]:
            assert not normalize(a.path).overlaps(normalize(b.path)), (a.path, b.path)
    # README.md no choca con nada: siempre gana.
    assert "agent7" in winners

"""Decisiones de arquitectura compartidas entre agentes.

Una decisión no se edita: si cambia, se registra otra que la reemplaza
(``supersedes``). Al registrarla se publica un broadcast para que los demás
agentes se enteren en su próxima lectura de bandeja.
"""

from __future__ import annotations

import json
import sqlite3

from agentbridge.clock import Clock, to_iso
from agentbridge.domain.models import Decision
from agentbridge.domain.paths import normalize_many
from agentbridge.errors import ConflictError, InvalidInputError, NotFoundError
from agentbridge.services import audit, projects
from agentbridge.storage.database import transaction

MAX_TEXT = 20_000
MAX_SEARCH_LIMIT = 100


def record_decision(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    agent_id: str,
    title: str,
    decision: str,
    rationale: str,
    affected_paths: list[str] | None = None,
    task_id: int | None = None,
    supersedes: int | None = None,
) -> Decision:
    title = title.strip()
    if not 1 <= len(title) <= 200:
        raise InvalidInputError("title debe tener entre 1 y 200 caracteres")
    for name, text in (("decision", decision), ("rationale", rationale)):
        if not text.strip():
            raise InvalidInputError(f"{name} no puede estar vacío")
        if len(text) > MAX_TEXT:
            raise InvalidInputError(f"{name} supera {MAX_TEXT} caracteres")
    paths = [str(p) for p in normalize_many(affected_paths or [])]

    with transaction(conn):
        projects.touch_agent(conn, clock, project_id, agent_id)
        if (
            task_id is not None
            and not conn.execute(
                "SELECT 1 FROM tasks WHERE id = ? AND project_id = ?", (task_id, project_id)
            ).fetchone()
        ):
            raise NotFoundError("tarea no encontrada en el proyecto", task_id=task_id)
        if supersedes is not None:
            old = conn.execute(
                "SELECT status FROM decisions WHERE id = ? AND project_id = ?", (supersedes, project_id)
            ).fetchone()
            if old is None:
                raise NotFoundError("decisión a reemplazar no encontrada", decision_id=supersedes)
            if old["status"] != "active":
                raise ConflictError("esa decisión ya fue reemplazada", decision_id=supersedes)

        now = to_iso(clock())
        decision_id = int(
            conn.execute(
                "INSERT INTO decisions (project_id, title, decision, rationale, affected_paths, task_id,"
                " author_agent_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, title, decision, rationale, json.dumps(paths), task_id, agent_id, now),
            ).lastrowid
        )
        if supersedes is not None:
            conn.execute(
                "UPDATE decisions SET status = 'superseded', superseded_by = ? WHERE id = ?", (decision_id, supersedes)
            )
        notice = f"Nueva decisión #{decision_id}: {title}"
        if supersedes is not None:
            notice += f" (reemplaza a #{supersedes})"
        if paths:
            notice += f". Rutas afectadas: {', '.join(paths)}"
        conn.execute(
            "INSERT INTO messages (project_id, sender_id, recipient_id, task_id, message_type, body, created_at)"
            " VALUES (?, ?, NULL, ?, 'info', ?, ?)",
            (project_id, agent_id, task_id, notice[:8000], now),
        )
        audit.record(
            conn,
            clock,
            project_id=project_id,
            actor_id=agent_id,
            event_type="decision.recorded",
            entity_type="decision",
            entity_id=decision_id,
            payload={"title": title, "supersedes": supersedes, "affected_paths": paths},
        )
        return Decision.from_row(conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone())


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_decisions(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    query: str | None = None,
    include_superseded: bool = False,
    limit: int = 20,
) -> list[Decision]:
    """Búsqueda textual simple (sin semántica en el MVP). Más recientes primero."""
    if not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise InvalidInputError(f"limit debe estar entre 1 y {MAX_SEARCH_LIMIT}")
    projects.get_project(conn, project_id)
    sql = "SELECT * FROM decisions WHERE project_id = ?"
    params: list[object] = [project_id]
    if not include_superseded:
        sql += " AND status = 'active'"
    if query and query.strip():
        pattern = f"%{_escape_like(query.strip())}%"
        sql += (
            " AND (title LIKE ? ESCAPE '\\' OR decision LIKE ? ESCAPE '\\'"
            " OR rationale LIKE ? ESCAPE '\\' OR affected_paths LIKE ? ESCAPE '\\')"
        )
        params += [pattern] * 4
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [Decision.from_row(r) for r in conn.execute(sql, params).fetchall()]

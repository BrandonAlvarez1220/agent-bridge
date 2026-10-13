"""Tareas: creación, dependencias, reclamación atómica con lease y transiciones.

Máquina de estados (docs/DATA_MODEL.md):

    open ──claim──► in_progress ──report_completion──► review ──approve──► done
      ▲                │  ▲                                │
      └──release───────┤  └──unblock── blocked             └──request_changes──► in_progress
                       └──block──────────►
    open / in_progress / blocked / review ──cancel──► cancelled

``ready`` no se guarda: una tarea está lista si está ``open`` y todas sus
dependencias están en ``done`` (ADR-0008).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

from agentbridge.clock import Clock, to_iso
from agentbridge.domain.models import Task, TaskReport
from agentbridge.domain.paths import normalize, normalize_many
from agentbridge.errors import ConflictError, InvalidInputError, NotFoundError, PermissionDeniedError
from agentbridge.services import audit, projects, reservations
from agentbridge.storage.database import transaction

DEFAULT_LEASE_SECONDS = 30 * 60
MIN_LEASE_SECONDS = 60
MAX_LEASE_SECONDS = 8 * 60 * 60
MAX_DESCRIPTION = 20_000
MAX_DEPENDENCIES = 50
MAX_REPORT_TEXT = 20_000
MAX_LIST_ITEMS = 500

STATUSES = ("open", "in_progress", "blocked", "review", "done", "cancelled")
ACTIVE = ("in_progress", "blocked")
FINAL = ("done", "cancelled")

# (estado actual, estado destino) -> quién puede hacerlo.
#   "owner": solo el agente asignado.  "any": cualquier agente registrado.
# A 'review' solo se llega con report_completion, para obligar a reportar.
_TRANSITIONS: dict[tuple[str, str], str] = {
    ("in_progress", "blocked"): "owner",
    ("blocked", "in_progress"): "owner",
    ("in_progress", "open"): "owner",  # soltar la tarea
    ("blocked", "open"): "owner",
    ("in_progress", "cancelled"): "owner",
    ("blocked", "cancelled"): "owner",
    ("open", "cancelled"): "any",
    ("review", "done"): "any",  # aprobación del integrador
    ("review", "in_progress"): "any",  # se piden cambios; vuelve al mismo dueño
    ("review", "cancelled"): "any",
}


def _validate_lease(lease_seconds: int) -> None:
    if not MIN_LEASE_SECONDS <= lease_seconds <= MAX_LEASE_SECONDS:
        raise InvalidInputError(f"lease_seconds debe estar entre {MIN_LEASE_SECONDS} y {MAX_LEASE_SECONDS}")


def _row(conn: sqlite3.Connection, project_id: str, task_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE id = ? AND project_id = ?", (task_id, project_id)).fetchone()
    if row is None:
        raise NotFoundError("tarea no encontrada en el proyecto", task_id=task_id)
    return row


def _dependency_status(conn: sqlite3.Connection, task_id: int) -> tuple[list[int], list[int]]:
    rows = conn.execute(
        "SELECT d.depends_on_task_id AS id, t.status FROM task_dependencies d"
        " JOIN tasks t ON t.id = d.depends_on_task_id WHERE d.task_id = ? ORDER BY d.depends_on_task_id",
        (task_id,),
    ).fetchall()
    deps = [r["id"] for r in rows]
    blocked_by = [r["id"] for r in rows if r["status"] != "done"]
    return deps, blocked_by


def _to_model(conn: sqlite3.Connection, row: sqlite3.Row, now: str) -> Task:
    deps, blocked_by = _dependency_status(conn, row["id"])
    allowed = [
        r["path"]
        for r in conn.execute("SELECT path FROM task_allowed_paths WHERE task_id = ? ORDER BY path", (row["id"],))
    ]
    lease = row["lease_expires_at"]
    return Task(
        **{k: v for k, v in dict(row).items() if k != "lease_expires_at"},
        lease_expires_at=lease,
        lease_expired=lease is not None and lease <= now,
        ready=row["status"] == "open" and not blocked_by,
        dependencies=deps,
        blocked_by=blocked_by,
        allowed_paths=allowed,
    )


def get_task(conn: sqlite3.Connection, clock: Clock, *, project_id: str, task_id: int) -> Task:
    return _to_model(conn, _row(conn, project_id, task_id), to_iso(clock()))


def _would_create_cycle(conn: sqlite3.Connection, task_id: int, depends_on: int) -> bool:
    """¿``depends_on`` ya depende (directa o transitivamente) de ``task_id``?

    Si es así, añadir task_id → depends_on cerraría un ciclo. La CTE recursiva
    recorre el grafo desde ``depends_on`` siguiendo sus dependencias.
    """
    row = conn.execute(
        """
        WITH RECURSIVE reach(id) AS (
            SELECT ?
            UNION
            SELECT d.depends_on_task_id FROM task_dependencies d JOIN reach r ON d.task_id = r.id
        )
        SELECT 1 FROM reach WHERE id = ? LIMIT 1
        """,
        (depends_on, task_id),
    ).fetchone()
    return row is not None


def _insert_dependencies(conn: sqlite3.Connection, project_id: str, task_id: int, depends_on: list[int]) -> None:
    for dep in depends_on:
        _row(conn, project_id, dep)  # existe y es del mismo proyecto
        if _would_create_cycle(conn, task_id, dep):
            raise ConflictError("la dependencia crearía un ciclo", task_id=task_id, depends_on=dep)
        conn.execute("INSERT OR IGNORE INTO task_dependencies VALUES (?, ?)", (task_id, dep))


def create_task(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    actor_id: str,
    title: str,
    description: str = "",
    priority: int = 2,
    dependencies: list[int] | None = None,
    allowed_paths: list[str] | None = None,
) -> Task:
    title = title.strip()
    if not 1 <= len(title) <= 200:
        raise InvalidInputError("title debe tener entre 1 y 200 caracteres")
    if len(description) > MAX_DESCRIPTION:
        raise InvalidInputError(f"description supera {MAX_DESCRIPTION} caracteres")
    if not 0 <= priority <= 4:
        raise InvalidInputError("priority debe estar entre 0 (máxima) y 4 (mínima)")
    dependencies = list(dict.fromkeys(dependencies or []))
    if len(dependencies) > MAX_DEPENDENCIES:
        raise InvalidInputError(f"máximo {MAX_DEPENDENCIES} dependencias")
    paths = normalize_many(allowed_paths or [])

    with transaction(conn):
        projects.touch_agent(conn, clock, project_id, actor_id)
        now = to_iso(clock())
        task_id = int(
            conn.execute(
                "INSERT INTO tasks (project_id, title, description, priority, created_by, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_id, title, description, priority, actor_id, now, now),
            ).lastrowid
        )
        _insert_dependencies(conn, project_id, task_id, dependencies)
        conn.executemany(
            "INSERT INTO task_allowed_paths (task_id, path) VALUES (?, ?)", [(task_id, str(p)) for p in paths]
        )
        audit.record(
            conn,
            clock,
            project_id=project_id,
            actor_id=actor_id,
            event_type="task.created",
            entity_type="task",
            entity_id=task_id,
            payload={"title": title, "priority": priority, "dependencies": dependencies},
        )
        return _to_model(conn, _row(conn, project_id, task_id), now)


def add_dependency(
    conn: sqlite3.Connection, clock: Clock, *, project_id: str, actor_id: str, task_id: int, depends_on: int
) -> Task:
    with transaction(conn):
        projects.touch_agent(conn, clock, project_id, actor_id)
        row = _row(conn, project_id, task_id)
        if row["status"] in FINAL:
            raise ConflictError("no se agregan dependencias a una tarea terminada", task_id=task_id)
        _insert_dependencies(conn, project_id, task_id, [depends_on])
        audit.record(
            conn,
            clock,
            project_id=project_id,
            actor_id=actor_id,
            event_type="task.dependency_added",
            entity_type="task",
            entity_id=task_id,
            payload={"depends_on": depends_on},
        )
        return _to_model(conn, _row(conn, project_id, task_id), to_iso(clock()))


def list_tasks(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    status: str | None = None,
    assignee_id: str | None = None,
    ready_only: bool = False,
    limit: int = 100,
) -> list[Task]:
    if not 1 <= limit <= MAX_LIST_ITEMS:
        raise InvalidInputError(f"limit debe estar entre 1 y {MAX_LIST_ITEMS}")
    projects.get_project(conn, project_id)
    sql = "SELECT * FROM tasks WHERE project_id = ?"
    params: list[object] = [project_id]
    if status is not None and status not in STATUSES:
        raise InvalidInputError(f"status inválido; valores: {', '.join(STATUSES)}")
    if ready_only:
        status = "open"
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    if assignee_id is not None:
        sql += " AND assignee_id = ?"
        params.append(assignee_id)
    if ready_only:
        sql += (
            " AND NOT EXISTS (SELECT 1 FROM task_dependencies d JOIN tasks dep ON dep.id = d.depends_on_task_id"
            " WHERE d.task_id = tasks.id AND dep.status <> 'done')"
        )
    sql += " ORDER BY priority, id LIMIT ?"
    params.append(limit)
    now = to_iso(clock())
    return [_to_model(conn, r, now) for r in conn.execute(sql, params).fetchall()]


def claim_task(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    task_id: int,
    agent_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> Task:
    """Reclama una tarea lista. Si ya es del agente, renueva el lease (idempotente).

    Una tarea activa de otro agente con el lease vencido se puede tomar; el
    traspaso queda auditado como ``task.lease_expired`` (ADR-0005).
    """
    _validate_lease(lease_seconds)
    with transaction(conn):
        projects.touch_agent(conn, clock, project_id, agent_id)
        row = _row(conn, project_id, task_id)
        now_dt = clock()
        now = to_iso(now_dt)
        lease = to_iso(now_dt + timedelta(seconds=lease_seconds))
        status, owner = row["status"], row["assignee_id"]

        if status in ACTIVE and owner == agent_id:
            conn.execute("UPDATE tasks SET lease_expires_at = ?, updated_at = ? WHERE id = ?", (lease, now, task_id))
            return _to_model(conn, _row(conn, project_id, task_id), now)

        if status == "open":
            _, blocked_by = _dependency_status(conn, task_id)
            if blocked_by:
                raise ConflictError("la tarea tiene dependencias sin terminar", task_id=task_id, blocked_by=blocked_by)
            event, payload = "task.claimed", {}
        elif status in ACTIVE and row["lease_expires_at"] is not None and row["lease_expires_at"] <= now:
            event, payload = "task.lease_expired", {"previous_assignee": owner, "expired_at": row["lease_expires_at"]}
        elif status in ACTIVE:
            raise ConflictError(
                "la tarea ya está reclamada",
                task_id=task_id,
                assignee_id=owner,
                lease_expires_at=row["lease_expires_at"],
            )
        else:
            raise ConflictError(f"no se puede reclamar una tarea en estado '{status}'", task_id=task_id)

        # El WHERE repite lo que se acaba de leer. Con BEGIN IMMEDIATE ya no puede
        # haber cambiado, pero si alguien elimina esa protección, el UPDATE falla
        # cerrado (0 filas) en lugar de pisar a otro agente.
        cursor = conn.execute(
            "UPDATE tasks SET status = 'in_progress', assignee_id = ?, lease_expires_at = ?, updated_at = ?"
            " WHERE id = ? AND status = ? AND assignee_id IS ?",
            (agent_id, lease, now, task_id, status, owner),
        )
        if cursor.rowcount != 1:
            raise ConflictError("la tarea cambió durante la reclamación; vuelve a intentarlo", task_id=task_id)
        audit.record(
            conn,
            clock,
            project_id=project_id,
            actor_id=agent_id,
            event_type=event,
            entity_type="task",
            entity_id=task_id,
            payload={**payload, "lease_expires_at": lease},
        )
        return _to_model(conn, _row(conn, project_id, task_id), now)


def update_task_status(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    task_id: int,
    agent_id: str,
    status: str,
    note: str = "",
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> Task:
    if status not in STATUSES:
        raise InvalidInputError(f"status inválido; valores: {', '.join(STATUSES)}")
    if len(note) > MAX_REPORT_TEXT:
        raise InvalidInputError(f"note supera {MAX_REPORT_TEXT} caracteres")
    _validate_lease(lease_seconds)
    with transaction(conn):
        projects.touch_agent(conn, clock, project_id, agent_id)
        row = _row(conn, project_id, task_id)
        current = row["status"]
        if current == status:
            return _to_model(conn, row, to_iso(clock()))
        who = _TRANSITIONS.get((current, status))
        if who is None:
            hint = " (usa report_completion para pasar a review)" if status == "review" else ""
            raise ConflictError(f"transición no permitida: {current} → {status}{hint}", task_id=task_id)
        if who == "owner" and row["assignee_id"] != agent_id:
            raise PermissionDeniedError(
                "solo el agente asignado puede hacer este cambio", task_id=task_id, assignee_id=row["assignee_id"]
            )

        now_dt = clock()
        now = to_iso(now_dt)
        assignee, lease, completed = row["assignee_id"], row["lease_expires_at"], row["completed_at"]
        if status == "open":
            assignee, lease = None, None
        elif status in ACTIVE:
            lease = to_iso(now_dt + timedelta(seconds=lease_seconds))
        elif status in FINAL or status == "review":
            lease = None
        if status in FINAL:
            completed = now

        conn.execute(
            "UPDATE tasks SET status = ?, assignee_id = ?, lease_expires_at = ?, completed_at = ?, updated_at = ?"
            " WHERE id = ?",
            (status, assignee, lease, completed, now, task_id),
        )
        released = 0
        if status in ("open", *FINAL) and row["assignee_id"] is not None:
            released = reservations.release_for_task(conn, clock, project_id, row["assignee_id"], task_id)
        audit.record(
            conn,
            clock,
            project_id=project_id,
            actor_id=agent_id,
            event_type="task.status_changed",
            entity_type="task",
            entity_id=task_id,
            payload={"from": current, "to": status, "note": note, "reservations_released": released},
        )
        return _to_model(conn, _row(conn, project_id, task_id), now)


def report_completion(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    task_id: int,
    agent_id: str,
    summary: str,
    changed_files: list[str],
    tests_run: list[str],
    test_results: str,
    blockers: str = "",
) -> TaskReport:
    """Registra el informe, pasa la tarea a ``review`` y libera las reservas de la tarea."""
    if not summary.strip():
        raise InvalidInputError("summary no puede estar vacío")
    for name, text in (("summary", summary), ("test_results", test_results), ("blockers", blockers)):
        if len(text) > MAX_REPORT_TEXT:
            raise InvalidInputError(f"{name} supera {MAX_REPORT_TEXT} caracteres")
    if len(changed_files) > MAX_LIST_ITEMS or len(tests_run) > MAX_LIST_ITEMS:
        raise InvalidInputError(f"máximo {MAX_LIST_ITEMS} elementos por lista")
    files = [str(normalize(f)) for f in changed_files]

    with transaction(conn):
        projects.touch_agent(conn, clock, project_id, agent_id)
        row = _row(conn, project_id, task_id)
        if row["assignee_id"] != agent_id:
            raise PermissionDeniedError("solo el agente asignado puede reportar la tarea", task_id=task_id)
        if row["status"] not in ACTIVE:
            raise ConflictError(f"no se puede reportar una tarea en estado '{row['status']}'", task_id=task_id)
        now = to_iso(clock())
        report_id = int(
            conn.execute(
                "INSERT INTO task_reports (task_id, agent_id, summary, changed_files, tests_run, test_results,"
                " blockers, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    agent_id,
                    summary.strip(),
                    json.dumps(files),
                    json.dumps(tests_run),
                    test_results,
                    blockers,
                    now,
                ),
            ).lastrowid
        )
        conn.execute(
            "UPDATE tasks SET status = 'review', lease_expires_at = NULL, updated_at = ? WHERE id = ?", (now, task_id)
        )
        released = reservations.release_for_task(conn, clock, project_id, agent_id, task_id)
        audit.record(
            conn,
            clock,
            project_id=project_id,
            actor_id=agent_id,
            event_type="task.reported",
            entity_type="task",
            entity_id=task_id,
            payload={
                "report_id": report_id,
                "changed_files": files,
                "tests_run": tests_run,
                "has_blockers": bool(blockers.strip()),
                "reservations_released": released,
            },
        )
        report = conn.execute("SELECT * FROM task_reports WHERE id = ?", (report_id,)).fetchone()
        return TaskReport.from_row(report)


def list_reports(conn: sqlite3.Connection, *, project_id: str, task_id: int) -> list[TaskReport]:
    _row(conn, project_id, task_id)
    rows = conn.execute("SELECT * FROM task_reports WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
    return [TaskReport.from_row(r) for r in rows]

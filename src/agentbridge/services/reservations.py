"""Reservas temporales de rutas (ADR-0004).

Una reserva es **coordinación cooperativa**: avisa a los demás agentes que una
ruta está en uso, pero no impide físicamente editarla. La protección real viene
de los worktrees, la revisión del diff y, para Claude Code, de los hooks
(ADR-0009).

Reglas:
* Reservar es todo o nada: si una ruta choca, no se crea ninguna.
* Una reserva activa = no liberada y no vencida. Vencer no requiere un proceso en
  segundo plano: se compara ``expires_at`` con la hora actual en cada consulta.
* Las reservas vencidas se auditan de forma perezosa en la siguiente escritura.
* Un choque solo se puede saltar con ``override_reason``, que queda auditado.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agentbridge.clock import Clock, to_iso
from agentbridge.domain.models import Reservation
from agentbridge.domain.paths import RepoPath, ensure_within, normalize, normalize_many
from agentbridge.errors import ConflictError, InvalidInputError, NotFoundError, PermissionDeniedError
from agentbridge.services import audit, projects
from agentbridge.storage.database import transaction

DEFAULT_TTL_SECONDS = 15 * 60
MIN_TTL_SECONDS = 30
MAX_TTL_SECONDS = 4 * 60 * 60
MAX_PATHS_PER_CALL = 50
MIN_OVERRIDE_REASON = 10


class ReserveResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    reservations: list[Reservation]
    warnings: list[str]


def _validate_ttl(ttl_seconds: int) -> None:
    if not MIN_TTL_SECONDS <= ttl_seconds <= MAX_TTL_SECONDS:
        raise InvalidInputError(f"ttl_seconds debe estar entre {MIN_TTL_SECONDS} y {MAX_TTL_SECONDS}")


def _active_rows(conn: sqlite3.Connection, project_id: str, now: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM reservations WHERE project_id = ? AND released_at IS NULL AND expires_at > ? ORDER BY id",
        (project_id, now),
    ).fetchall()


def expire_due(conn: sqlite3.Connection, clock: Clock, project_id: str) -> int:
    """Audita las reservas que vencieron desde la última escritura. Requiere transacción abierta."""
    now = to_iso(clock())
    rows = conn.execute(
        "SELECT * FROM reservations WHERE project_id = ? AND released_at IS NULL"
        " AND expires_at <= ? AND expired_logged_at IS NULL",
        (project_id, now),
    ).fetchall()
    for row in rows:
        conn.execute("UPDATE reservations SET expired_logged_at = ? WHERE id = ?", (now, row["id"]))
        audit.record(
            conn,
            clock,
            project_id=project_id,
            actor_id=None,
            event_type="reservation.expired",
            entity_type="reservation",
            entity_id=row["id"],
            payload={"agent_id": row["agent_id"], "path": row["path"], "expires_at": row["expires_at"]},
        )
    return len(rows)


def _conflict_view(row: sqlite3.Row) -> dict[str, object]:
    return {
        "reservation_id": row["id"],
        "agent_id": row["agent_id"],
        "path": row["path"],
        "task_id": row["task_id"],
        "expires_at": row["expires_at"],
    }


def _find_conflicts(
    rows: list[sqlite3.Row], agent_id: str, paths: list[RepoPath]
) -> list[tuple[RepoPath, sqlite3.Row]]:
    held = [(normalize(r["path"]), r) for r in rows if r["agent_id"] != agent_id]
    return [(p, row) for p in paths for other, row in held if p.overlaps(other)]


def reserve_paths(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    agent_id: str,
    paths: list[str],
    task_id: int | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    override_reason: str | None = None,
) -> ReserveResult:
    _validate_ttl(ttl_seconds)
    if not paths:
        raise InvalidInputError("paths no puede estar vacío")
    if len(paths) > MAX_PATHS_PER_CALL:
        raise InvalidInputError(f"máximo {MAX_PATHS_PER_CALL} rutas por llamada")
    if override_reason is not None and len(override_reason.strip()) < MIN_OVERRIDE_REASON:
        raise InvalidInputError(f"override_reason debe explicar el motivo (mínimo {MIN_OVERRIDE_REASON} caracteres)")
    requested = normalize_many(paths)

    project = projects.get_project(conn, project_id)
    for path in requested:
        ensure_within(Path(project.repository_root), path)

    with transaction(conn):
        projects.touch_agent(conn, clock, project_id, agent_id)
        warnings = _task_warnings(conn, project_id, agent_id, task_id, requested)
        expire_due(conn, clock, project_id)

        now_dt = clock()
        now = to_iso(now_dt)
        expires = to_iso(now_dt + timedelta(seconds=ttl_seconds))
        active = _active_rows(conn, project_id, now)

        conflicts = _find_conflicts(active, agent_id, requested)
        if conflicts and override_reason is None:
            raise ConflictError(
                "hay rutas reservadas por otros agentes; espera, negocia por mensaje o usa override_reason",
                conflicts=[{"requested": str(p), **_conflict_view(row)} for p, row in conflicts],
            )

        own_exact = {r["path"]: r for r in active if r["agent_id"] == agent_id}
        result_ids: list[int] = []
        for path in requested:
            key = str(path)
            if key in own_exact:
                # Ya la tiene este agente: se renueva en lugar de duplicarla (idempotencia).
                conn.execute(
                    "UPDATE reservations SET expires_at = MAX(expires_at, ?) WHERE id = ?",
                    (expires, own_exact[key]["id"]),
                )
                result_ids.append(own_exact[key]["id"])
                continue
            path_conflicts = [row for p, row in conflicts if p == path]
            cursor = conn.execute(
                "INSERT INTO reservations"
                " (project_id, agent_id, task_id, path, override_reason, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_id, agent_id, task_id, key, override_reason if path_conflicts else None, now, expires),
            )
            reservation_id = int(cursor.lastrowid)
            result_ids.append(reservation_id)
            audit.record(
                conn,
                clock,
                project_id=project_id,
                actor_id=agent_id,
                event_type="reservation.override" if path_conflicts else "reservation.created",
                entity_type="reservation",
                entity_id=reservation_id,
                payload={
                    "path": key,
                    "task_id": task_id,
                    "expires_at": expires,
                    **(
                        {"override_reason": override_reason, "conflicts": [_conflict_view(r) for r in path_conflicts]}
                        if path_conflicts
                        else {}
                    ),
                },
            )
        return ReserveResult(reservations=_get_many(conn, result_ids), warnings=warnings)


def _task_warnings(
    conn: sqlite3.Connection, project_id: str, agent_id: str, task_id: int | None, requested: list[RepoPath]
) -> list[str]:
    if task_id is None:
        return []
    task = conn.execute("SELECT * FROM tasks WHERE id = ? AND project_id = ?", (task_id, project_id)).fetchone()
    if task is None:
        raise NotFoundError("tarea no encontrada en el proyecto", task_id=task_id)
    if task["assignee_id"] != agent_id or task["status"] not in ("in_progress", "blocked"):
        raise PermissionDeniedError("solo el dueño de una tarea activa puede reservar rutas para ella", task_id=task_id)
    allowed = [
        normalize(r["path"])
        for r in conn.execute("SELECT path FROM task_allowed_paths WHERE task_id = ?", (task_id,)).fetchall()
    ]
    if not allowed:
        return []
    # Fuera de las rutas permitidas = la ruta pedida no está contenida en ninguna permitida.
    outside = [str(p) for p in requested if not any(_contains(a, p) for a in allowed)]
    return [f"'{p}' está fuera de allowed_paths de la tarea {task_id}; avisa si el alcance cambió" for p in outside]


def _contains(container: RepoPath, path: RepoPath) -> bool:
    if container.is_dir:
        return path.parts[: len(container.parts)] == container.parts
    return container == path


def _get_many(conn: sqlite3.Connection, ids: list[int]) -> list[Reservation]:
    return [_get(conn, i) for i in ids]


def _get(conn: sqlite3.Connection, reservation_id: int) -> Reservation:
    row = conn.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if row is None:
        raise NotFoundError("reserva no encontrada", reservation_id=reservation_id)
    return Reservation.from_row(row)


def _owned_row(conn: sqlite3.Connection, project_id: str, reservation_id: int, agent_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM reservations WHERE id = ? AND project_id = ?", (reservation_id, project_id)
    ).fetchone()
    if row is None:
        raise NotFoundError("reserva no encontrada", reservation_id=reservation_id)
    if row["agent_id"] != agent_id:
        raise PermissionDeniedError(
            "la reserva pertenece a otro agente", reservation_id=reservation_id, owner=row["agent_id"]
        )
    return row


def list_reservations(
    conn: sqlite3.Connection, clock: Clock, *, project_id: str, paths: list[str] | None = None
) -> list[Reservation]:
    """Reservas activas; con ``paths``, solo las que se solapan con alguna de esas rutas."""
    projects.get_project(conn, project_id)
    rows = _active_rows(conn, project_id, to_iso(clock()))
    if paths:
        wanted = normalize_many(paths)
        rows = [r for r in rows if any(normalize(r["path"]).overlaps(w) for w in wanted)]
    return [Reservation.from_row(r) for r in rows]


def find_conflicts(
    conn: sqlite3.Connection, clock: Clock, *, project_id: str, agent_id: str, paths: list[str]
) -> list[Reservation]:
    """Reservas activas de **otros** agentes que chocan con ``paths``. Pensado para hooks."""
    rows = _active_rows(conn, project_id, to_iso(clock()))
    return [Reservation.from_row(row) for _, row in _find_conflicts(rows, agent_id, normalize_many(paths))]


def renew_reservation(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    reservation_id: int,
    agent_id: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Reservation:
    _validate_ttl(ttl_seconds)
    with transaction(conn):
        projects.touch_agent(conn, clock, project_id, agent_id)
        row = _owned_row(conn, project_id, reservation_id, agent_id)
        now_dt = clock()
        if row["released_at"] is not None:
            raise ConflictError("la reserva ya fue liberada; crea una nueva", reservation_id=reservation_id)
        if row["expires_at"] <= to_iso(now_dt):
            # Otro agente pudo haber tomado la ruta mientras estaba vencida: no se revive en silencio.
            raise ConflictError("la reserva venció; vuelve a reservar con reserve_paths", reservation_id=reservation_id)
        expires = to_iso(now_dt + timedelta(seconds=ttl_seconds))
        conn.execute("UPDATE reservations SET expires_at = ? WHERE id = ?", (expires, reservation_id))
        audit.record(
            conn,
            clock,
            project_id=project_id,
            actor_id=agent_id,
            event_type="reservation.renewed",
            entity_type="reservation",
            entity_id=reservation_id,
            payload={"expires_at": expires},
        )
        return _get(conn, reservation_id)


def release_reservation(
    conn: sqlite3.Connection, clock: Clock, *, project_id: str, reservation_id: int, agent_id: str
) -> Reservation:
    """Libera una reserva propia. Liberar dos veces no es error (idempotente)."""
    with transaction(conn):
        projects.touch_agent(conn, clock, project_id, agent_id)
        row = _owned_row(conn, project_id, reservation_id, agent_id)
        if row["released_at"] is None:
            _release_row(conn, clock, project_id, agent_id, row, reason="released")
        return _get(conn, reservation_id)


def _release_row(
    conn: sqlite3.Connection, clock: Clock, project_id: str, agent_id: str, row: sqlite3.Row, *, reason: str
) -> None:
    conn.execute("UPDATE reservations SET released_at = ? WHERE id = ?", (to_iso(clock()), row["id"]))
    audit.record(
        conn,
        clock,
        project_id=project_id,
        actor_id=agent_id,
        event_type="reservation.released",
        entity_type="reservation",
        entity_id=row["id"],
        payload={"path": row["path"], "reason": reason},
    )


def release_for_task(conn: sqlite3.Connection, clock: Clock, project_id: str, agent_id: str, task_id: int) -> int:
    """Libera las reservas del agente asociadas a una tarea. Requiere transacción abierta."""
    rows = conn.execute(
        "SELECT * FROM reservations WHERE project_id = ? AND agent_id = ? AND task_id = ? AND released_at IS NULL",
        (project_id, agent_id, task_id),
    ).fetchall()
    for row in rows:
        _release_row(conn, clock, project_id, agent_id, row, reason=f"task {task_id} finished")
    return len(rows)

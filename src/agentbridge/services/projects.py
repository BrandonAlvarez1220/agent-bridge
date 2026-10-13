"""Registro de proyectos y agentes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agentbridge.clock import Clock, to_iso
from agentbridge.domain.identifiers import validate_id
from agentbridge.domain.models import Agent, Project
from agentbridge.errors import ConflictError, InvalidInputError, NotFoundError
from agentbridge.services import audit
from agentbridge.storage.database import transaction

MAX_RULES_LENGTH = 20_000


def _resolve_repository_root(raw: str | Path) -> Path:
    root = Path(raw).expanduser()
    if not root.is_absolute():
        raise InvalidInputError("repository_root debe ser una ruta absoluta", path=str(raw))
    if not root.is_dir():
        raise InvalidInputError("repository_root no existe o no es un directorio", path=str(raw))
    # En un repositorio normal '.git' es un directorio; en un worktree es un archivo.
    if not (root / ".git").exists():
        raise InvalidInputError("repository_root no es la raíz de un repositorio Git", path=str(raw))
    return root.resolve()


def register_project(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    name: str,
    repository_root: str | Path,
    default_branch: str = "main",
    rules: str = "",
) -> Project:
    """Registra un proyecto. Repetir la llamada con los mismos datos devuelve el existente."""
    validate_id(project_id, kind="project_id")
    if not name.strip():
        raise InvalidInputError("name no puede estar vacío")
    if len(rules) > MAX_RULES_LENGTH:
        raise InvalidInputError(f"rules supera {MAX_RULES_LENGTH} caracteres")
    root = str(_resolve_repository_root(repository_root))

    with transaction(conn):
        existing = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if existing is not None:
            if existing["repository_root"] != root:
                raise ConflictError(
                    "ya existe un proyecto con ese id y otra raíz",
                    project_id=project_id,
                    repository_root=existing["repository_root"],
                )
            return Project.from_row(existing)
        clash = conn.execute("SELECT id FROM projects WHERE repository_root = ?", (root,)).fetchone()
        if clash is not None:
            raise ConflictError("esa raíz ya está registrada con otro id", project_id=clash["id"])

        conn.execute(
            "INSERT INTO projects (id, name, repository_root, default_branch, rules, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, name.strip(), root, default_branch, rules, to_iso(clock())),
        )
        audit.record(
            conn,
            clock,
            project_id=project_id,
            actor_id=None,
            event_type="project.registered",
            entity_type="project",
            entity_id=project_id,
            payload={"repository_root": root, "default_branch": default_branch},
        )
        return Project.from_row(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())


def get_project(conn: sqlite3.Connection, project_id: str) -> Project:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise NotFoundError("proyecto no registrado", project_id=project_id)
    return Project.from_row(row)


def register_agent(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    agent_id: str,
    agent_type: str,
    workspace: str | Path | None = None,
    branch: str | None = None,
) -> Agent:
    """Registra o actualiza un agente. Idempotente; solo audita altas y cambios reales.

    ``workspace`` o ``branch`` en ``None`` significan "sin cambios", no "borrar".
    Así el servidor, al arrancar, no pisa la rama que registró ``worktree create``.
    """
    validate_id(agent_id, kind="agent_id")
    if not agent_type.strip() or len(agent_type) > 64:
        raise InvalidInputError("agent_type debe tener entre 1 y 64 caracteres")
    workspace_str: str | None = None
    if workspace is not None:
        ws = Path(workspace).expanduser()
        if not ws.is_absolute() or not ws.is_dir():
            raise InvalidInputError("workspace debe ser un directorio absoluto existente", path=str(workspace))
        workspace_str = str(ws.resolve())

    now = to_iso(clock())
    with transaction(conn):
        get_project(conn, project_id)
        existing = conn.execute(
            "SELECT * FROM agents WHERE project_id = ? AND id = ?", (project_id, agent_id)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO agents (project_id, id, agent_type, workspace, branch, created_at, last_seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_id, agent_id, agent_type, workspace_str, branch, now, now),
            )
            event = "agent.registered"
        else:
            workspace_str = workspace_str if workspace_str is not None else existing["workspace"]
            branch = branch if branch is not None else existing["branch"]
            changed = (existing["agent_type"], existing["workspace"], existing["branch"], existing["status"]) != (
                agent_type,
                workspace_str,
                branch,
                "active",
            )
            conn.execute(
                "UPDATE agents SET agent_type = ?, workspace = ?, branch = ?, status = 'active', last_seen_at = ?"
                " WHERE project_id = ? AND id = ?",
                (agent_type, workspace_str, branch, now, project_id, agent_id),
            )
            event = "agent.updated" if changed else None
        if event:
            audit.record(
                conn,
                clock,
                project_id=project_id,
                actor_id=agent_id,
                event_type=event,
                entity_type="agent",
                entity_id=agent_id,
                payload={"agent_type": agent_type, "workspace": workspace_str, "branch": branch},
            )
        return _get_agent(conn, project_id, agent_id)


def _get_agent(conn: sqlite3.Connection, project_id: str, agent_id: str) -> Agent:
    row = conn.execute("SELECT * FROM agents WHERE project_id = ? AND id = ?", (project_id, agent_id)).fetchone()
    if row is None:
        raise NotFoundError("agente no registrado en el proyecto", project_id=project_id, agent_id=agent_id)
    return Agent.from_row(row)


def require_agent(conn: sqlite3.Connection, project_id: str, agent_id: str) -> Agent:
    """Punto de entrada de los demás servicios: falla si el agente no existe."""
    return _get_agent(conn, project_id, agent_id)


def touch_agent(conn: sqlite3.Connection, clock: Clock, project_id: str, agent_id: str) -> None:
    """Actualiza last_seen_at. Se llama dentro de la transacción de cada operación del agente."""
    cursor = conn.execute(
        "UPDATE agents SET last_seen_at = ? WHERE project_id = ? AND id = ?",
        (to_iso(clock()), project_id, agent_id),
    )
    if cursor.rowcount == 0:
        raise NotFoundError("agente no registrado en el proyecto", project_id=project_id, agent_id=agent_id)


def list_agents(conn: sqlite3.Connection, project_id: str) -> list[Agent]:
    get_project(conn, project_id)
    rows = conn.execute("SELECT * FROM agents WHERE project_id = ? ORDER BY id", (project_id,)).fetchall()
    return [Agent.from_row(r) for r in rows]

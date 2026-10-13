"""Worktrees y ramas por agente o por tarea.

Un worktree es una carpeta adicional enlazada al mismo repositorio, con su
propia rama. Cada agente edita su carpeta y nunca la de otro. Los cambios solo
se juntan en la integración (``agentbridge.integration``).

Convención de nombres:
    carpeta: <padre del repo>/<nombre del repo>-<agente>[-t<tarea>]
    rama:    <agente>/work  o  <agente>/task-<id>-<slug-del-título>
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from agentbridge import gitutil
from agentbridge.clock import Clock
from agentbridge.errors import ConflictError, InvalidInputError
from agentbridge.services import audit, projects, tasks
from agentbridge.storage.database import transaction

MAX_SLUG = 40


def slugify(text: str) -> str:
    """'Añadir login con OAuth' → 'anadir-login-con-oauth'."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:MAX_SLUG].rstrip("-") or "tarea"


def validate_branch(repo: Path, branch: str) -> str:
    """Valida un nombre de rama antes de pasarlo a Git.

    Se rechaza un '-' inicial porque Git lo interpretaría como una opción
    (inyección de argumentos), aunque no pase por un shell.
    """
    if not branch or branch.startswith("-"):
        raise InvalidInputError("nombre de rama inválido", branch=branch)
    if gitutil.git(repo, "check-ref-format", "--branch", branch, check=False).returncode != 0:
        raise InvalidInputError("nombre de rama inválido para Git", branch=branch)
    return branch


def branch_exists(repo: Path, branch: str) -> bool:
    return gitutil.git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0


@dataclass(frozen=True)
class WorktreeInfo:
    path: str
    branch: str | None
    head: str
    agent_id: str | None
    dirty: bool
    ahead: int | None
    behind: int | None


@dataclass(frozen=True)
class CreatedWorktree:
    path: Path
    branch: str
    base: str


def create_worktree(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    agent_id: str,
    task_id: int | None = None,
    base: str | None = None,
    path: Path | None = None,
    branch: str | None = None,
) -> CreatedWorktree:
    """Crea carpeta y rama nuevas desde ``base`` y registra el worktree del agente.

    Nunca reutiliza una carpeta o rama existentes: si ya existen, falla. Así no
    puede pisar trabajo sin integrar.
    """
    project = projects.get_project(conn, project_id)
    projects.require_agent(conn, project_id, agent_id)
    repo = Path(project.repository_root)
    base = base or project.default_branch
    validate_branch(repo, base)
    if not branch_exists(repo, base):
        raise InvalidInputError("la rama base no existe", base=base)

    suffix = ""
    if task_id is not None:
        task = tasks.get_task(conn, clock, project_id=project_id, task_id=task_id)
        branch = branch or f"{agent_id}/task-{task.id}-{slugify(task.title)}"
        suffix = f"-t{task.id}"
    branch = validate_branch(repo, branch or f"{agent_id}/work")
    if branch_exists(repo, branch):
        raise ConflictError("la rama ya existe; elige otro nombre o integra la anterior", branch=branch)

    target = (path or repo.parent / f"{repo.name}-{agent_id}{suffix}").expanduser().resolve()
    if target.exists():
        raise ConflictError("la carpeta destino ya existe; no se reutiliza para no pisar trabajo", path=str(target))

    gitutil.git(repo, "worktree", "add", "-b", branch, str(target), base)

    with transaction(conn):
        conn.execute(
            "UPDATE agents SET workspace = ?, branch = ? WHERE project_id = ? AND id = ?",
            (str(target), branch, project_id, agent_id),
        )
        audit.record(
            conn,
            clock,
            project_id=project_id,
            actor_id=agent_id,
            event_type="worktree.created",
            entity_type="agent",
            entity_id=agent_id,
            payload={"path": str(target), "branch": branch, "base": base, "task_id": task_id},
        )
    return CreatedWorktree(path=target, branch=branch, base=base)


def _parse_porcelain(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            if current:
                entries.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        entries.append(current)
    return entries


def list_worktrees(conn: sqlite3.Connection, *, project_id: str) -> list[WorktreeInfo]:
    """Worktrees del repo, con el agente dueño, si hay cambios sin commit y cuánto difieren de la base."""
    project = projects.get_project(conn, project_id)
    repo = Path(project.repository_root)
    owners = {a.workspace: a.id for a in projects.list_agents(conn, project_id) if a.workspace}
    result = []
    for entry in _parse_porcelain(gitutil.git(repo, "worktree", "list", "--porcelain").stdout):
        path = str(Path(entry["worktree"]).resolve())
        branch = entry.get("branch", "").removeprefix("refs/heads/") or None
        status = gitutil.git(Path(path), "status", "--porcelain", check=False)
        ahead = behind = None
        if branch and branch != project.default_branch:
            counts = gitutil.git(
                repo, "rev-list", "--left-right", "--count", f"{project.default_branch}...{branch}", check=False
            )
            if counts.returncode == 0:
                behind, ahead = (int(n) for n in counts.stdout.split())
        result.append(
            WorktreeInfo(
                path=path,
                branch=branch,
                head=entry.get("HEAD", "")[:12],
                agent_id=owners.get(path),
                dirty=status.returncode == 0 and bool(status.stdout.strip()),
                ahead=ahead,
                behind=behind,
            )
        )
    return result

"""Integración controlada: revisar, fusionar en memoria, probar y solo entonces fusionar.

``check_integration`` nunca modifica ramas ni worktrees existentes:

1. ``git merge-tree --write-tree`` fusiona base y rama **en memoria** y devuelve
   el árbol resultante o la lista de conflictos.
2. Si no hay conflictos y hay comando de pruebas, ese árbol se convierte en un
   commit suelto (no queda en ninguna rama) y se saca en un worktree temporal.
   Las pruebas se ejecutan sobre **exactamente lo que se fusionaría**, no sobre la
   rama aislada: dos ramas pueden pasar por separado y fallar juntas.
3. Se cruza el diff con AgentBridge: archivos no declarados en el informe de la
   tarea y archivos reservados por otros agentes.

``merge_branch`` exige una revisión aprobada en la misma llamada, el worktree
principal limpio y en la rama base, y confirmación explícita. Después comprueba
que el árbol fusionado sea idéntico al probado.
"""

from __future__ import annotations

import shlex
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agentbridge import gitutil
from agentbridge.clock import Clock
from agentbridge.domain.paths import normalize
from agentbridge.errors import ConflictError, InvalidInputError
from agentbridge.services import audit, projects, reservations, tasks
from agentbridge.storage.database import transaction
from agentbridge.worktrees import branch_exists, validate_branch

DEFAULT_TEST_TIMEOUT = 15 * 60
OUTPUT_TAIL = 4000
INTEGRATOR_ID = "integrator"
# Identidad del commit temporal de prueba: no entra al historial y no debe
# depender de la configuración de Git del usuario ni hacerse pasar por él.
_PROBE_IDENTITY = {
    "GIT_AUTHOR_NAME": "AgentBridge",
    "GIT_AUTHOR_EMAIL": "agentbridge@localhost",
    "GIT_COMMITTER_NAME": "AgentBridge",
    "GIT_COMMITTER_EMAIL": "agentbridge@localhost",
}


@dataclass
class IntegrationReport:
    branch: str
    base: str
    base_commit: str
    branch_commit: str
    commits_ahead: int
    changed_files: list[str]
    shortstat: str
    merged_tree: str | None = None
    conflicts: list[str] = field(default_factory=list)
    conflict_messages: list[str] = field(default_factory=list)
    test_command: list[str] | None = None
    tests_passed: bool | None = None
    test_output_tail: str = ""
    agent_id: str | None = None
    review_tasks: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.commits_ahead == 0:
            return "nothing_to_integrate"
        if self.conflicts:
            return "conflicts"
        if self.tests_passed is False:
            return "tests_failed"
        if self.tests_passed is None:
            return "ready_without_tests"
        return "ready"

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "verdict": self.verdict}


def _rev(repo: Path, ref: str) -> str:
    return gitutil.git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def _merge_tree(repo: Path, base: str, branch: str) -> tuple[str, list[str], list[str]]:
    """Fusión en memoria. Devuelve (árbol, archivos en conflicto, mensajes de conflicto)."""
    result = gitutil.git(repo, "merge-tree", "--write-tree", "--name-only", "-z", base, branch, check=False)
    if result.returncode not in (0, 1):
        raise InvalidInputError(
            "git merge-tree falló (se requiere Git 2.38 o superior)", stderr=result.stderr.strip()[:300]
        )
    fields = result.stdout.split("\0")
    tree, rest = fields[0].strip(), fields[1:]
    conflicts: list[str] = []
    while rest and rest[0] != "":
        conflicts.append(rest.pop(0))
    messages = [f.strip() for f in rest if f.startswith("CONFLICT")]
    return tree, (conflicts if result.returncode == 1 else []), messages


def _run_tests(repo: Path, commit: str, command: list[str], timeout: int) -> tuple[bool, str]:
    """Saca ``commit`` en un worktree temporal fuera del repo, corre las pruebas y lo elimina."""
    workdir = Path(tempfile.mkdtemp(prefix="agentbridge-integration-"))
    checkout = workdir / "tree"
    try:
        gitutil.git(repo, "worktree", "add", "--detach", str(checkout), commit)
        try:
            # Sin shell: el comando se separa con shlex y se ejecuta como lista.
            proc = subprocess.run(command, cwd=checkout, capture_output=True, text=True, timeout=timeout, check=False)
            output = (proc.stdout + proc.stderr)[-OUTPUT_TAIL:]
            return proc.returncode == 0, output
        except subprocess.TimeoutExpired:
            return False, f"las pruebas superaron el tiempo límite de {timeout} s"
        except FileNotFoundError:
            return False, f"no se encontró el comando de pruebas: {command[0]}"
    finally:
        gitutil.git(repo, "worktree", "remove", "--force", str(checkout), check=False)
        gitutil.git(repo, "worktree", "prune", check=False)
        shutil.rmtree(workdir, ignore_errors=True)


def _worktree_of(repo: Path, branch: str) -> Path | None:
    out = gitutil.git(repo, "worktree", "list", "--porcelain").stdout
    path = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif line == f"branch refs/heads/{branch}" and path is not None:
            return path
    return None


def check_integration(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    branch: str,
    base: str | None = None,
    test_command: str | None = None,
    timeout: int = DEFAULT_TEST_TIMEOUT,
) -> IntegrationReport:
    project = projects.get_project(conn, project_id)
    repo = Path(project.repository_root)
    base = validate_branch(repo, base or project.default_branch)
    branch = validate_branch(repo, branch)
    for ref in (base, branch):
        if not branch_exists(repo, ref):
            raise InvalidInputError("la rama no existe", branch=ref)
    if base == branch:
        raise InvalidInputError("la rama a integrar no puede ser la rama base", branch=branch)

    base_commit, branch_commit = _rev(repo, base), _rev(repo, branch)
    names = gitutil.git(repo, "diff", "--name-only", "-z", f"{base_commit}...{branch_commit}").stdout
    report = IntegrationReport(
        branch=branch,
        base=base,
        base_commit=base_commit,
        branch_commit=branch_commit,
        commits_ahead=int(gitutil.git(repo, "rev-list", "--count", f"{base_commit}..{branch_commit}").stdout),
        changed_files=[n for n in names.split("\0") if n],
        shortstat=gitutil.git(repo, "diff", "--shortstat", f"{base_commit}...{branch_commit}").stdout.strip(),
    )

    holder = _worktree_of(repo, branch)
    if holder is not None and gitutil.git(holder, "status", "--porcelain", check=False).stdout.strip():
        report.warnings.append(f"el worktree {holder} tiene cambios sin commit: no forman parte de esta integración")

    _cross_check(conn, clock, project_id, report)

    if report.commits_ahead:
        report.merged_tree, report.conflicts, report.conflict_messages = _merge_tree(repo, base_commit, branch_commit)

    if test_command and report.commits_ahead and not report.conflicts:
        report.test_command = shlex.split(test_command)
        if not report.test_command:
            raise InvalidInputError("el comando de pruebas está vacío")
        commit = gitutil.git(
            repo,
            "commit-tree",
            report.merged_tree or "",
            "-p",
            base_commit,
            "-p",
            branch_commit,
            "-m",
            f"AgentBridge: prueba de integración de {branch}",
            env=_PROBE_IDENTITY,
        ).stdout.strip()
        report.tests_passed, report.test_output_tail = _run_tests(repo, commit, report.test_command, timeout)

    with transaction(conn):
        audit.record(
            conn,
            clock,
            project_id=project_id,
            actor_id=None,
            event_type="integration.checked",
            entity_type="branch",
            entity_id=branch,
            payload={
                "base": base,
                "verdict": report.verdict,
                "branch_commit": branch_commit,
                "conflicts": report.conflicts,
                "tests_passed": report.tests_passed,
                "warnings": report.warnings,
            },
        )
    return report


def _cross_check(conn: sqlite3.Connection, clock: Clock, project_id: str, report: IntegrationReport) -> None:
    """Compara el diff real con lo que AgentBridge sabe de la rama."""
    agent = next((a for a in projects.list_agents(conn, project_id) if a.branch == report.branch), None)
    if agent is not None:
        report.agent_id = agent.id
        in_review = tasks.list_tasks(conn, clock, project_id=project_id, status="review", assignee_id=agent.id)
        report.review_tasks = [t.id for t in in_review]
        declared: set[str] = set()
        for t in in_review:
            for r in tasks.list_reports(conn, project_id=project_id, task_id=t.id):
                declared.update(r.changed_files)
        undeclared = sorted(set(report.changed_files) - declared)
        if in_review and undeclared:
            report.warnings.append(
                "archivos cambiados que ningún informe de tarea declara: " + ", ".join(undeclared[:20])
            )
        if not in_review:
            report.warnings.append(f"el agente {agent.id} no tiene tareas en review para esta rama")
    else:
        report.warnings.append("ningún agente registrado trabaja en esta rama")

    for res in reservations.list_reservations(conn, clock, project_id=project_id):
        if res.agent_id == report.agent_id:
            continue
        held = normalize(res.path)
        touched = []
        for f in report.changed_files:
            try:
                if normalize(f).overlaps(held):
                    touched.append(f)
            except InvalidInputError:
                continue
        if touched:
            report.warnings.append(
                f"la rama cambia rutas reservadas ahora por {res.agent_id} ({res.path}): " + ", ".join(touched[:10])
            )


def merge_branch(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    branch: str,
    test_command: str,
    base: str | None = None,
    approve_tasks: bool = True,
    timeout: int = DEFAULT_TEST_TIMEOUT,
) -> IntegrationReport:
    """Fusiona ``branch`` en la base dentro del worktree principal, solo si la revisión da 'ready'."""
    project = projects.get_project(conn, project_id)
    repo = Path(project.repository_root)
    report = check_integration(
        conn, clock, project_id=project_id, branch=branch, base=base, test_command=test_command, timeout=timeout
    )
    if report.verdict != "ready":
        raise ConflictError(f"no se fusiona: el veredicto es '{report.verdict}'", report=report.to_dict())

    if gitutil.current_branch(repo) != report.base:
        raise ConflictError(
            "el worktree principal no está en la rama base; cámbiala a mano antes de fusionar",
            worktree=str(repo),
            base=report.base,
        )
    if gitutil.git(repo, "status", "--porcelain").stdout.strip():
        raise ConflictError("el worktree principal tiene cambios sin commit", worktree=str(repo))
    if _rev(repo, report.base) != report.base_commit:
        raise ConflictError("la rama base cambió durante la revisión; vuelve a ejecutar la integración")

    merge = gitutil.git(
        repo,
        "merge",
        "--no-ff",
        "--no-edit",
        "-m",
        f"Integra {report.branch} (revisado y probado con AgentBridge)",
        report.branch_commit,
        check=False,
    )
    if merge.returncode != 0:
        gitutil.git(repo, "merge", "--abort", check=False)
        raise ConflictError("git merge falló y se revirtió", stderr=merge.stderr.strip()[:500])

    merged_tree = gitutil.git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    if merged_tree != report.merged_tree:
        report.warnings.append("el árbol fusionado difiere del probado; revisa el resultado antes de publicarlo")

    projects.register_agent(conn, clock, project_id=project_id, agent_id=INTEGRATOR_ID, agent_type="human")
    if approve_tasks:
        for task_id in report.review_tasks:
            tasks.update_task_status(
                conn,
                clock,
                project_id=project_id,
                task_id=task_id,
                agent_id=INTEGRATOR_ID,
                status="done",
                note=f"integrada en {report.base} desde {report.branch}",
            )
    with transaction(conn):
        audit.record(
            conn,
            clock,
            project_id=project_id,
            actor_id=INTEGRATOR_ID,
            event_type="integration.merged",
            entity_type="branch",
            entity_id=report.branch,
            payload={"base": report.base, "branch_commit": report.branch_commit, "tasks_done": report.review_tasks},
        )
    return report

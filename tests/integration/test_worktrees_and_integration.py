from __future__ import annotations

import shlex
import subprocess
import sys

import pytest

from agentbridge import cli, integration, worktrees
from agentbridge.clock import system_clock
from agentbridge.errors import ConflictError, InvalidInputError
from agentbridge.services import audit, projects, reservations, tasks
from agentbridge.storage.database import open_database

P = "demo"


def git(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=t@example.com", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit_file(wt, rel, content, msg="cambio"):
    target = wt / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    git(wt, "add", rel)
    git(wt, "commit", "-q", "-m", msg)


def py(code: str) -> str:
    """Comando de pruebas portátil: el intérprete actual ejecutando un fragmento."""
    return shlex.join([sys.executable, "-c", code])


@pytest.fixture
def proj(tmp_path):
    main = tmp_path / "app"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    git(main, "config", "user.name", "Integrador")
    git(main, "config", "user.email", "integrador@example.com")
    commit_file(main, "README.md", "hola\n", "inicio")
    conn = open_database(tmp_path / "bridge.db")
    projects.register_project(conn, system_clock, project_id=P, name=P, repository_root=main)
    for agent in ("claude", "opencode"):
        projects.register_agent(conn, system_clock, project_id=P, agent_id=agent, agent_type=agent)
    return {"main": main.resolve(), "conn": conn, "db": tmp_path / "bridge.db", "tmp": tmp_path}


def task_branch(proj, agent="claude", title="Agregar login"):
    conn = proj["conn"]
    task = tasks.create_task(conn, system_clock, project_id=P, actor_id=agent, title=title)
    tasks.claim_task(conn, system_clock, project_id=P, task_id=task.id, agent_id=agent)
    created = worktrees.create_worktree(conn, system_clock, project_id=P, agent_id=agent, task_id=task.id)
    return task, created


def report(proj, task, agent, files):
    tasks.report_completion(
        proj["conn"], system_clock, project_id=P, task_id=task.id, agent_id=agent,
        summary="hecho", changed_files=files, tests_run=["pytest"], test_results="ok",
    )  # fmt: skip


def check(proj, branch, test_cmd=None):
    return integration.check_integration(proj["conn"], system_clock, project_id=P, branch=branch, test_command=test_cmd)


# --- worktrees --------------------------------------------------------------------


def test_slugify():
    assert worktrees.slugify("Añadir login con OAuth 2.0!") == "anadir-login-con-oauth-2-0"
    assert worktrees.slugify("¡¡¡") == "tarea"


@pytest.mark.parametrize("bad", ["-x", "--upload-pack=evil", "a..b", "con espacio", ""])
def test_branch_names_are_validated(proj, bad):
    with pytest.raises(InvalidInputError):
        worktrees.validate_branch(proj["main"], bad)


def test_create_worktree_for_task_and_never_reuse(proj):
    task, created = task_branch(proj)
    assert created.branch == f"claude/task-{task.id}-agregar-login"
    assert created.path == proj["main"].parent / f"app-claude-t{task.id}"
    agent = projects.require_agent(proj["conn"], P, "claude")
    assert (agent.workspace, agent.branch) == (str(created.path), created.branch)
    with pytest.raises(ConflictError, match="rama ya existe"):
        worktrees.create_worktree(proj["conn"], system_clock, project_id=P, agent_id="claude", task_id=task.id)
    (proj["main"].parent / "app-opencode").mkdir()
    with pytest.raises(ConflictError, match="carpeta destino ya existe"):
        worktrees.create_worktree(proj["conn"], system_clock, project_id=P, agent_id="opencode")


def test_list_worktrees_shows_owner_progress_and_dirty(proj):
    _, created = task_branch(proj)
    commit_file(created.path, "src/a.py", "a = 1\n")
    (created.path / "borrador.txt").write_text("sin commit")
    info = {w.agent_id: w for w in worktrees.list_worktrees(proj["conn"], project_id=P)}
    assert info["claude"].ahead == 1 and info["claude"].behind == 0 and info["claude"].dirty
    assert info[None].branch == "main"


# --- integración ------------------------------------------------------------------


def test_tests_run_on_the_merged_result_not_the_isolated_branch(proj):
    task, created = task_branch(proj)
    commit_file(created.path, "feature.txt", "nueva\n")
    report(proj, task, "claude", ["feature.txt"])
    commit_file(proj["main"], "base.txt", "otro cambio en main\n")  # main avanzó mientras tanto

    both_exist = py(
        "import pathlib,sys; sys.exit(0 if all(pathlib.Path(p).exists() for p in ('feature.txt','base.txt')) else 1)"
    )
    result = check(proj, created.branch, both_exist)
    # En la rama aislada base.txt no existe: solo pasa si se prueba el árbol fusionado.
    assert result.verdict == "ready", result.test_output_tail
    assert result.changed_files == ["feature.txt"] and result.review_tasks == [task.id]
    assert result.warnings == []


def test_check_never_modifies_refs_or_leaves_worktrees(proj):
    _, created = task_branch(proj)
    commit_file(created.path, "x.txt", "x\n")
    before = (git(proj["main"], "rev-parse", "main"), git(proj["main"], "rev-parse", created.branch))
    worktrees_before = git(proj["main"], "worktree", "list")
    check(proj, created.branch, py("pass"))
    assert (git(proj["main"], "rev-parse", "main"), git(proj["main"], "rev-parse", created.branch)) == before
    assert git(proj["main"], "worktree", "list") == worktrees_before


def test_conflicts_are_detected_without_running_tests(proj):
    _, a = task_branch(proj, "claude", "uno")
    _, b = task_branch(proj, "opencode", "dos")
    commit_file(a.path, "README.md", "versión de claude\n")
    commit_file(b.path, "README.md", "versión de opencode\n")
    # Se integra la rama de claude en main; luego la de opencode choca con main.
    git(proj["main"], "merge", "-q", "--no-ff", "-m", "m", a.branch)
    result = check(proj, b.branch, py("raise SystemExit(0)"))
    assert result.verdict == "conflicts" and result.conflicts == ["README.md"]
    assert result.tests_passed is None
    assert any("CONFLICT" in m for m in result.conflict_messages)


def test_failing_tests_block_and_show_output(proj):
    _, created = task_branch(proj)
    commit_file(created.path, "x.txt", "x\n")
    result = check(proj, created.branch, py("print('3 failed'); raise SystemExit(1)"))
    assert result.verdict == "tests_failed" and "3 failed" in result.test_output_tail


def test_cross_check_warns_about_undeclared_and_reserved_files(proj):
    task, created = task_branch(proj)
    reservations.reserve_paths(proj["conn"], system_clock, project_id=P, agent_id="opencode", paths=["docs/"])
    commit_file(created.path, "src/a.py", "a\n")
    commit_file(created.path, "docs/guia.md", "g\n")
    report(proj, task, "claude", ["src/a.py"])
    result = check(proj, created.branch)
    assert result.verdict == "ready_without_tests"
    assert any("ningún informe de tarea declara: docs/guia.md" in w for w in result.warnings)
    assert any("reservadas ahora por opencode" in w for w in result.warnings)


def test_nothing_to_integrate(proj):
    _, created = task_branch(proj)
    assert check(proj, created.branch).verdict == "nothing_to_integrate"


def test_merge_requires_ready_and_clean_main_then_marks_tasks_done(proj):
    task, created = task_branch(proj)
    commit_file(created.path, "feature.txt", "nueva\n")
    report(proj, task, "claude", ["feature.txt"])
    conn = proj["conn"]

    (proj["main"] / "suelto.txt").write_text("sin commit")
    with pytest.raises(ConflictError, match="cambios sin commit"):
        integration.merge_branch(conn, system_clock, project_id=P, branch=created.branch, test_command=py("pass"))
    (proj["main"] / "suelto.txt").unlink()

    with pytest.raises(ConflictError, match="tests_failed"):
        integration.merge_branch(
            conn, system_clock, project_id=P, branch=created.branch, test_command=py("raise SystemExit(1)")
        )
    assert git(proj["main"], "rev-list", "--count", "main") == "1"  # nada se fusionó

    result = integration.merge_branch(conn, system_clock, project_id=P, branch=created.branch, test_command=py("pass"))
    assert result.warnings == []  # el árbol fusionado es idéntico al probado
    assert len(git(proj["main"], "log", "-1", "--format=%P").split()) == 2  # merge --no-ff
    assert (proj["main"] / "feature.txt").exists()
    assert tasks.get_task(conn, system_clock, project_id=P, task_id=task.id).status == "done"
    events = [e.event_type for e in audit.list_events(conn, project_id=P, limit=500)]
    assert events[-1] == "integration.merged"


def test_cli_merge_requires_yes_and_check_exit_codes(proj, capsys):
    task, created = task_branch(proj)
    commit_file(created.path, "f.txt", "f\n")
    base = ["--project-id", P, "--db", str(proj["db"]), "--branch", created.branch]
    assert cli.main(["integrate", "check", *base]) == 0
    assert "ready_without_tests" in capsys.readouterr().out
    assert cli.main(["integrate", "merge", *base, "--test-cmd", py("pass")]) == cli.EXIT_ERROR
    assert git(proj["main"], "rev-list", "--count", "main") == "1"
    assert cli.main(["integrate", "check", *base, "--test-cmd", py("raise SystemExit(2)")]) == cli.EXIT_CONFLICT
    # argparse ya rechaza "--branch --evil"; con "=" el valor llega y lo frena validate_branch.
    assert cli.main(["integrate", "check", "--project-id", P, "--db", str(proj["db"]), "--branch=--evil"]) == 1


def test_cli_worktree_create_and_list(proj, capsys):
    ident = ["--project-id", P, "--agent-id", "opencode", "--db", str(proj["db"])]
    assert cli.main(["worktree", "create", *ident]) == 0
    assert "opencode/work" in capsys.readouterr().out
    assert cli.main(["worktree", "list", "--project-id", P, "--db", str(proj["db"])]) == 0
    assert "opencode" in capsys.readouterr().out

from __future__ import annotations

import pytest

from agentbridge.errors import ConflictError, InvalidInputError, NotFoundError
from agentbridge.services import audit, projects


def _register(db, clock, repo, **kwargs):
    params = {"project_id": "demo", "name": "Demo", "repository_root": repo} | kwargs
    return projects.register_project(db, clock, **params)


def test_register_project_is_idempotent(db, clock, repo):
    first = _register(db, clock, repo)
    second = _register(db, clock, repo)
    assert first == second
    assert first.repository_root == str(repo.resolve())
    assert [e.event_type for e in audit.list_events(db, project_id="demo")] == ["project.registered"]


def test_worktree_root_with_git_file_is_accepted(db, clock, tmp_path):
    root = tmp_path / "wt"
    root.mkdir()
    (root / ".git").write_text("gitdir: /somewhere\n")
    assert _register(db, clock, root).repository_root == str(root.resolve())


def test_same_id_different_root_conflicts(db, clock, repo, tmp_path):
    _register(db, clock, repo)
    other = tmp_path / "other"
    (other / ".git").mkdir(parents=True)
    with pytest.raises(ConflictError):
        _register(db, clock, other)


def test_same_root_different_id_conflicts(db, clock, repo):
    _register(db, clock, repo)
    with pytest.raises(ConflictError):
        _register(db, clock, repo, project_id="otro")


@pytest.mark.parametrize("bad_root", ["relative/path", "/no/existe"])
def test_invalid_roots_are_rejected(db, clock, bad_root):
    with pytest.raises(InvalidInputError):
        _register(db, clock, bad_root)


def test_directory_without_git_is_rejected(db, clock, tmp_path):
    with pytest.raises(InvalidInputError, match="Git"):
        _register(db, clock, tmp_path)


@pytest.mark.parametrize("bad_id", ["Demo", "con espacio", "", "-x", "a" * 65, "ñ"])
def test_invalid_ids_are_rejected(db, clock, repo, bad_id):
    with pytest.raises(InvalidInputError):
        _register(db, clock, repo, project_id=bad_id)


def test_register_agent_audits_only_real_changes(db, clock, repo, tmp_path):
    _register(db, clock, repo)
    projects.register_agent(db, clock, project_id="demo", agent_id="claude", agent_type="claude-code")
    clock.advance(10)
    again = projects.register_agent(db, clock, project_id="demo", agent_id="claude", agent_type="claude-code")
    assert again.last_seen_at > again.created_at
    projects.register_agent(
        db, clock, project_id="demo", agent_id="claude", agent_type="claude-code", workspace=tmp_path, branch="b"
    )
    events = [e.event_type for e in audit.list_events(db, project_id="demo")]
    assert events == ["project.registered", "agent.registered", "agent.updated"]


def test_register_agent_requires_existing_project(db, clock):
    with pytest.raises(NotFoundError):
        projects.register_agent(db, clock, project_id="nada", agent_id="claude", agent_type="x")


def test_register_agent_rejects_missing_workspace(db, clock, repo):
    _register(db, clock, repo)
    with pytest.raises(InvalidInputError):
        projects.register_agent(db, clock, project_id="demo", agent_id="claude", agent_type="x", workspace="/no/existe")


def test_touch_and_require_unknown_agent(db, clock, repo):
    _register(db, clock, repo)
    with pytest.raises(NotFoundError):
        projects.touch_agent(db, clock, "demo", "fantasma")
    with pytest.raises(NotFoundError):
        projects.require_agent(db, "demo", "fantasma")


def test_list_agents(db, clock, repo):
    _register(db, clock, repo)
    for agent in ("opencode", "claude"):
        projects.register_agent(db, clock, project_id="demo", agent_id=agent, agent_type=agent)
    assert [a.id for a in projects.list_agents(db, "demo")] == ["claude", "opencode"]


def test_reregistering_with_none_keeps_workspace_and_branch(db, clock, repo, tmp_path):
    """Regresión: al arrancar, el servidor re-registra al agente y no debe borrar su rama."""
    _register(db, clock, repo)
    projects.register_agent(
        db, clock, project_id="demo", agent_id="claude", agent_type="x", workspace=tmp_path, branch="claude/work"
    )
    again = projects.register_agent(db, clock, project_id="demo", agent_id="claude", agent_type="x")
    assert (again.workspace, again.branch) == (str(tmp_path.resolve()), "claude/work")

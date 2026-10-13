from __future__ import annotations

import pytest

from agentbridge.config import Settings
from agentbridge.errors import InvalidInputError, NotFoundError
from agentbridge.server import bootstrap
from agentbridge.services import projects

BASE = {"AGENTBRIDGE_PROJECT_ID": "demo", "AGENTBRIDGE_AGENT_ID": "claude"}


def test_settings_require_identity():
    with pytest.raises(InvalidInputError, match="AGENTBRIDGE_AGENT_ID"):
        Settings.from_env({"AGENTBRIDGE_PROJECT_ID": "demo"})


@pytest.mark.parametrize("key", ["AGENTBRIDGE_PROJECT_ID", "AGENTBRIDGE_AGENT_ID"])
def test_settings_validate_ids(key):
    with pytest.raises(InvalidInputError):
        Settings.from_env(BASE | {key: "Con Espacios"})


def test_settings_defaults(tmp_path):
    s = Settings.from_env(BASE | {"AGENTBRIDGE_DB": str(tmp_path / "x.db")})
    assert s.agent_type == "claude" and s.log_level == "INFO" and s.repo_root is None


def test_bootstrap_requires_registered_project_or_root(tmp_path):
    s = Settings.from_env(BASE | {"AGENTBRIDGE_DB": str(tmp_path / "x.db")})
    with pytest.raises(NotFoundError, match="AGENTBRIDGE_REPO_ROOT"):
        bootstrap(s)


def test_bootstrap_registers_project_and_agent(tmp_path, repo, clock):
    env = BASE | {
        "AGENTBRIDGE_DB": str(tmp_path / "x.db"),
        "AGENTBRIDGE_REPO_ROOT": str(repo),
        "AGENTBRIDGE_WORKSPACE": str(repo),
        "AGENTBRIDGE_AGENT_TYPE": "claude-code",
    }
    conn = bootstrap(Settings.from_env(env), clock)
    (agent,) = projects.list_agents(conn, "demo")
    assert agent.agent_type == "claude-code" and agent.workspace == str(repo.resolve())
    # Arrancar de nuevo (reinicio del cliente) es idempotente.
    conn2 = bootstrap(Settings.from_env(env), clock)
    assert len(projects.list_agents(conn2, "demo")) == 1

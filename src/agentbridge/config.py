"""Configuración del proceso, leída de variables de entorno (ADR-0002).

El cliente MCP (Claude Code, OpenCode) lanza un proceso por agente y le pasa
estas variables desde su archivo de configuración. Así la identidad la fija
quien configura el cliente, no el modelo.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agentbridge.domain.identifiers import validate_id
from agentbridge.errors import InvalidInputError

ENV_PROJECT = "AGENTBRIDGE_PROJECT_ID"
ENV_AGENT = "AGENTBRIDGE_AGENT_ID"
ENV_AGENT_TYPE = "AGENTBRIDGE_AGENT_TYPE"
ENV_DB = "AGENTBRIDGE_DB"
ENV_REPO_ROOT = "AGENTBRIDGE_REPO_ROOT"
ENV_WORKSPACE = "AGENTBRIDGE_WORKSPACE"
ENV_LOG_LEVEL = "AGENTBRIDGE_LOG_LEVEL"

DEFAULT_DB = Path.home() / ".agentbridge" / "agentbridge.db"


@dataclass(frozen=True)
class Settings:
    project_id: str
    agent_id: str
    agent_type: str
    db_path: Path
    repo_root: Path | None
    workspace: Path | None
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        missing = [name for name in (ENV_PROJECT, ENV_AGENT) if not env.get(name)]
        if missing:
            raise InvalidInputError(f"faltan variables de entorno: {', '.join(missing)}")
        project_id = validate_id(env[ENV_PROJECT], kind=ENV_PROJECT)
        agent_id = validate_id(env[ENV_AGENT], kind=ENV_AGENT)
        log_level = env.get(ENV_LOG_LEVEL, "INFO").upper()
        if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise InvalidInputError(f"{ENV_LOG_LEVEL} inválido: {log_level}")
        workspace = env.get(ENV_WORKSPACE)
        repo_root = env.get(ENV_REPO_ROOT)
        return cls(
            project_id=project_id,
            agent_id=agent_id,
            agent_type=env.get(ENV_AGENT_TYPE, agent_id),
            db_path=Path(env.get(ENV_DB) or DEFAULT_DB).expanduser(),
            repo_root=Path(repo_root).expanduser() if repo_root else None,
            # Por defecto, el directorio donde el cliente lanzó el proceso: su worktree.
            workspace=Path(workspace).expanduser() if workspace else Path.cwd(),
            log_level=log_level,
        )

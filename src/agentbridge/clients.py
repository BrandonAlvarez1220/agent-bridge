"""Genera la configuración de Claude Code y OpenCode en el worktree de un agente.

Reglas de seguridad:
* Solo se escriben archivos **no versionados**. Llevan la identidad del agente,
  así que en un commit se mezclarían entre ramas. Se agregan a
  ``info/exclude`` (ignorado local que no se versiona).
* Si un archivo está versionado o no es JSON válido, no se toca: se devuelve
  el fragmento para integrarlo a mano.
* Al fusionar JSON solo se cambian las claves de AgentBridge; lo demás se conserva.
* Repetir ``setup`` deja el mismo resultado (idempotente).
"""

from __future__ import annotations

import json
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from agentbridge import gitutil
from agentbridge.domain.identifiers import validate_id
from agentbridge.errors import InvalidInputError

SERVER_NAME = "agentbridge"
HOOK_MARKER = " hook claude-"
PLUGIN_PATH = ".opencode/plugins/agentbridge.js"
PROTOCOL_PATH = ".agentbridge/PROTOCOL.md"


@dataclass
class SetupReport:
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    manual: dict[str, str] = field(default_factory=dict)  # archivo -> fragmento a integrar a mano
    excluded: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Identity:
    project_id: str
    agent_id: str
    db_path: Path
    command: list[str]  # cómo invocar el CLI `agentbridge`

    def env(self, worktree: Path) -> dict[str, str]:
        return {
            "AGENTBRIDGE_PROJECT_ID": self.project_id,
            "AGENTBRIDGE_AGENT_ID": self.agent_id,
            "AGENTBRIDGE_DB": str(self.db_path),
            "AGENTBRIDGE_WORKSPACE": str(worktree),
        }

    def cli_args(self, *extra: str) -> list[str]:
        return [*self.command, *extra, "--project-id", self.project_id, "--agent-id", self.agent_id,
                "--db", str(self.db_path)]  # fmt: skip


def default_command() -> list[str]:
    """Ruta absoluta del CLI instalado, para que los clientes no dependan del PATH."""
    for candidate in (Path(sys.executable).with_name("agentbridge"), Path(sys.executable).with_name("agentbridge.exe")):
        if candidate.exists():
            return [str(candidate)]
    found = shutil.which("agentbridge")
    if found:
        return [str(Path(found).resolve())]
    raise InvalidInputError("no encuentro el ejecutable 'agentbridge'; instala el paquete o usa --command")


def make_identity(project_id: str, agent_id: str, db_path: Path, command: list[str] | None) -> Identity:
    return Identity(
        project_id=validate_id(project_id, kind="project_id"),
        agent_id=validate_id(agent_id, kind="agent_id"),
        db_path=db_path.expanduser().resolve(),
        command=command or default_command(),
    )


def _worktree_root(path: Path) -> Path:
    if not path.is_dir():
        raise InvalidInputError("el worktree no existe", path=str(path))
    return gitutil.toplevel(path)


def _dump(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _write_text(worktree: Path, relative: str, content: str, report: SetupReport) -> bool:
    """Escribe un archivo no versionado. Devuelve False si lo dejó para integración manual."""
    target = worktree / relative
    if gitutil.is_tracked(worktree, relative):
        report.manual[relative] = content
        report.notes.append(f"{relative} está versionado: no lo modifico para no publicar la identidad del agente.")
        return False
    if target.exists() and target.read_text(encoding="utf-8") == content:
        report.unchanged.append(relative)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        report.written.append(relative)
    report.excluded += gitutil.add_to_exclude(worktree, [relative])
    return True


def _merge_json(worktree: Path, relative: str, merge: Any, fragment: dict[str, Any], report: SetupReport) -> None:
    """Fusiona ``fragment`` en un JSON existente con ``merge(data) -> data``."""
    target = worktree / relative
    data: dict[str, Any] = {}
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            report.manual[relative] = _dump(fragment)
            report.notes.append(f"{relative} no es JSON estricto (¿comentarios?): intégralo a mano.")
            return
        if not isinstance(data, dict):
            report.manual[relative] = _dump(fragment)
            return
    _write_text(worktree, relative, _dump(merge(data)), report)


# --- Claude Code ------------------------------------------------------------------


def claude_mcp_entry(identity: Identity, worktree: Path) -> dict[str, Any]:
    return {
        "type": "stdio",
        "command": identity.command[0],
        "args": [*identity.command[1:], "serve"],
        "env": identity.env(worktree),
    }


def claude_hooks(identity: Identity) -> dict[str, list[dict[str, Any]]]:
    def command(*sub: str) -> dict[str, Any]:
        return {"type": "command", "command": shlex.join(identity.cli_args("hook", *sub)), "timeout": 15}

    return {
        "SessionStart": [{"matcher": "startup|resume|clear|compact", "hooks": [command("claude-context", "--full")]}],
        "UserPromptSubmit": [{"hooks": [command("claude-context")]}],
        "PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit", "hooks": [command("claude-pre-tool-use")]}],
    }


def _merge_claude_settings(data: dict[str, Any], ours: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    hooks = data.setdefault("hooks", {})
    for event, groups in ours.items():
        kept = []
        for group in hooks.get(event, []):
            # Se quitan solo las entradas previas de AgentBridge, para que repetir setup no las duplique.
            remaining = [h for h in group.get("hooks", []) if HOOK_MARKER not in str(h.get("command", ""))]
            if remaining:
                kept.append({**group, "hooks": remaining})
        hooks[event] = kept + groups
    return data


def setup_claude(worktree: Path, identity: Identity) -> SetupReport:
    root = _worktree_root(worktree)
    report = SetupReport()
    entry = claude_mcp_entry(identity, root)
    _merge_json(
        root,
        ".mcp.json",
        lambda d: {**d, "mcpServers": {**d.get("mcpServers", {}), SERVER_NAME: entry}},
        {"mcpServers": {SERVER_NAME: entry}},
        report,
    )
    hooks = claude_hooks(identity)
    _merge_json(
        root,
        ".claude/settings.local.json",
        lambda d: _merge_claude_settings(d, hooks),
        {"hooks": hooks},
        report,
    )
    report.notes.append(
        "Claude Code pedirá aprobar el servidor de .mcp.json la primera vez; "
        "compruébalo con /mcp o 'claude mcp get agentbridge'."
    )
    return report


# --- OpenCode ---------------------------------------------------------------------


def protocol_text() -> str:
    return resources.files("agentbridge").joinpath("templates/PROTOCOL.md").read_text(encoding="utf-8")


def opencode_plugin(identity: Identity) -> str:
    template = resources.files("agentbridge").joinpath("templates/opencode-plugin.js").read_text(encoding="utf-8")
    return template.replace("__AGENTBRIDGE_COMMAND__", json.dumps(identity.cli_args("check-paths")))


def opencode_mcp_entry(identity: Identity, worktree: Path) -> dict[str, Any]:
    return {
        "type": "local",
        "command": [*identity.command, "serve"],
        "environment": identity.env(worktree),
        "enabled": True,
    }


def _merge_opencode(data: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    data.setdefault("$schema", "https://opencode.ai/config.json")
    data["mcp"] = {**data.get("mcp", {}), SERVER_NAME: entry}
    instructions = list(data.get("instructions", []))
    if PROTOCOL_PATH not in instructions:
        instructions.append(PROTOCOL_PATH)
    data["instructions"] = instructions
    return data


def setup_opencode(worktree: Path, identity: Identity) -> SetupReport:
    root = _worktree_root(worktree)
    report = SetupReport()
    entry = opencode_mcp_entry(identity, root)
    _merge_json(
        root,
        "opencode.json",
        lambda d: _merge_opencode(d, entry),
        {"mcp": {SERVER_NAME: entry}, "instructions": [PROTOCOL_PATH]},
        report,
    )
    _write_text(root, PLUGIN_PATH, opencode_plugin(identity), report)
    _write_text(root, PROTOCOL_PATH, protocol_text(), report)
    report.notes.append(
        "OpenCode no documenta un hook de inicio de sesión: el protocolo llega por 'instructions' y el modelo "
        "debe llamar a read_inbox por su cuenta. El plugin sí bloquea ediciones sobre rutas reservadas por otros."
    )
    return report

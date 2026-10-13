"""Línea de comandos de AgentBridge.

agentbridge init         registra un proyecto (raíz del repositorio)
agentbridge serve        servidor MCP por stdio (lo lanzan los clientes)
agentbridge setup        configura Claude Code u OpenCode en un worktree
agentbridge status       resumen del proyecto
agentbridge check-paths  ¿alguna de estas rutas está reservada por otro agente?
agentbridge hook ...     puntos de entrada para los hooks de Claude Code
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agentbridge import clients, gitutil, hooks
from agentbridge.clock import system_clock
from agentbridge.config import DEFAULT_DB, ENV_AGENT, ENV_DB, ENV_PROJECT, ENV_WORKSPACE
from agentbridge.errors import AgentBridgeError
from agentbridge.services import messaging, projects, reservations, tasks
from agentbridge.storage.database import open_database

EXIT_CONFLICT = 3
EXIT_ERROR = 1


def _identity_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-id", default=os.environ.get(ENV_PROJECT), required=not os.environ.get(ENV_PROJECT))
    parser.add_argument("--agent-id", default=os.environ.get(ENV_AGENT), required=not os.environ.get(ENV_AGENT))
    _db_arg(parser)


def _db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, default=Path(os.environ.get(ENV_DB) or DEFAULT_DB))


def _workspace(args: argparse.Namespace, fallback: str | None = None) -> Path:
    """Raíz del worktree del agente.

    Se sube hasta la raíz con Git porque el directorio actual puede ser un
    subdirectorio: desde 'repo/src', 'repo/src/a.py' debe verse como 'src/a.py',
    no como 'a.py', o no chocaría con la reserva de otro agente.
    """
    raw = (
        getattr(args, "workspace", None)
        or os.environ.get(ENV_WORKSPACE)
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or fallback
    )
    start = Path(raw).expanduser() if raw else Path.cwd()
    try:
        return gitutil.toplevel(start)
    except AgentBridgeError:
        return start.resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentbridge", description="Coordinación local multiagente sobre MCP.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="registra un proyecto")
    p.add_argument("--project-id", required=True)
    p.add_argument("--repo", type=Path, required=True, help="raíz del repositorio principal")
    p.add_argument("--name")
    p.add_argument("--default-branch", default="main")
    p.add_argument("--rules-file", type=Path, help="archivo con reglas del proyecto para los agentes")
    _db_arg(p)

    sub.add_parser("serve", help="servidor MCP por stdio (configuración por variables de entorno)")

    p = sub.add_parser("setup", help="configura un cliente en el worktree de un agente")
    p.add_argument("client", choices=["claude", "opencode"])
    p.add_argument("--worktree", type=Path, default=Path.cwd())
    p.add_argument(
        "--command",
        dest="agentbridge_command",
        nargs="+",
        help="cómo invocar agentbridge (por defecto, ruta absoluta detectada)",
    )
    _identity_args(p)

    p = sub.add_parser("status", help="resumen del proyecto")
    p.add_argument("--project-id", default=os.environ.get(ENV_PROJECT), required=not os.environ.get(ENV_PROJECT))
    _db_arg(p)

    p = sub.add_parser("check-paths", help=f"código {EXIT_CONFLICT} si otro agente reservó alguna ruta")
    p.add_argument("paths", nargs="+")
    p.add_argument("--workspace", type=Path)
    _identity_args(p)

    p = sub.add_parser("hook", help="hooks de Claude Code (leen JSON por stdin)")
    p.add_argument("event", choices=["claude-pre-tool-use", "claude-context"])
    p.add_argument("--full", action="store_true", help="contexto completo (inicio de sesión)")
    p.add_argument("--workspace", type=Path)
    _identity_args(p)
    return parser


def _open(db: Path):
    return open_database(db.expanduser())


def cmd_init(args: argparse.Namespace) -> int:
    rules = args.rules_file.read_text(encoding="utf-8") if args.rules_file else ""
    conn = _open(args.db)
    project = projects.register_project(
        conn,
        system_clock,
        project_id=args.project_id,
        name=args.name or args.project_id,
        repository_root=args.repo.expanduser().resolve(),
        default_branch=args.default_branch,
        rules=rules,
    )
    print(f"Proyecto '{project.id}' registrado en {args.db.expanduser()} (raíz: {project.repository_root})")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    identity = clients.make_identity(args.project_id, args.agent_id, args.db, args.agentbridge_command)
    worktree = args.worktree.expanduser().resolve()
    report = (clients.setup_claude if args.client == "claude" else clients.setup_opencode)(worktree, identity)
    for path in report.written:
        print(f"escrito:     {path}")
    for path in report.unchanged:
        print(f"sin cambios: {path}")
    for pattern in report.excluded:
        print(f"ignorado en Git (info/exclude): {pattern}")
    for path, fragment in report.manual.items():
        print(f"\nINTEGRA A MANO en {path}:\n{fragment}")
    for note in report.notes:
        print(f"nota: {note}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = _open(args.db)
    project = projects.get_project(conn, args.project_id)
    print(f"Proyecto {project.id} — {project.repository_root}")
    for agent in projects.list_agents(conn, project.id):
        pending = messaging.count_unacknowledged(conn, project_id=project.id, agent_id=agent.id)
        print(f"  agente {agent.id:<12} visto {agent.last_seen_at}  mensajes sin confirmar: {pending}")
    for task in tasks.list_tasks(conn, system_clock, project_id=project.id, limit=500):
        if task.status in ("done", "cancelled"):
            continue
        owner = f" → {task.assignee_id}" if task.assignee_id else ""
        flag = " (lease vencido)" if task.lease_expired else ""
        print(f"  tarea #{task.id:<4} [{task.status}]{owner}{flag} {task.title}")
    for res in reservations.list_reservations(conn, system_clock, project_id=project.id):
        print(f"  reserva #{res.id:<4} {res.path} → {res.agent_id} hasta {res.expires_at}")
    return 0


def cmd_check_paths(args: argparse.Namespace) -> int:
    conn = _open(args.db)
    workspace = _workspace(args)
    blocked = []
    for raw in args.paths:
        check = hooks.check_edit(
            conn, project_id=args.project_id, agent_id=args.agent_id, workspace=workspace, file_path=raw
        )
        if not check.allowed:
            blocked.append(check.reason())
    if blocked:
        print("\n".join(blocked))
        return EXIT_CONFLICT
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    """Hooks de Claude Code. Ante errores internos, fail-open (ver agentbridge.hooks)."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    conn = _open(args.db)

    if args.event == "claude-context":
        text = hooks.session_context(conn, project_id=args.project_id, agent_id=args.agent_id, full=args.full)
        if text:
            print(text)
        return 0

    file_path = (payload.get("tool_input") or {}).get("file_path") or (payload.get("tool_input") or {}).get(
        "notebook_path"
    )
    if not isinstance(file_path, str):
        return 0
    workspace = _workspace(args, fallback=payload.get("cwd"))
    check = hooks.check_edit(
        conn, project_id=args.project_id, agent_id=args.agent_id, workspace=workspace, file_path=file_path
    )
    if not check.allowed:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": check.reason(),
                    }
                },
                ensure_ascii=False,
            )
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        from agentbridge.server import main as serve

        serve()
        return 0
    handlers = {
        "init": cmd_init,
        "setup": cmd_setup,
        "status": cmd_status,
        "check-paths": cmd_check_paths,
        "hook": cmd_hook,
    }
    try:
        return handlers[args.command](args)
    except AgentBridgeError as exc:
        print(f"agentbridge: {exc.message} {json.dumps(exc.details, ensure_ascii=False, default=str)}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 — los hooks no deben romper al cliente con una traza
        if args.command in ("hook", "check-paths"):
            print(f"agentbridge: error interno ({type(exc).__name__}: {exc}); se permite la operación", file=sys.stderr)
            return EXIT_ERROR
        raise


if __name__ == "__main__":
    raise SystemExit(main())

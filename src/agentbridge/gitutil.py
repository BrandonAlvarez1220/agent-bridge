"""Llamadas a Git seguras: lista de argumentos, sin shell, con timeout.

Nunca se construye un comando concatenando texto. Los argumentos que vienen de
afuera se pasan como elementos separados, así que no pueden inyectar opciones
ni comandos extra.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agentbridge.errors import InvalidInputError

GIT_TIMEOUT_SECONDS = 30


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise InvalidInputError("git no está instalado o no está en el PATH") from exc
    if check and result.returncode != 0:
        raise InvalidInputError(f"git {args[0]} falló: {result.stderr.strip()[:500]}", cwd=str(cwd))
    return result


def toplevel(path: Path) -> Path:
    return Path(git(path, "rev-parse", "--show-toplevel").stdout.strip()).resolve()


def common_dir(path: Path) -> Path:
    """Directorio Git compartido por todos los worktrees (donde vive info/exclude)."""
    out = git(path, "rev-parse", "--git-common-dir").stdout.strip()
    result = Path(out)
    return (result if result.is_absolute() else path / result).resolve()


def is_tracked(worktree: Path, relative: str) -> bool:
    return git(worktree, "ls-files", "--error-unmatch", "--", relative, check=False).returncode == 0


def current_branch(worktree: Path) -> str | None:
    result = git(worktree, "branch", "--show-current", check=False)
    name = result.stdout.strip()
    return name or None


def add_to_exclude(worktree: Path, patterns: list[str]) -> list[str]:
    """Agrega patrones a info/exclude (ignorado local, no versionado). Devuelve los que agregó."""
    exclude = common_dir(worktree) / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
    added = [p for p in patterns if p not in existing]
    if added:
        with exclude.open("a", encoding="utf-8") as fh:
            if existing and existing[-1].strip():
                fh.write("\n")
            fh.write("# AgentBridge: configuración local por agente\n")
            fh.writelines(f"{p}\n" for p in added)
    return added

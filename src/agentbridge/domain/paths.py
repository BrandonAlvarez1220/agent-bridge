"""Validación y normalización de rutas de reserva (ADR-0004).

Una reserva se guarda como ruta **relativa a la raíz del repositorio** con '/'
como separador. Así la misma ruta significa lo mismo en el worktree de cada
agente, aunque sus carpetas físicas sean distintas.

Formas admitidas:
    src/auth/login.py   un archivo
    src/auth/           un directorio y todo lo que contiene ('/' final)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agentbridge.errors import InvalidInputError

MAX_PATH_LENGTH = 512
_GLOB_CHARS = re.compile(r"[*?\[\]{}]")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_FORBIDDEN_TOP_LEVEL = {".git"}


@dataclass(frozen=True, slots=True)
class RepoPath:
    """Ruta normalizada. ``parts`` son los segmentos; ``is_dir`` indica reserva de directorio."""

    parts: tuple[str, ...]
    is_dir: bool

    def __str__(self) -> str:
        text = "/".join(self.parts)
        return f"{text}/" if self.is_dir else text

    def overlaps(self, other: RepoPath) -> bool:
        """Dos rutas se solapan si los segmentos de una son prefijo de los de la otra.

        Se compara por segmentos y no por texto para que 'src/auth/' no se
        solape con 'src/authz.py' (con texto, 'src/auth' sí es prefijo).
        """
        shorter, longer = sorted((self.parts, other.parts), key=len)
        return longer[: len(shorter)] == shorter


def normalize(raw: str) -> RepoPath:
    """Convierte la entrada del agente en una ``RepoPath`` o lanza ``InvalidInputError``."""
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidInputError("la ruta está vacía")
    if len(raw) > MAX_PATH_LENGTH:
        raise InvalidInputError(f"la ruta supera {MAX_PATH_LENGTH} caracteres", path=raw[:80])
    if "\x00" in raw or any(ord(c) < 32 for c in raw):
        raise InvalidInputError("la ruta contiene caracteres de control", path=raw)

    # Se aceptan separadores de Windows por comodidad; dentro del repo siempre se usa '/'.
    text = raw.strip().replace("\\", "/")

    if text.startswith("/") or _WINDOWS_DRIVE.match(text):
        raise InvalidInputError("se esperaba una ruta relativa a la raíz del repositorio", path=raw)
    if _GLOB_CHARS.search(text):
        raise InvalidInputError("no se admiten comodines; reserva el directorio contenedor con '/' final", path=raw)

    is_dir = text.endswith("/")
    parts = tuple(p for p in text.split("/") if p not in ("", "."))
    if not parts:
        raise InvalidInputError("la ruta apunta a la raíz del repositorio; reserva algo más específico", path=raw)
    if ".." in parts:
        raise InvalidInputError("no se admite '..' en las rutas", path=raw)
    # .lower(): en Windows y macOS el sistema de archivos no distingue mayúsculas.
    if parts[0].lower() in _FORBIDDEN_TOP_LEVEL:
        raise InvalidInputError("no se puede reservar el directorio interno de Git", path=raw)
    return RepoPath(parts=parts, is_dir=is_dir)


def ensure_within(root: Path, path: RepoPath) -> Path:
    """Resuelve ``path`` dentro de ``root`` y verifica que no escape por un enlace simbólico.

    ``Path.resolve`` sigue los enlaces de los componentes que existen, así que si
    'docs' es un enlace a '/etc', 'docs/passwd' resuelve fuera de la raíz y se
    rechaza. Los componentes que todavía no existen se aceptan: reservar un
    archivo que se va a crear es un caso normal.
    """
    real_root = root.resolve(strict=True)
    target = real_root.joinpath(*path.parts).resolve(strict=False)
    if not target.is_relative_to(real_root):
        raise InvalidInputError("la ruta sale del repositorio", path=str(path))
    return target


def normalize_many(raws: list[str]) -> list[RepoPath]:
    """Normaliza una lista y quita duplicados conservando el orden."""
    seen: dict[str, RepoPath] = {}
    for raw in raws:
        path = normalize(raw)
        seen.setdefault(str(path), path)
    return list(seen.values())

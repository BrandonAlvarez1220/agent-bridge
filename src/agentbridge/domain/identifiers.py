"""Validación de identificadores de proyecto y agente."""

from __future__ import annotations

import re

from agentbridge.errors import InvalidInputError

_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def validate_id(value: str, *, kind: str) -> str:
    """Identificadores en minúsculas, sin espacios, de 1 a 64 caracteres.

    Son estrictos porque aparecen en nombres de rama, rutas de base de datos y
    registros de auditoría; un carácter raro ahí es una fuente de bugs.
    """
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise InvalidInputError(
            f"{kind} inválido: usa minúsculas, dígitos, '.', '_' o '-' (máx. 64)",
            value=str(value)[:80],
        )
    return value

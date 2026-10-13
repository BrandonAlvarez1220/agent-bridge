"""Errores de dominio.

Cada error lleva un ``code`` estable para que el servidor MCP pueda devolver
errores accionables y las pruebas puedan verificarlos sin depender del texto.
"""

from __future__ import annotations


class AgentBridgeError(Exception):
    code = "agentbridge_error"

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(AgentBridgeError):
    code = "not_found"


class InvalidInputError(AgentBridgeError):
    code = "invalid_input"


class ConflictError(AgentBridgeError):
    """La operación choca con el estado actual (tarea ya reclamada, reserva solapada...)."""

    code = "conflict"


class PermissionDeniedError(AgentBridgeError):
    """El agente intenta operar sobre algo que pertenece a otro agente."""

    code = "permission_denied"

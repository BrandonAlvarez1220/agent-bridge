"""Bitácora de auditoría de solo inserción.

``record`` se llama **dentro** de la transacción de la operación auditada: si la
operación se revierte, su evento también. Así la bitácora nunca cuenta algo que
no ocurrió.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from agentbridge.clock import Clock, to_iso
from agentbridge.domain.models import AuditEvent
from agentbridge.errors import InvalidInputError

MAX_PAYLOAD_BYTES = 16_000
MAX_LIST_LIMIT = 500
REDACTED = "[redactado]"
_SECRET_KEY = re.compile(r"(secret|token|password|passwd|api[_-]?key|credential|authorization)", re.IGNORECASE)


def _redact(value: Any) -> Any:
    """Oculta valores cuyas claves parecen secretos, en cualquier nivel de anidación."""
    if isinstance(value, dict):
        return {k: REDACTED if _SECRET_KEY.search(str(k)) else _redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]
    return value


def record(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    actor_id: str | None,
    event_type: str,
    entity_type: str,
    entity_id: str | int | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    if not conn.in_transaction:
        raise RuntimeError("audit.record debe llamarse dentro de la transacción de la operación auditada")
    body = json.dumps(_redact(payload or {}), ensure_ascii=False, sort_keys=True, default=str)
    if len(body.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        body = json.dumps({"truncated": True, "original_bytes": len(body.encode("utf-8"))})
    cursor = conn.execute(
        "INSERT INTO audit_events (project_id, actor_id, event_type, entity_type, entity_id, payload_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            project_id,
            actor_id,
            event_type,
            entity_type,
            None if entity_id is None else str(entity_id),
            body,
            to_iso(clock()),
        ),
    )
    return int(cursor.lastrowid)


def list_events(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    after_id: int = 0,
    limit: int = 100,
) -> list[AuditEvent]:
    """Eventos en orden de inserción. Se pagina por ``id`` (no por fecha) porque es único y creciente."""
    if not 1 <= limit <= MAX_LIST_LIMIT:
        raise InvalidInputError(f"limit debe estar entre 1 y {MAX_LIST_LIMIT}")
    rows = conn.execute(
        "SELECT * FROM audit_events WHERE project_id = ? AND id > ? ORDER BY id LIMIT ?",
        (project_id, after_id, limit),
    ).fetchall()
    return [AuditEvent.from_row(r) for r in rows]

"""Mensajes persistentes entre agentes (ADR-0006).

Estados verificables: publicado (fila en ``messages``), entregado
(``delivered_at``: devuelto en ``read_inbox``) y confirmado
(``acknowledged_at``). "Leído por el modelo" no es observable y no se registra.
"""

from __future__ import annotations

import sqlite3

from agentbridge.clock import Clock, to_iso
from agentbridge.domain.models import InboxMessage, Message, Receipt
from agentbridge.errors import InvalidInputError, NotFoundError, PermissionDeniedError
from agentbridge.services import audit, projects
from agentbridge.storage.database import transaction

MESSAGE_TYPES = ("info", "question", "dependency", "conflict", "handoff", "review_request", "urgent")
MAX_BODY = 8000
MAX_INBOX_LIMIT = 200

# Mensajes visibles para un agente: dirigidos a él o broadcast de otro agente.
_VISIBLE_TO = (
    "m.project_id = :project AND (m.recipient_id = :agent OR (m.recipient_id IS NULL AND m.sender_id <> :agent))"
)


def publish_message(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    sender_id: str,
    body: str,
    message_type: str = "info",
    recipient_id: str | None = None,
    task_id: int | None = None,
) -> Message:
    if message_type not in MESSAGE_TYPES:
        raise InvalidInputError(f"message_type inválido; valores: {', '.join(MESSAGE_TYPES)}")
    if not body.strip():
        raise InvalidInputError("body no puede estar vacío")
    if len(body) > MAX_BODY:
        raise InvalidInputError(f"body supera {MAX_BODY} caracteres; resume o divide el mensaje")
    if recipient_id == sender_id:
        raise InvalidInputError("un agente no puede enviarse mensajes a sí mismo")

    with transaction(conn):
        projects.touch_agent(conn, clock, project_id, sender_id)
        if recipient_id is not None:
            projects.require_agent(conn, project_id, recipient_id)
        if (
            task_id is not None
            and not conn.execute(
                "SELECT 1 FROM tasks WHERE id = ? AND project_id = ?", (task_id, project_id)
            ).fetchone()
        ):
            raise NotFoundError("tarea no encontrada en el proyecto", task_id=task_id)
        message_id = int(
            conn.execute(
                "INSERT INTO messages (project_id, sender_id, recipient_id, task_id, message_type, body, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_id, sender_id, recipient_id, task_id, message_type, body, to_iso(clock())),
            ).lastrowid
        )
        # El cuerpo no va a la auditoría: ya vive en messages y podría ser grande o sensible.
        audit.record(
            conn,
            clock,
            project_id=project_id,
            actor_id=sender_id,
            event_type="message.published",
            entity_type="message",
            entity_id=message_id,
            payload={"recipient_id": recipient_id, "message_type": message_type, "task_id": task_id},
        )
        return _get(conn, message_id)


def _get(conn: sqlite3.Connection, message_id: int) -> Message:
    row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if row is None:
        raise NotFoundError("mensaje no encontrado", message_id=message_id)
    return Message(**dict(row))


def read_inbox(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    project_id: str,
    agent_id: str,
    unread_only: bool = True,
    limit: int = 50,
) -> list[InboxMessage]:
    """Devuelve mensajes y los marca como entregados.

    ``unread_only`` significa "sin confirmar": un mensaje sigue apareciendo hasta
    que el agente lo confirme, aunque ya se le haya entregado. Así un agente que
    se reinicia a mitad de una tarea no pierde mensajes.
    """
    if not 1 <= limit <= MAX_INBOX_LIMIT:
        raise InvalidInputError(f"limit debe estar entre 1 y {MAX_INBOX_LIMIT}")
    with transaction(conn):
        projects.touch_agent(conn, clock, project_id, agent_id)
        sql = (
            "SELECT m.* FROM messages m LEFT JOIN message_receipts r"
            " ON r.message_id = m.id AND r.agent_id = :agent"
            f" WHERE {_VISIBLE_TO}"
        )
        if unread_only:
            sql += " AND r.acknowledged_at IS NULL"
        sql += " ORDER BY m.id LIMIT :limit"
        rows = conn.execute(sql, {"project": project_id, "agent": agent_id, "limit": limit}).fetchall()

        now = to_iso(clock())
        conn.executemany(
            "INSERT INTO message_receipts (message_id, project_id, agent_id, delivered_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (message_id, agent_id)"
            " DO UPDATE SET delivered_at = COALESCE(delivered_at, excluded.delivered_at)",
            [(r["id"], project_id, agent_id, now) for r in rows],
        )
        result = []
        for row in rows:
            receipt = conn.execute(
                "SELECT delivered_at, acknowledged_at FROM message_receipts WHERE message_id = ? AND agent_id = ?",
                (row["id"], agent_id),
            ).fetchone()
            result.append(
                InboxMessage(
                    message=Message(**dict(row)),
                    delivered_at=receipt["delivered_at"],
                    acknowledged_at=receipt["acknowledged_at"],
                )
            )
        return result


def count_unacknowledged(conn: sqlite3.Connection, *, project_id: str, agent_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM messages m LEFT JOIN message_receipts r"
        " ON r.message_id = m.id AND r.agent_id = :agent"
        f" WHERE {_VISIBLE_TO} AND r.acknowledged_at IS NULL",
        {"project": project_id, "agent": agent_id},
    ).fetchone()
    return int(row[0])


def acknowledge_message(
    conn: sqlite3.Connection, clock: Clock, *, project_id: str, message_id: int, agent_id: str
) -> Receipt:
    """Confirma un mensaje. Confirmar dos veces conserva la primera fecha (idempotente)."""
    with transaction(conn):
        projects.touch_agent(conn, clock, project_id, agent_id)
        visible = conn.execute(
            f"SELECT 1 FROM messages m WHERE m.id = :id AND {_VISIBLE_TO}",
            {"id": message_id, "project": project_id, "agent": agent_id},
        ).fetchone()
        if visible is None:
            if conn.execute(
                "SELECT 1 FROM messages WHERE id = ? AND project_id = ?", (message_id, project_id)
            ).fetchone():
                raise PermissionDeniedError("el mensaje no está dirigido a este agente", message_id=message_id)
            raise NotFoundError("mensaje no encontrado", message_id=message_id)

        existing = conn.execute(
            "SELECT acknowledged_at FROM message_receipts WHERE message_id = ? AND agent_id = ?", (message_id, agent_id)
        ).fetchone()
        if existing is None or existing["acknowledged_at"] is None:
            now = to_iso(clock())
            conn.execute(
                "INSERT INTO message_receipts (message_id, project_id, agent_id, delivered_at, acknowledged_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT (message_id, agent_id) DO UPDATE SET"
                " delivered_at = COALESCE(delivered_at, excluded.delivered_at),"
                " acknowledged_at = excluded.acknowledged_at",
                (message_id, project_id, agent_id, now, now),
            )
            audit.record(
                conn,
                clock,
                project_id=project_id,
                actor_id=agent_id,
                event_type="message.acknowledged",
                entity_type="message",
                entity_id=message_id,
            )
        row = conn.execute(
            "SELECT agent_id, delivered_at, acknowledged_at FROM message_receipts"
            " WHERE message_id = ? AND agent_id = ?",
            (message_id, agent_id),
        ).fetchone()
        return Receipt(**dict(row))


def get_message_status(conn: sqlite3.Connection, *, project_id: str, message_id: int, agent_id: str) -> list[Receipt]:
    """Recibos de un mensaje, solo para su remitente.

    Un destinatario sin recibo todavía no lo ha recibido. Para un broadcast solo
    aparecen los agentes que ya consultaron su bandeja.
    """
    message = _get(conn, message_id)
    if message.project_id != project_id:
        raise NotFoundError("mensaje no encontrado", message_id=message_id)
    if message.sender_id != agent_id:
        raise PermissionDeniedError("solo el remitente puede consultar el estado del mensaje", message_id=message_id)
    rows = conn.execute(
        "SELECT agent_id, delivered_at, acknowledged_at FROM message_receipts WHERE message_id = ? ORDER BY agent_id",
        (message_id,),
    ).fetchall()
    return [Receipt(**dict(r)) for r in rows]


def peek_unacknowledged(conn: sqlite3.Connection, *, project_id: str, agent_id: str, limit: int = 10) -> list[Message]:
    """Vista previa de solo lectura para hooks: no crea recibos ni marca entregas.

    Un aviso del hook no cuenta como entrega: el agente debe llamar a
    ``read_inbox`` para recibir el mensaje completo y luego confirmarlo.
    """
    if not 1 <= limit <= MAX_INBOX_LIMIT:
        raise InvalidInputError(f"limit debe estar entre 1 y {MAX_INBOX_LIMIT}")
    rows = conn.execute(
        "SELECT m.* FROM messages m LEFT JOIN message_receipts r ON r.message_id = m.id AND r.agent_id = :agent"
        f" WHERE {_VISIBLE_TO} AND r.acknowledged_at IS NULL ORDER BY m.id LIMIT :limit",
        {"project": project_id, "agent": agent_id, "limit": limit},
    ).fetchall()
    return [Message(**dict(r)) for r in rows]

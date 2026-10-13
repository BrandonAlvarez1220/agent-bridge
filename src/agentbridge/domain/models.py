"""Modelos de salida. Son inmutables y se construyen desde filas de SQLite."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class Project(_Model):
    id: str
    name: str
    repository_root: str
    default_branch: str
    rules: str
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Project:
        return cls(**dict(row))


class Agent(_Model):
    project_id: str
    id: str
    agent_type: str
    workspace: str | None
    branch: str | None
    status: str
    created_at: str
    last_seen_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Agent:
        return cls(**dict(row))


class AuditEvent(_Model):
    id: int
    project_id: str
    actor_id: str | None
    event_type: str
    entity_type: str
    entity_id: str | None
    payload: dict[str, Any]
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> AuditEvent:
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json"))
        return cls(**data)


class Task(_Model):
    id: int
    project_id: str
    title: str
    description: str
    status: str
    priority: int
    assignee_id: str | None
    lease_expires_at: str | None
    lease_expired: bool
    ready: bool
    dependencies: list[int]
    blocked_by: list[int]
    allowed_paths: list[str]
    created_by: str | None
    created_at: str
    updated_at: str
    completed_at: str | None


class TaskReport(_Model):
    id: int
    task_id: int
    agent_id: str
    summary: str
    changed_files: list[str]
    tests_run: list[str]
    test_results: str
    blockers: str
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> TaskReport:
        data = dict(row)
        data["changed_files"] = json.loads(data["changed_files"])
        data["tests_run"] = json.loads(data["tests_run"])
        return cls(**data)


class Message(_Model):
    id: int
    project_id: str
    sender_id: str
    recipient_id: str | None
    task_id: int | None
    message_type: str
    body: str
    created_at: str


class InboxMessage(_Model):
    """Mensaje tal como se entrega a un agente.

    ``trust`` recuerda al modelo receptor que el cuerpo lo escribió otro agente:
    es información para evaluar, no una instrucción que deba obedecer (ADR-0006).
    """

    message: Message
    delivered_at: str
    acknowledged_at: str | None
    trust: str = "untrusted_peer_content"


class Receipt(_Model):
    agent_id: str
    delivered_at: str | None
    acknowledged_at: str | None


class Reservation(_Model):
    id: int
    project_id: str
    agent_id: str
    task_id: int | None
    path: str
    override_reason: str | None
    created_at: str
    expires_at: str
    released_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Reservation:
        data = dict(row)
        data.pop("expired_logged_at", None)
        return cls(**data)


class Decision(_Model):
    id: int
    project_id: str
    title: str
    decision: str
    rationale: str
    affected_paths: list[str]
    task_id: int | None
    author_agent_id: str | None
    status: str
    superseded_by: int | None
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Decision:
        data = dict(row)
        data["affected_paths"] = json.loads(data["affected_paths"])
        return cls(**data)

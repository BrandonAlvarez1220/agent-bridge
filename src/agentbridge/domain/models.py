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

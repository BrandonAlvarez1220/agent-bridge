"""El servidor MCP de punta a punta con el cliente oficial en memoria.

Dos servidores (claude y opencode) con conexiones distintas al mismo archivo,
como harían dos procesos stdio.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
import pytest
from mcp import Client

from agentbridge.server import build_server
from agentbridge.storage.database import open_database

P = "demo"

EXPECTED_TOOLS = {
    "get_project_context",
    "get_activity_log",
    "create_task",
    "list_tasks",
    "get_task",
    "claim_task",
    "update_task_status",
    "add_task_dependency",
    "report_completion",
    "publish_message",
    "read_inbox",
    "acknowledge_message",
    "get_message_status",
    "reserve_paths",
    "list_reservations",
    "release_reservation",
    "record_decision",
    "search_decisions",
}


class Agents:
    """Ejecuta llamadas MCP como 'claude' u 'opencode' sobre la misma base."""

    def __init__(self, tmp_path, clock) -> None:
        self.servers = {
            name: build_server(open_database(tmp_path / "bridge.db"), project_id=P, agent_id=name, clock=clock)
            for name in ("claude", "opencode")
        }

    def call(self, agent: str, tool: str, **args: Any) -> dict[str, Any]:
        result = self.raw(agent, tool, **args)
        assert not result.is_error, result.content[0].text
        return result.structured_content

    def error(self, agent: str, tool: str, **args: Any) -> dict[str, Any]:
        result = self.raw(agent, tool, **args)
        assert result.is_error
        text = result.content[0].text
        return json.loads(text[text.index("{") :])

    def raw(self, agent: str, tool: str, **args: Any):
        async def go():
            async with Client(self.servers[agent]) as client:
                return await client.call_tool(tool, args)

        return anyio.run(go)


@pytest.fixture
def agents(bridge, tmp_path, clock) -> Agents:
    return Agents(tmp_path, clock)


def test_tool_surface_never_accepts_identity(agents):
    async def go():
        async with Client(agents.servers["claude"]) as client:
            return (await client.list_tools()).tools

    tools = anyio.run(go)
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for t in tools:
        props = set(t.input_schema.get("properties", {}))
        assert not props & {"agent_id", "project_id", "sender_id", "actor_id"}, t.name
        assert t.description, t.name
    read_only = {t.name for t in tools if t.annotations and t.annotations.read_only_hint}
    assert {"list_tasks", "get_project_context", "list_reservations"} <= read_only
    assert "claim_task" not in read_only


def test_two_agents_full_flow(agents):
    task = agents.call("claude", "create_task", title="Login", allowed_paths=["src/auth/"])
    task_id = task["id"]

    agents.call("claude", "claim_task", task_id=task_id)
    err = agents.error("opencode", "claim_task", task_id=task_id)
    assert err["error"] == "conflict" and err["details"]["assignee_id"] == "claude"

    agents.call("claude", "reserve_paths", paths=["src/auth/"], task_id=task_id)
    err = agents.error("opencode", "reserve_paths", paths=["src/auth/login.py"])
    assert err["error"] == "conflict" and err["details"]["conflicts"][0]["agent_id"] == "claude"

    msg = agents.call("claude", "publish_message", body="Cambio la firma de login()", recipient_id="opencode")
    assert agents.call("claude", "get_message_status", message_id=msg["id"])["receipts"] == []

    inbox = agents.call("opencode", "read_inbox")
    assert [m["message"]["id"] for m in inbox["messages"]] == [msg["id"]]
    assert inbox["messages"][0]["trust"] == "untrusted_peer_content"
    agents.call("opencode", "acknowledge_message", message_ids=[msg["id"]])
    (receipt,) = agents.call("claude", "get_message_status", message_id=msg["id"])["receipts"]
    assert receipt["acknowledged_at"] is not None

    report = agents.call(
        "claude",
        "report_completion",
        task_id=task_id,
        summary="login listo",
        changed_files=["src/auth/login.py"],
        tests_run=["uv run pytest"],
        test_results="12 passed",
    )
    assert report["changed_files"] == ["src/auth/login.py"]
    assert agents.call("opencode", "list_reservations")["reservations"] == []

    done = agents.call("opencode", "update_task_status", task_id=task_id, status="done", note="revisado")
    assert done["status"] == "done"
    detail = agents.call("claude", "get_task", task_id=task_id)
    assert len(detail["reports"]) == 1

    events = agents.call("claude", "get_activity_log", limit=500)["events"]
    assert {"task.claimed", "reservation.created", "task.reported", "task.status_changed"} <= {
        e["event_type"] for e in events
    }


def test_domain_errors_are_structured(agents):
    assert agents.error("claude", "get_task", task_id=999)["error"] == "not_found"
    assert agents.error("claude", "reserve_paths", paths=["../fuera.py"])["error"] == "invalid_input"
    res = agents.call("claude", "reserve_paths", paths=["a.py"])["reservations"][0]
    assert agents.error("opencode", "release_reservation", reservation_id=res["id"])["error"] == "permission_denied"


def test_schema_validation_rejects_bad_arguments(agents):
    task = agents.call("claude", "create_task", title="t")
    result = agents.raw("claude", "update_task_status", task_id=task["id"], status="ready")
    assert result.is_error


def test_context_and_decisions(agents):
    agents.call("claude", "record_decision", title="UTC", decision="Usar UTC", rationale="Procesos comparables")
    ctx = agents.call("opencode", "get_project_context")
    assert ctx["you"]["id"] == "opencode"
    assert ctx["unacknowledged_messages"] == 1
    assert [d["title"] for d in ctx["decisions"]] == ["UTC"]
    found = agents.call("opencode", "search_decisions", query="utc")["decisions"]
    assert len(found) == 1


def test_concurrent_calls_on_one_server_do_not_interleave_transactions(agents):
    """El SDK corre herramientas síncronas en hilos; sin el lock, dos BEGIN chocarían en la misma conexión."""
    server = agents.servers["claude"]

    async def go():
        results = []
        async with Client(server) as client:

            async def create(i: int) -> None:
                results.append(await client.call_tool("create_task", {"title": f"t{i}"}))

            async with anyio.create_task_group() as tg:
                for i in range(30):
                    tg.start_soon(create, i)
        return results

    results = anyio.run(go)
    assert all(not r.is_error for r in results), [r.content[0].text for r in results if r.is_error][:1]
    assert len(agents.call("opencode", "list_tasks", limit=500)["tasks"]) == 30

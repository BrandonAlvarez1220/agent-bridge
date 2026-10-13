"""Arranca el servidor como lo haría un cliente MCP real: proceso hijo por stdio."""

from __future__ import annotations

import os
import shutil
import sys

import anyio
import pytest
from mcp import Client, StdioServerParameters


def _params(tmp_path, repo, agent: str, **extra: str) -> StdioServerParameters:
    env = {
        **os.environ,
        "AGENTBRIDGE_PROJECT_ID": "demo",
        "AGENTBRIDGE_AGENT_ID": agent,
        "AGENTBRIDGE_DB": str(tmp_path / "shared.db"),
        "AGENTBRIDGE_REPO_ROOT": str(repo),
        "AGENTBRIDGE_WORKSPACE": str(repo),
        **extra,
    }
    command = shutil.which("agentbridge-mcp")
    if command is None:
        pytest.skip("agentbridge-mcp no está instalado en el entorno")
    return StdioServerParameters(command=command, args=[], env=env)


def test_two_stdio_processes_share_state(tmp_path, repo):
    async def go():
        async with Client(_params(tmp_path, repo, "claude")) as claude:
            created = await claude.call_tool("create_task", {"title": "desde stdio"})
            assert not created.is_error
            await claude.call_tool("publish_message", {"body": "hola", "recipient_id": None})
        async with Client(_params(tmp_path, repo, "opencode")) as opencode:
            ctx = await opencode.call_tool("get_project_context", {})
            assert not ctx.is_error
            return ctx.structured_content

    ctx = anyio.run(go)
    assert ctx["you"]["id"] == "opencode"
    assert [t["title"] for t in ctx["ready_tasks"]] == ["desde stdio"]
    assert ctx["unacknowledged_messages"] == 1
    assert {a["id"] for a in ctx["agents"]} == {"claude", "opencode"}


def test_missing_identity_fails_fast(tmp_path):
    import subprocess

    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTBRIDGE_")}
    proc = subprocess.run(
        [sys.executable, "-m", "agentbridge.server"], env=env, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 2
    assert "AGENTBRIDGE_PROJECT_ID" in proc.stderr

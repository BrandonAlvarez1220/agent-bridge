"""Escenario completo: dos agentes como procesos stdio reales, worktrees reales e integración.

Los servidores se lanzan con la configuración que escribió `agentbridge setup`
(.mcp.json para Claude Code, opencode.json para OpenCode), así que esta prueba
también valida esos archivos. Lo único simulado es el modelo: las llamadas a
herramientas y las ediciones las hace la prueba en su lugar.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client, StdioServerParameters

from agentbridge import cli

P = "app"
TEST_CMD = shlex.join([sys.executable, "-m", "unittest", "-q"])


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(wt: Path, rel: str, content: str, msg: str) -> None:
    (wt / rel).write_text(content)
    git(wt, "add", rel)
    git(wt, "commit", "-q", "-m", msg)


def server_from_claude_config(wt: Path) -> StdioServerParameters:
    entry = json.loads((wt / ".mcp.json").read_text())["mcpServers"]["agentbridge"]
    return StdioServerParameters(command=entry["command"], args=entry["args"], env={**os.environ, **entry["env"]})


def server_from_opencode_config(wt: Path) -> StdioServerParameters:
    entry = json.loads((wt / "opencode.json").read_text())["mcp"]["agentbridge"]
    command, *args = entry["command"]
    return StdioServerParameters(command=command, args=args, env={**os.environ, **entry["environment"]})


class ToolFailed(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(payload)
        self.payload = payload


async def call(client: Client, tool: str, **args: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, args)
    if result.is_error:
        text = result.content[0].text
        raise ToolFailed(json.loads(text[text.index("{") :]))
    return result.structured_content


def cli_ok(*argv: str) -> None:
    assert cli.main(list(argv)) == 0, argv


@pytest.fixture
def world(tmp_path):
    """Repo de ejemplo con pruebas unittest, proyecto registrado, worktrees y clientes configurados."""
    main = tmp_path / "app"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    git(main, "config", "user.name", "Integrador")
    git(main, "config", "user.email", "integrador@example.com")
    (main / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (main / "test_calc.py").write_text(
        "import unittest\nimport calc\n\n"
        "class T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(calc.add(2, 3), 5)\n"
    )
    git(main, "add", ".")
    git(main, "commit", "-q", "-m", "inicio")

    db = str(tmp_path / "bridge.db")
    cli_ok("init", "--project-id", P, "--repo", str(main), "--db", db)
    wts = {}
    for agent in ("claude", "opencode"):
        ident = ["--project-id", P, "--agent-id", agent, "--db", db]
        # Los procesos stdio registran al agente al arrancar; aquí se registra antes para crear su worktree.
        from agentbridge.clock import system_clock
        from agentbridge.services import projects
        from agentbridge.storage.database import open_database

        projects.register_agent(open_database(db), system_clock, project_id=P, agent_id=agent, agent_type=agent)
        cli_ok("worktree", "create", *ident)
        wts[agent] = main.parent / f"app-{agent}"
        for g in ("user.name", "user.email"):
            git(wts[agent], "config", g, f"{agent}@example.com" if g == "user.email" else agent)
    cli_ok("setup", "claude", "--worktree", str(wts["claude"]), "--project-id", P, "--agent-id", "claude", "--db", db)
    cli_ok(
        "setup", "opencode", "--worktree", str(wts["opencode"]), "--project-id", P, "--agent-id", "opencode", "--db", db
    )
    return {"main": main, "db": db, "wt": wts}


def test_two_agents_collaborate_end_to_end(world, capsys):
    main, db, wt = world["main"], world["db"], world["wt"]

    async def scenario():
        async with (
            Client(server_from_claude_config(wt["claude"])) as claude,
            Client(server_from_opencode_config(wt["opencode"])) as opencode,
        ):
            # Los dos procesos ven el mismo estado y su propia identidad.
            assert (await call(claude, "get_project_context"))["you"]["id"] == "claude"
            assert (await call(opencode, "get_project_context"))["you"]["id"] == "opencode"

            # Claude planifica: B depende de A.
            a = await call(claude, "create_task", title="Agregar multiply", allowed_paths=["calc.py", "test_calc.py"])
            b = await call(claude, "create_task", title="Agregar square", dependencies=[a["id"]])
            await call(claude, "claim_task", task_id=a["id"])
            await call(claude, "reserve_paths", paths=["calc.py"], task_id=a["id"])

            # OpenCode no puede tomar B (dependencia) ni reservar calc.py (reservado).
            with pytest.raises(ToolFailed) as err:
                await call(opencode, "claim_task", task_id=b["id"])
            assert err.value.payload["details"]["blocked_by"] == [a["id"]]
            with pytest.raises(ToolFailed) as err:
                await call(opencode, "reserve_paths", paths=["calc.py"])
            assert err.value.payload["error"] == "conflict"

            # El plugin de OpenCode bloquearía la edición: misma verificación que usa (check-paths).
            assert cli.main(
                ["check-paths", "--project-id", P, "--agent-id", "opencode", "--db", db,
                 "--workspace", str(wt["opencode"]), "calc.py"]
            ) == cli.EXIT_CONFLICT  # fmt: skip

            # OpenCode pregunta; Claude responde. Nada se pierde entre procesos.
            q = await call(
                opencode, "publish_message", recipient_id="claude", message_type="question",
                body="¿multiply recibe enteros o también floats?",
            )  # fmt: skip
            inbox = await call(claude, "read_inbox")
            assert [m["message"]["id"] for m in inbox["messages"]] == [q["id"]]
            await call(claude, "acknowledge_message", message_ids=[q["id"]])
            await call(claude, "publish_message", recipient_id="opencode", body="Ambos; usa el operador *.")
            assert (await call(opencode, "get_message_status", message_id=q["id"]))["receipts"][0]["acknowledged_at"]

            # Claude implementa A en su worktree y reporta.
            commit(
                wt["claude"],
                "calc.py",
                "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n",
                "multiply",
            )
            await call(
                claude, "report_completion", task_id=a["id"], summary="multiply agregado",
                changed_files=["calc.py"], tests_run=[TEST_CMD], test_results="1 test OK",
            )  # fmt: skip
            assert (await call(opencode, "list_reservations"))["reservations"] == []
            return a, b

        # (los procesos terminan aquí)

    a, b = anyio.run(scenario)

    # Integración de la rama de Claude: revisión y fusión controladas.
    capsys.readouterr()
    integrate = ["--project-id", P, "--db", db, "--test-cmd", TEST_CMD]
    assert cli.main(["integrate", "check", "--branch", "claude/work", *integrate]) == 0
    assert "claude/work → main: ready" in capsys.readouterr().out
    cli_ok("integrate", "merge", "--branch", "claude/work", *integrate, "--yes")
    assert "multiply" in (main / "calc.py").read_text()

    async def second_round():
        # Proceso nuevo para OpenCode: la respuesta de Claude sigue esperándolo.
        async with Client(server_from_opencode_config(wt["opencode"])) as opencode:
            inbox = await call(opencode, "read_inbox")
            assert [m["message"]["body"] for m in inbox["messages"]] == ["Ambos; usa el operador *."]
            await call(opencode, "acknowledge_message", message_ids=[inbox["messages"][0]["message"]["id"]])

            # A está done: B ya se puede reclamar.
            assert (await call(opencode, "get_task", task_id=a["id"]))["task"]["status"] == "done"
            await call(opencode, "claim_task", task_id=b["id"])
            await call(opencode, "reserve_paths", paths=["calc.py"], task_id=b["id"])

            git(wt["opencode"], "merge", "-q", "main")  # trae multiply a su rama
            commit(
                wt["opencode"],
                "calc.py",
                (wt["opencode"] / "calc.py").read_text() + "\n\ndef square(a):\n    return multiply(a, a)\n",
                "square",
            )
            await call(
                opencode, "report_completion", task_id=b["id"], summary="square usa multiply",
                changed_files=["calc.py"], tests_run=[TEST_CMD], test_results="OK",
            )  # fmt: skip

    anyio.run(second_round)
    cli_ok("integrate", "merge", "--branch", "opencode/work", *integrate, "--yes")

    # main contiene ambos cambios, y las pruebas del proyecto pasan sobre el resultado.
    assert "def square" in (main / "calc.py").read_text()
    subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=main, check=True, capture_output=True)

    async def audit_trail():
        async with Client(server_from_claude_config(wt["claude"])) as claude:
            return (await call(claude, "get_activity_log", limit=500))["events"]

    kinds = [e["event_type"] for e in anyio.run(audit_trail)]
    for expected in (
        "task.claimed", "reservation.created", "message.published", "message.acknowledged",
        "task.reported", "integration.checked", "integration.merged",
    ):  # fmt: skip
        assert expected in kinds, expected
    assert kinds.count("integration.merged") == 2


def test_conflicting_branches_are_caught_before_merge(world, capsys):
    main, db, wt = world["main"], world["db"], world["wt"]
    commit(wt["claude"], "calc.py", "def add(a, b):\n    return int(a) + int(b)\n", "claude")
    commit(wt["opencode"], "calc.py", "def add(a, b):\n    return float(a) + float(b)\n", "opencode")
    integrate = ["--project-id", P, "--db", db, "--test-cmd", TEST_CMD]
    cli_ok("integrate", "merge", "--branch", "claude/work", *integrate, "--yes")
    before = git(main, "rev-parse", "main")
    capsys.readouterr()
    assert cli.main(["integrate", "check", "--branch", "opencode/work", *integrate]) == cli.EXIT_CONFLICT
    assert "CONFLICTO: calc.py" in capsys.readouterr().out
    assert cli.main(["integrate", "merge", "--branch", "opencode/work", *integrate, "--yes"]) == cli.EXIT_ERROR
    assert git(main, "rev-parse", "main") == before  # main intacto

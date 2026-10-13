"""Ejecuta el plugin generado con Bun (el runtime de OpenCode) y su shell `$` real.

No reemplaza una prueba dentro de OpenCode: verifica la lógica del plugin y su
llamada al CLI, pero no que OpenCode lo cargue ni el nombre de los argumentos de
apply_patch.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from agentbridge import cli
from agentbridge.clock import system_clock
from agentbridge.services import projects, reservations
from agentbridge.storage.database import open_database

RUNNER = """
import { $ } from "bun";
const { AgentBridge } = await import(process.argv[2]);
const worktree = process.argv[3];
const hooks = await AgentBridge({ $, directory: worktree, worktree });
const run = async (tool, args) => {
  try { await hooks["tool.execute.before"]({ tool }, { args }); return "allowed"; }
  catch (e) { return "blocked: " + e.message; }
};
const patch = "*** Begin Patch\\n*** Update File: src/auth/login.py\\n@@\\n-x\\n+y\\n*** End Patch";
console.log(JSON.stringify({
  edit_reserved: await run("edit", { filePath: worktree + "/src/auth/login.py" }),
  write_free: await run("write", { filePath: worktree + "/README.md" }),
  read_reserved: await run("read", { filePath: worktree + "/src/auth/login.py" }),
  patch_reserved: await run("apply_patch", { patchText: patch }),
  parsed: AgentBridge.pathsFromArgs({ patchText: patch }),
}));
"""


@pytest.mark.skipif(shutil.which("bun") is None, reason="Bun no está instalado")
def test_plugin_blocks_reserved_edits_with_real_bun_shell(git_project, tmp_path):
    main, worktrees = git_project
    wt = worktrees["opencode"]
    db = tmp_path / "bridge.db"
    assert cli.main(["init", "--project-id", "demo", "--repo", str(main), "--db", str(db)]) == 0
    conn = open_database(db)
    for agent in ("claude", "opencode"):
        projects.register_agent(conn, system_clock, project_id="demo", agent_id=agent, agent_type=agent)
    reservations.reserve_paths(conn, system_clock, project_id="demo", agent_id="claude", paths=["src/auth/"])

    ident = ["--project-id", "demo", "--agent-id", "opencode", "--db", str(db)]
    assert cli.main(["setup", "opencode", "--worktree", str(wt), *ident]) == 0

    runner = tmp_path / "runner.mjs"
    runner.write_text(RUNNER)
    result = subprocess.run(
        ["bun", str(runner), str(wt / ".opencode" / "plugins" / "agentbridge.js"), str(wt)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["edit_reserved"].startswith("blocked:") and "reservada por claude" in out["edit_reserved"]
    assert out["write_free"] == "allowed"
    assert out["read_reserved"] == "allowed"
    assert out["patch_reserved"].startswith("blocked:")
    assert out["parsed"] == ["src/auth/login.py"]

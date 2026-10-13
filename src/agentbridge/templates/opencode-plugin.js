// AgentBridge para OpenCode. Generado por `agentbridge setup opencode`; no lo edites a mano.
//
// Antes de que OpenCode edite un archivo, pregunta a AgentBridge si otro agente
// tiene reservada esa ruta. Si la tiene, bloquea la herramienta lanzando un error
// (mecanismo documentado de `tool.execute.before`).
//
// Política ante fallos internos: fail-open. Si AgentBridge no responde, se avisa
// y se permite la edición. Un coordinador roto no debe congelar el trabajo.
//
// NO VERIFICADO contra la documentación: los argumentos de `apply_patch`. Se
// buscan las cabeceras "*** Add/Update/Delete File:" en cualquier argumento de texto.

const COMMAND = __AGENTBRIDGE_COMMAND__;
const EDIT_TOOLS = new Set(["edit", "write", "apply_patch"]);
const CONFLICT_EXIT_CODE = 3;

function pathsFromArgs(args) {
  if (!args || typeof args !== "object") return [];
  if (typeof args.filePath === "string") return [args.filePath];
  const found = [];
  for (const value of Object.values(args)) {
    if (typeof value !== "string") continue;
    for (const m of value.matchAll(/^\*\*\* (?:Add|Update|Delete) File: (.+)$/gm)) found.push(m[1].trim());
    for (const m of value.matchAll(/^\*\*\* Move to: (.+)$/gm)) found.push(m[1].trim());
  }
  return found;
}

export const AgentBridge = async ({ $, directory, worktree }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (!EDIT_TOOLS.has(input.tool)) return;
      const paths = pathsFromArgs(output.args);
      if (paths.length === 0) return;
      // El shell de Bun escapa cada elemento interpolado: no hay inyección de comandos.
      const result = await $`${COMMAND} --workspace ${worktree || directory} ${paths}`.quiet().nothrow();
      if (result.exitCode === CONFLICT_EXIT_CODE) {
        throw new Error(result.stdout.toString().trim());
      }
      if (result.exitCode !== 0) {
        console.error("[agentbridge] check-paths falló; se permite la edición (fail-open):", result.stderr.toString());
      }
    },
  };
};

// Solo se exporta el plugin: OpenCode podría tratar cada export como un plugin.
AgentBridge.pathsFromArgs = pathsFromArgs;

# ADR-0009: hooks del cliente para cumplir el protocolo

**Estado:** aceptada (implementada en la etapa 6; falta la verificación manual con los clientes reales)

## Contexto

Un mensaje persistente no hace que un agente lo lea. Una reserva no impide que un agente edite el archivo. Si el protocolo depende de que el LLM "se acuerde" de llamar a `read_inbox` o `list_reservations`, se va a incumplir.

## Decisión

| Cliente | Mecanismo | Efecto |
| --- | --- | --- |
| Claude Code | Hook `PreToolUse` sobre `Edit\|Write\|MultiEdit\|NotebookEdit` | Devuelve `permissionDecision: "deny"` si otro agente tiene reservada la ruta |
| Claude Code | Hook `SessionStart` | Inyecta identidad, protocolo, tareas activas y vista previa de mensajes |
| Claude Code | Hook `UserPromptSubmit` | Avisa solo cuando hay mensajes sin confirmar; si no, no agrega nada |
| OpenCode | Plugin `tool.execute.before` sobre `edit`, `write`, `apply_patch` | Lanza un error si la ruta está reservada por otro agente |
| OpenCode | `instructions` → `.agentbridge/PROTOCOL.md` | Protocolo en el contexto. No hay hook de inicio documentado |

Los hooks llaman al CLI (`agentbridge hook ...`, `agentbridge check-paths`), no al servidor MCP, para no depender de que el servidor esté arrancado.

## Detalles

- **La vista previa no es entrega.** El hook muestra los mensajes pendientes pero no crea recibos. Solo `read_inbox` marca un mensaje como entregado, así `get_message_status` no afirma más de lo que pasó.
- **El texto de otros agentes se rotula como no confiable** también en la vista previa.
- **Ruta relativa a la raíz del worktree,** que se obtiene con Git. El `cwd` del cliente puede ser un subdirectorio.
- **Fail-open:** ante un error interno el hook permite la edición y avisa por stderr.

## Consecuencias

- En Claude Code y OpenCode, editar una ruta reservada por otro agente queda bloqueado mientras la configuración esté instalada. Los cambios hechos fuera de esos clientes (un editor o la terminal) siguen sin bloquearse.
- El hook no exige que el agente reserve antes de editar: solo impide pisar reservas ajenas. Exigirlo sería más estricto, pero haría fallar ediciones legítimas en archivos que nadie usa.

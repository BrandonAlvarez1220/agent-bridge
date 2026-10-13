# Configurar Claude Code y OpenCode

Cada agente trabaja en **su propio worktree** y tiene su propia identidad. `agentbridge setup` escribe la configuración de cada cliente dentro de su worktree y la marca como ignorada por Git (`.git/info/exclude`), para que la identidad del agente nunca termine en un commit.

## 1. Instalar

```bash
# Desde la carpeta de AgentBridge. Instala los comandos `agentbridge` y `agentbridge-mcp`.
uv tool install .
agentbridge --help
```

`setup` escribe la **ruta absoluta** del ejecutable en la configuración de los clientes, así que funciona aunque el `PATH` del cliente sea distinto al de tu terminal. Si reinstalas AgentBridge en otra ubicación, vuelve a ejecutar `setup`.

## 2. Registrar el proyecto y crear los worktrees

```bash
cd /ruta/mi-proyecto
agentbridge init --project-id mi-proyecto --repo .

git worktree add ../mi-proyecto-claude   -b claude/trabajo
git worktree add ../mi-proyecto-opencode -b opencode/trabajo
```

La base por defecto es `~/.agentbridge/agentbridge.db`. Todos los agentes deben usar la misma (`--db` o `AGENTBRIDGE_DB`).

## 3. Configurar cada cliente

```bash
agentbridge setup claude   --worktree ../mi-proyecto-claude   --project-id mi-proyecto --agent-id claude
agentbridge setup opencode --worktree ../mi-proyecto-opencode --project-id mi-proyecto --agent-id opencode
```

| Cliente | Archivo | Para qué |
| --- | --- | --- |
| Claude Code | `.mcp.json` | Servidor MCP `agentbridge` con la identidad del agente |
| Claude Code | `.claude/settings.local.json` | Hooks: contexto al iniciar, aviso de mensajes en cada prompt y bloqueo de ediciones sobre rutas reservadas por otro agente |
| OpenCode | `opencode.json` | Servidor MCP (`type: local`) e `instructions` apuntando al protocolo |
| OpenCode | `.opencode/plugins/agentbridge.js` | Bloquea `edit`, `write` y `apply_patch` sobre rutas reservadas por otro agente |
| OpenCode | `.agentbridge/PROTOCOL.md` | Reglas de trabajo que OpenCode carga como instrucciones |

`setup` se puede repetir sin problema. Conserva tus claves y hooks propios y **nunca modifica un archivo versionado ni un JSON con comentarios**: en esos casos imprime el fragmento para que lo integres a mano.

## 4. Verificación manual (hazla una vez en tu máquina)

Desde este entorno no puedo abrir Claude Code ni OpenCode de forma interactiva. Estas comprobaciones cierran ese hueco:

| # | Dónde | Acción | Resultado esperado |
| --- | --- | --- | --- |
| 1 | Claude (worktree claude) | Abrir `claude` y aprobar el servidor `agentbridge` | `/mcp` lo muestra conectado con 18 herramientas |
| 2 | Claude | Primera respuesta de la sesión | El modelo sabe que es el agente `claude` (lo inyecta el hook `SessionStart`) |
| 3 | OpenCode (worktree opencode) | Abrir `opencode` | El servidor `agentbridge` aparece conectado |
| 4 | OpenCode | Pedir: "crea una tarea 'prueba' y reclámala" | `agentbridge status` muestra la tarea asignada a `opencode` |
| 5 | Claude | Pedir: "reclama la tarea 'prueba'" | Error `conflict` con `assignee_id: opencode` |
| 6 | OpenCode | Pedir: "reserva `README.md`" | `agentbridge status` muestra la reserva |
| 7 | Claude | Pedir: "edita README.md" | La edición se **bloquea** con el motivo del hook |
| 8 | OpenCode | Pedir: "envía a claude el mensaje 'hola'" | En Claude, el siguiente prompt muestra el aviso de mensaje pendiente |
| 9 | Claude | Pedir: "reserva `docs/`" y luego, en OpenCode, "edita docs/x.md" | El plugin bloquea la edición en OpenCode |

Si algo no coincide, `AGENTBRIDGE_LOG_LEVEL=DEBUG` en la configuración del servidor y `agentbridge status` ayudan a diagnosticar.

## Qué está verificado y qué no

| Elemento | Estado | Cómo |
| --- | --- | --- |
| Servidor MCP por stdio con el cliente MCP oficial | ✅ Probado | `tests/integration/test_stdio_process.py` |
| Formato de `.mcp.json`, hooks `PreToolUse`/`SessionStart`/`UserPromptSubmit` | 📄 Según la documentación de Claude Code (oct. 2026) | La lógica del hook está probada ejecutando el comando generado en `bash` |
| Formato de `opencode.json`, plugins en `.opencode/plugins/`, `tool.execute.before` | 📄 Según la documentación de OpenCode | La lógica del plugin está probada con Bun y su shell `$` real |
| Argumentos de `apply_patch` en OpenCode | ⚠️ No documentado | El plugin busca cabeceras `*** Update File:` en cualquier argumento de texto |
| Inyección de contexto al iniciar sesión en OpenCode | ⚠️ Sin hook documentado | Se usa `instructions`; el modelo debe llamar a `read_inbox` por su cuenta |
| Integración real con ambos clientes abiertos | ⏳ Pendiente | Tabla de verificación manual de arriba |
| Windows | ⚠️ No probado | Los comandos de los hooks se citan para un shell POSIX |

## Política ante fallos

Si AgentBridge está caído o mal configurado, los hooks y el plugin **permiten** la edición y avisan por stderr (*fail-open*). Un coordinador roto no debe paralizar el trabajo. Los worktrees y la revisión del diff siguen protegiendo el repositorio.

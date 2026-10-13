# AgentBridge

Infraestructura local para que varios agentes de programación —inicialmente **Claude Code** y **OpenCode**— colaboren en un mismo repositorio sin pisarse cambios, perder decisiones ni duplicar trabajo.

AgentBridge no une los modelos ni comparte sus conversaciones. Les da un **estado compartido y verificable**: tareas, mensajes, decisiones, dependencias, reservas temporales de rutas y una bitácora de auditoría. Todo vive en una base SQLite y se expone mediante un servidor MCP.

> **Estado:** MVP completo (8 de 8 etapas, 176 pruebas). Pendiente: verificarlo con Claude Code y OpenCode abiertos en tu máquina. Ver la [lista de verificación](docs/CLIENT_SETUP.md#4-verificación-manual-hazla-una-vez-en-tu-máquina).

## Cómo funciona

```text
 Claude Code (worktree ../app-claude, rama claude/work)     OpenCode (worktree ../app-opencode, rama opencode/work)
   ├─ proceso MCP agentbridge (AGENT_ID=claude)               ├─ proceso MCP agentbridge (AGENT_ID=opencode)
   └─ hooks: bloquean ediciones sobre rutas ajenas            └─ plugin: bloquea ediciones sobre rutas ajenas
                         │                                                 │
                         └──────────────► SQLite compartido ◄──────────────┘
                              tareas · mensajes · reservas · decisiones · auditoría
                                                 │
                         agentbridge integrate: merge-tree + pruebas sobre el resultado fusionado
                                                 │
                                  merge --no-ff a main (lo decides tú)
```

Hay **cuatro capas de protección**, porque ninguna basta sola:

1. **Coordinación (MCP):** una tarea tiene un solo dueño; las reservas de rutas no se solapan; los mensajes persisten hasta que se confirman.
2. **Cumplimiento (hooks/plugin):** Claude Code y OpenCode no pueden editar una ruta reservada por otro agente.
3. **Aislamiento (worktrees):** cada agente edita su propia carpeta y rama.
4. **Integración (revisión):** conflictos y pruebas se verifican sobre el resultado fusionado antes de tocar `main`.

## Qué garantiza y qué no

| Garantiza (con pruebas) | No garantiza |
| --- | --- |
| Una tarea reclamada tiene un solo dueño, aunque varios agentes compitan (8 procesos en carrera) | Que OpenCode lea su bandeja sin que se lo pidan (no hay hook de inicio documentado) |
| Los mensajes persisten entre reinicios hasta que el destinatario los confirma | Que un archivo reservado no se edite desde fuera de los clientes (editor, terminal) |
| Ninguna reserva activa se solapa con la de otro agente sin una excepción auditada | Que no haya conflictos: los **detecta** antes de fusionar, no los evita |
| Claude Code y OpenCode no editan rutas reservadas por otro agente | Que el código sea correcto: las pruebas reducen el riesgo, no lo eliminan |
| `integrate merge` no fusiona sin conflictos resueltos, pruebas en verde y tu `--yes` | Que nadie haga `git merge` a mano por fuera de AgentBridge |
| | Autenticación multiusuario: es una herramienta local de un solo usuario |

## Inicio rápido

Requisitos: Python ≥ 3.12, [uv](https://docs.astral.sh/uv/), Git ≥ 2.38.

```bash
# 1. Instalar (desde esta carpeta)
uv tool install .

# 2. Registrar tu proyecto y crear un worktree por agente
cd /ruta/mi-app
agentbridge init --project-id mi-app --repo .
agentbridge worktree create --project-id mi-app --agent-id claude     # ../mi-app-claude
agentbridge worktree create --project-id mi-app --agent-id opencode   # ../mi-app-opencode

# 3. Configurar cada cliente en su worktree
agentbridge setup claude   --worktree ../mi-app-claude   --project-id mi-app --agent-id claude
agentbridge setup opencode --worktree ../mi-app-opencode --project-id mi-app --agent-id opencode

# 4. Trabajar: abre `claude` en ../mi-app-claude y `opencode` en ../mi-app-opencode
agentbridge status --project-id mi-app

# 5. Integrar: revisar (no modifica nada) y fusionar si todo está bien
agentbridge integrate check --project-id mi-app --branch claude/work --test-cmd "uv run pytest -q"
agentbridge integrate merge --project-id mi-app --branch claude/work --test-cmd "uv run pytest -q" --yes
```

Pídele al primer agente algo como *"crea las tareas para implementar X y reparte el trabajo"*. El protocolo que reciben los agentes está en [`PROTOCOL.md`](src/agentbridge/templates/PROTOCOL.md).

## Herramientas MCP

| Grupo | Herramientas |
| --- | --- |
| Contexto | `get_project_context`, `get_activity_log` |
| Tareas | `create_task`, `list_tasks`, `get_task`, `claim_task` (también renueva el plazo), `update_task_status`, `add_task_dependency`, `report_completion` |
| Mensajes | `publish_message`, `read_inbox`, `acknowledge_message`, `get_message_status` |
| Reservas | `reserve_paths` (reservar de nuevo renueva el plazo), `list_reservations`, `release_reservation` |
| Decisiones | `record_decision`, `search_decisions` |

Ninguna herramienta acepta `agent_id`: la identidad la fija la configuración del proceso (ADR-0002). Los errores llegan como JSON con código estable (`invalid_input`, `not_found`, `conflict`, `permission_denied`, `busy`).

## Comandos

| Comando | Para qué |
| --- | --- |
| `agentbridge init` | Registrar un proyecto |
| `agentbridge worktree create\|list` | Worktree y rama por agente (`--task-id` para uno por tarea) |
| `agentbridge setup claude\|opencode` | Configurar un cliente en su worktree |
| `agentbridge status` | Agentes, tareas abiertas, reservas y mensajes pendientes |
| `agentbridge integrate check\|merge` | Revisión e integración controladas |
| `agentbridge check-paths` | ¿Alguna ruta está reservada por otro agente? (código 3 = sí) |
| `agentbridge serve` | Servidor MCP por stdio (lo lanzan los clientes) |

## Solución de problemas

| Síntoma | Causa probable | Qué hacer |
| --- | --- | --- |
| El cliente no ve el servidor `agentbridge` | Claude Code pide aprobar los servidores de `.mcp.json` | Ejecuta `/mcp` en Claude y apruébalo; en OpenCode revisa `opencode.json` |
| `el proyecto no está registrado` | La base del servidor no es la de `init` | Usa el mismo `--db` (o `AGENTBRIDGE_DB`) en `init` y en `setup` |
| `no encuentro el ejecutable 'agentbridge'` al hacer `setup` | Instalado solo en un `.venv` | `uv tool install .` o `setup ... --command /ruta/agentbridge` |
| El servidor dejó de arrancar tras mover AgentBridge | `setup` guardó la ruta absoluta del ejecutable | Repite `agentbridge setup` |
| Una tarea quedó "reclamada" por un agente que se cerró | El plazo (*lease*) todavía no vence | Espera a que venza (30 min por defecto) y otro agente podrá reclamarla. Queda auditado |
| Error `busy` | Otra escritura larga tiene el lock de SQLite | Reintenta; si persiste, revisa procesos colgados (`agentbridge status`) |
| `integrate check` da `conflicts` | Las dos ramas cambiaron las mismas líneas | El dueño de la rama hace `git merge main` en su worktree, resuelve y vuelve a reportar |
| `integrate merge` se niega | El worktree principal no está en `main` o tiene cambios | `git status` en el repo principal; deja `main` limpio |
| `git merge-tree` falla | Git anterior a 2.38 | Actualiza Git |
| Un hook falla y no bloquea | Política *fail-open*: un coordinador roto no debe frenar el trabajo | Mira stderr del hook; prueba con `agentbridge check-paths ...` |
| Quedó un worktree temporal tras un corte | La integración se interrumpió | `git worktree prune` |

Para más detalle, ejecuta el servidor con `AGENTBRIDGE_LOG_LEVEL=DEBUG` (los logs van a stderr).

## Limitaciones conocidas

- **Sin verificación con los clientes reales todavía.** Los formatos siguen la documentación oficial (octubre de 2026). La lógica de hooks y plugin está probada fuera de los clientes. Falta abrir Claude Code y OpenCode de verdad ([lista](docs/CLIENT_SETUP.md#4-verificación-manual-hazla-una-vez-en-tu-máquina)).
- **OpenCode:** los argumentos de `apply_patch` no están documentados (el plugin busca las cabeceras del parche) y no hay hook de inicio de sesión, así que la bandeja depende de que el modelo la consulte.
- **Windows sin probar.** Los comandos de los hooks se citan para un shell POSIX.
- **Reservas sin globs:** archivo o directorio (`src/auth/`). Es más grueso, pero el solapamiento es exacto (ADR-0004).
- **Un solo usuario, local.** Sin autenticación: quien edite la configuración puede elegir cualquier identidad.
- **Sin notificaciones activas:** un agente inactivo no se despierta por un mensaje; lo verá en su próxima sesión o prompt.

## Desarrollo

```bash
uv sync                    # entorno y dependencias
uv run pytest              # 176 pruebas: unitarias, concurrencia (procesos), integración y end-to-end
uv run ruff check && uv run ruff format --check
```

| Carpeta | Qué prueba |
| --- | --- |
| `tests/unit` | Esquema, rutas, servicios, configuración |
| `tests/concurrency` | 8 procesos compitiendo por la misma tarea o rutas solapadas |
| `tests/integration` | Servidor MCP (en memoria y stdio), CLI, hooks en `bash`, plugin en Bun, worktrees, integración |
| `tests/e2e` | Dos agentes como procesos stdio lanzados con la configuración que genera `setup`, de la tarea al merge |

## Documentación

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): componentes, capas, concurrencia y errores.
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md): tablas, estados e invariantes.
- [`docs/CLIENT_SETUP.md`](docs/CLIENT_SETUP.md): instalación, configuración de clientes y verificación manual.
- [`docs/WORKTREES.md`](docs/WORKTREES.md): worktrees, integración controlada y recuperación.
- [`docs/SECURITY.md`](docs/SECURITY.md): modelo de amenazas y defensas.
- [`docs/decisions/`](docs/decisions/): 11 decisiones de arquitectura (ADR) con su *por qué*.
- [`docs/ROADMAP.md`](docs/ROADMAP.md): etapas, riesgos y pruebas de aceptación.

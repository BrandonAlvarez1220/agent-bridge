# AgentBridge

Infraestructura local para que varios agentes de programación —inicialmente **Claude Code** y **OpenCode**— colaboren en un mismo repositorio sin pisarse cambios, perder decisiones ni duplicar trabajo.

AgentBridge no une los modelos ni comparte sus conversaciones. Les da un **estado compartido y verificable**: tareas, mensajes, decisiones, dependencias, reservas temporales de rutas y una bitácora de auditoría. Todo vive en una base SQLite y se expone mediante un servidor MCP.

> **Estado:** en desarrollo (etapa 6 de 8). Hay servidor MCP, CLI y configuración automática para Claude Code y OpenCode, con hooks que bloquean ediciones sobre rutas reservadas por otro agente. Falta la verificación manual con ambos clientes abiertos ([`docs/CLIENT_SETUP.md`](docs/CLIENT_SETUP.md)). Ver [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Qué garantiza y qué no

| Garantiza (con pruebas) | No garantiza |
| --- | --- |
| Una tarea reclamada tiene un solo dueño, aunque dos agentes compitan por ella | Que un agente consulte su bandeja si nadie se lo pide (ver ADR-0009) |
| Los mensajes persisten aunque el destinatario esté apagado | Que un archivo reservado no se edite desde fuera de Claude Code u OpenCode (editor, terminal) |
| No se crean reservas solapadas sin una excepción auditada | Que no haya conflictos de Git o semánticos al integrar |
| Claude Code y OpenCode no editan rutas reservadas por otro agente (hooks/plugin) | Que OpenCode lea su bandeja sin que se lo pidan |

## Desarrollo

Requiere Python 3.12+ y [uv](https://docs.astral.sh/uv/).

```bash
uv sync            # crea .venv e instala dependencias
uv run pytest      # ejecuta todas las pruebas
uv run ruff check  # lint
```

## Ejecutar el servidor MCP

Cada agente lanza su propio proceso. La identidad se pasa por variables de entorno, nunca como parámetro de una herramienta (ver ADR-0002).

| Variable | Obligatoria | Descripción |
| --- | --- | --- |
| `AGENTBRIDGE_PROJECT_ID` | sí | Id del proyecto (minúsculas, `.`, `_`, `-`) |
| `AGENTBRIDGE_AGENT_ID` | sí | Id del agente, p. ej. `claude` u `opencode` |
| `AGENTBRIDGE_DB` | no | Ruta de la base compartida. Por defecto `~/.agentbridge/agentbridge.db` |
| `AGENTBRIDGE_REPO_ROOT` | la primera vez | Raíz del repo. Registra el proyecto si aún no existe |
| `AGENTBRIDGE_AGENT_TYPE` | no | Etiqueta libre, p. ej. `claude-code` |
| `AGENTBRIDGE_WORKSPACE` | no | Worktree del agente. Por defecto, el directorio actual |
| `AGENTBRIDGE_LOG_LEVEL` | no | `INFO` por defecto. Los logs van a stderr |

Lo normal es no lanzarlo a mano: `agentbridge setup claude|opencode` genera la configuración de cada cliente. La guía completa está en [`docs/CLIENT_SETUP.md`](docs/CLIENT_SETUP.md).

```bash
uv tool install .
agentbridge init  --project-id demo --repo /ruta/al/repo
agentbridge setup claude   --worktree ../demo-claude   --project-id demo --agent-id claude
agentbridge setup opencode --worktree ../demo-opencode --project-id demo --agent-id opencode
agentbridge status --project-id demo
```

### Herramientas

| Grupo | Herramientas |
| --- | --- |
| Contexto | `get_project_context`, `get_activity_log` |
| Tareas | `create_task`, `list_tasks`, `get_task`, `claim_task` (también renueva el plazo), `update_task_status`, `add_task_dependency`, `report_completion` |
| Mensajes | `publish_message`, `read_inbox`, `acknowledge_message`, `get_message_status` |
| Reservas | `reserve_paths` (reservar de nuevo renueva el plazo), `list_reservations`, `release_reservation` |
| Decisiones | `record_decision`, `search_decisions` |

Son 18, igual que en la especificación, pero no las mismas. Se quitaron `register_agent` (el registro ocurre al arrancar el proceso), `list_agents` (ya viene en `get_project_context`) y `renew_reservation` (basta con volver a llamar a `reserve_paths`). Se agregaron `get_task`, `add_task_dependency` y `get_message_status`. Esta última existe para comprobar si un mensaje se entregó en lugar de suponerlo.

## Documentación

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): componentes, flujo y diagrama.
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md): tablas, estados e invariantes.
- [`docs/decisions/`](docs/decisions/): decisiones de arquitectura (ADR) y el *por qué* de cada una.
- [`docs/CLIENT_SETUP.md`](docs/CLIENT_SETUP.md): instalación, configuración de clientes y verificación manual.
- [`docs/ROADMAP.md`](docs/ROADMAP.md): etapas, riesgos y pruebas de aceptación.

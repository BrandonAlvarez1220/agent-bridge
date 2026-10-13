# Roadmap

| Etapa | Contenido | Estado |
| --- | --- | --- |
| 1 | Inspección del entorno, arquitectura, ADRs | ✅ |
| 2 | Estructura, esquema, migraciones, transacciones | ✅ |
| 3 | Dominio: validación de rutas e ids, modelos, auditoría, registro de proyectos y agentes | ✅ |
| 4 | Servicios de tareas (claim, lease, dependencias), mensajería, reservas, decisiones y contexto | ✅ |
| 5 | Servidor MCP (`mcp` 2.x, `MCPServer`), pruebas con cliente en memoria y con procesos stdio reales | ✅ |
| 6 | CLI, `setup` para Claude Code y OpenCode, hooks y plugin que bloquean ediciones sobre rutas reservadas | ✅ (falta la verificación manual con los clientes, ver CLIENT_SETUP.md) |
| 7 | Worktrees por agente/tarea e integración controlada (`merge-tree`, pruebas sobre el resultado fusionado) | ✅ |
| 8 | End-to-end con dos procesos stdio, README completo, solución de problemas | ⏳ |

## Entorno verificado (2026-10-13)

- Python 3.13 (se requiere ≥ 3.12 por `sqlite3.connect(autocommit=...)`).
- SDK `mcp` 2.3.0. En 2.x `FastMCP` pasó a llamarse `MCPServer` (`mcp.server.mcpserver`). Se fija `mcp>=2.2,<3`.
- Claude Code: servidores stdio en `.mcp.json` (`mcpServers.<nombre>.command/args/env`) o con `claude mcp add --transport stdio --env K=V <nombre> -- <cmd>`.
- OpenCode: `opencode.json` → `mcp.<nombre> = { "type": "local", "command": [...], "environment": {...}, "enabled": true }`.

Los formatos de configuración siguen la documentación oficial. La lógica de hooks y plugin está probada fuera de los clientes. La prueba con Claude Code y OpenCode abiertos queda como verificación manual: ver [`CLIENT_SETUP.md`](CLIENT_SETUP.md).

## Riesgos

| Riesgo | Mitigación |
| --- | --- |
| Los agentes no revisan su bandeja ni sus reservas por iniciativa propia | Hooks de Claude Code y plugin + `instructions` de OpenCode (ADR-0009). En OpenCode la bandeja sigue dependiendo del modelo |
| Un agente se cae con una tarea reclamada | *Lease* con vencimiento (ADR-0005) |
| El LLM se equivoca de identidad | Identidad fijada por el proceso (ADR-0002) |
| Demasiadas herramientas degradan la elección del modelo | Superficie mínima; unir operaciones afines |
| Cambios de la API del SDK MCP | Adaptador delgado en `server.py` |
| Mensajes con *prompt injection* | Se devuelven marcados como datos de otro agente, nunca como instrucciones |

## Pruebas de aceptación (de la especificación)

1. Ambos clientes se conectan al mismo estado → parcial: dos procesos stdio comparten estado con el cliente MCP oficial (`tests/integration/test_stdio_process.py`); falta verificar con Claude Code y OpenCode reales (etapa 6).
2. Mensaje persiste entre reinicios → ✅ (`test_message_survives_recipient_restart`).
3. `claim_task` concurrente → un ganador → ✅ (8 procesos, `tests/concurrency/test_service_races.py`).
4. Reservas incompatibles rechazadas salvo excepción auditada → ✅ (unitarias y 8 procesos en carrera).
5. Reserva expirada reutilizable y auditada → ✅
6. No liberar reservas ajenas → ✅
7. Dependencias bloquean (y no se permiten ciclos) → ✅
8. Rutas fuera del repo rechazadas → ✅ (`tests/unit/test_paths.py`, incluye escape por enlace simbólico).
9. Worktrees separan cambios → ✅ (`tests/integration/test_worktrees_and_integration.py`).
10. Conflictos Git detectados al integrar → ✅ (`integrate check` sin modificar ramas; nunca se afirma que el MCP los evite).
11. Pruebas con un comando (`uv run pytest`) → ✅
12. README completo → etapa 8.

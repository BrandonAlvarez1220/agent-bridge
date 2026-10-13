# Roadmap

| Etapa | Contenido | Estado |
| --- | --- | --- |
| 1 | Inspección del entorno, arquitectura, ADRs | ✅ |
| 2 | Estructura, esquema, migraciones, transacciones | ✅ |
| 3 | Dominio: validación de rutas e ids, modelos, auditoría, registro de proyectos y agentes | ✅ |
| 4 | Servicios de tareas (claim, lease, dependencias), mensajería y reservas | ⏳ |
| 5 | Servidor MCP (`mcp` 2.x, `MCPServer`) y pruebas con cliente en memoria | ⏳ |
| 6 | Configuración verificada de Claude Code (`.mcp.json`) y OpenCode (`opencode.json`), hooks | ⏳ |
| 7 | Comandos de worktrees e integración controlada | ⏳ |
| 8 | End-to-end con dos procesos stdio, README completo, solución de problemas | ⏳ |

## Entorno verificado (2026-10-13)

- Python 3.13 (se requiere ≥ 3.12 por `sqlite3.connect(autocommit=...)`).
- SDK `mcp` 2.3.0. En 2.x `FastMCP` pasó a llamarse `MCPServer` (`mcp.server.mcpserver`). Se fija `mcp>=2.2,<3`.
- Claude Code: servidores stdio en `.mcp.json` (`mcpServers.<nombre>.command/args/env`) o con `claude mcp add --transport stdio --env K=V <nombre> -- <cmd>`.
- OpenCode: `opencode.json` → `mcp.<nombre> = { "type": "local", "command": [...], "environment": {...}, "enabled": true }`.

Las configuraciones se **probarán con los clientes reales en la etapa 6**. Hasta entonces se consideran no verificadas.

## Riesgos

| Riesgo | Mitigación |
| --- | --- |
| Los agentes no revisan su bandeja ni sus reservas por iniciativa propia | Hooks de Claude Code y plugins/`AGENTS.md` de OpenCode (ADR-0009) |
| Un agente se cae con una tarea reclamada | *Lease* con vencimiento (ADR-0005) |
| El LLM se equivoca de identidad | Identidad fijada por el proceso (ADR-0002) |
| Demasiadas herramientas degradan la elección del modelo | Superficie mínima; unir operaciones afines |
| Cambios de la API del SDK MCP | Adaptador delgado en `server.py` |
| Mensajes con *prompt injection* | Se devuelven marcados como datos de otro agente, nunca como instrucciones |

## Pruebas de aceptación (de la especificación)

1. Ambos clientes se conectan al mismo estado → etapa 6/8.
2. Mensaje persiste entre reinicios → etapa 4.
3. `claim_task` concurrente → un ganador. Patrón ya probado con 8 procesos (`tests/concurrency`); falta el servicio.
4. Reservas incompatibles rechazadas salvo excepción auditada → etapa 4.
5. Reserva expirada reutilizable y auditada → etapa 4.
6. No liberar reservas ajenas → etapa 4.
7. Dependencias bloquean → etapa 4.
8. Rutas fuera del repo rechazadas → ✅ (`tests/unit/test_paths.py`, incluye escape por enlace simbólico).
9. Worktrees separan cambios → etapa 7.
10. Conflictos Git detectados al integrar → etapa 7.
11. Pruebas con un comando (`uv run pytest`) → ✅
12. README completo → etapa 8.

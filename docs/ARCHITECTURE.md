# Arquitectura

## Idea central

MCP solo define **cómo** un agente invoca herramientas. No coordina, no despierta agentes y no impide conflictos de Git. AgentBridge implementa la coordinación explícitamente con tres piezas:

1. **Estado compartido** en un único archivo SQLite: tareas, mensajes, reservas, decisiones y auditoría.
2. **Reglas atómicas** sobre ese estado, aplicadas en transacciones `BEGIN IMMEDIATE`.
3. **Aislamiento físico**: cada agente trabaja en su propio Git worktree y rama, y la integración pasa por revisión humana.

## Diagrama

```text
 ┌────────────── Claude Code ──────────────┐   ┌─────────────── OpenCode ───────────────┐
 │ worktree: ../demo-claude  rama: claude/* │   │ worktree: ../demo-oc  rama: opencode/* │
 │                                          │   │                                         │
 │  lanza por stdio ─► proceso agentbridge  │   │  lanza por stdio ─► proceso agentbridge │
 │                     AGENT_ID=claude      │   │                     AGENT_ID=opencode   │
 └──────────────────────────┬───────────────┘   └────────────────────┬────────────────────┘
                            │  SQLite (WAL, BEGIN IMMEDIATE)         │
                            └────────────────┬───────────────────────┘
                                             ▼
                              ~/.agentbridge/<proyecto>.db
                    tareas · mensajes · reservas · decisiones · auditoría
                                             │
                                             ▼
                       Integración: diff + tests + revisión humana → merge
```

No hay un proceso servidor central: **el punto de encuentro es la base de datos** (ADR-0001).

## Capas

```text
src/agentbridge/
├── clock.py          reloj UTC inyectable (pruebas de expiración sin dormir)
├── errors.py         errores de dominio con código estable
├── storage/          conexión, migraciones, transacciones
├── domain/           modelos Pydantic, ids y validación de rutas
├── services/         proyectos/agentes, tareas, mensajería,
│                     reservas, decisiones, contexto y auditoría
└── server.py         adaptador MCP delgado sobre los servicios   (etapa 5)
```

**Por qué separar servicios del servidor MCP:** la lógica se prueba con llamadas Python directas, que son rápidas y deterministas. Si el SDK de MCP cambia (como pasó en la versión 2.0), solo se toca `server.py`.

## Identidad

El agente no declara quién es en cada llamada. El proceso se lanza con `AGENTBRIDGE_AGENT_ID` y `AGENTBRIDGE_PROJECT_ID`, y las herramientas usan esa identidad (ADR-0002).

## Flujo de un agente

1. `get_project_context` → reglas y decisiones vigentes.
2. `read_inbox` → mensajes pendientes.
3. `list_tasks` → tareas disponibles (`ready` = abiertas y con dependencias en `done`).
4. `claim_task` → atómico; recibe un *lease* que debe renovar.
5. `reserve_paths` → reservas con TTL sobre archivos o directorios.
6. Trabaja en su worktree; publica mensajes y decisiones cuando cambia un contrato.
7. `report_completion` → pasa a `review` y libera reservas.
8. Un humano revisa el diff, ejecuta los tests y hace el merge.

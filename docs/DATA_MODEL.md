# Modelo de datos

El esquema está en [`src/agentbridge/storage/migrations/0001_initial.sql`](../src/agentbridge/storage/migrations/0001_initial.sql). La versión se guarda en `PRAGMA user_version`.

## Tablas

| Tabla | Propósito |
| --- | --- |
| `projects` | Repositorio registrado, su raíz, rama por defecto y reglas |
| `agents` | Identidades por proyecto (PK compuesta `project_id, id`) |
| `tasks` | Trabajo; estado, prioridad 0–4, dueño y *lease* |
| `task_dependencies` | Grafo dirigido acíclico entre tareas |
| `task_allowed_paths` | Rutas que una tarea espera tocar |
| `task_reports` | Informes de finalización (archivos, tests, bloqueos) |
| `messages` | Mensajes dirigidos (`recipient_id`) o broadcast (`NULL`) |
| `message_receipts` | Entrega y confirmación por agente |
| `reservations` | Reservas de rutas con vencimiento |
| `decisions` | Decisiones de arquitectura, reemplazables |
| `audit_events` | Bitácora de solo inserción (protegida por triggers) |

## Estados de tarea

```text
           claim                 report_completion          aprobación humana
 open ───────────────► in_progress ───────────────► review ─────────────────► done
  ▲  ◄── release / lease vence ──┘  │  ▲                │
  │                                 ▼  │ unblock        └── cambios pedidos ──► in_progress
  │                              blocked
  └─────────────── cualquier estado no final ──────────────► cancelled
```

`ready` **no es un estado guardado**. Se calcula como `status = 'open'` y todas las dependencias en `done` (ADR-0008).

## Invariantes

| Invariante | Dónde se garantiza |
| --- | --- |
| `in_progress`/`blocked` ⇔ tiene dueño; `open` ⇒ sin dueño | `CHECK` en `tasks` |
| Una tarea no depende de sí misma | `CHECK` en `task_dependencies` |
| Sin ciclos de dependencias | Servicio, en transacción (DFS antes de insertar) |
| Un solo ganador al reclamar | `UPDATE ... WHERE assignee_id IS NULL` en `BEGIN IMMEDIATE` |
| Sin reservas activas solapadas entre agentes distintos | Servicio, en transacción, salvo excepción con `override_reason` |
| Remitentes, destinatarios y dueños existen | Claves foráneas compuestas |
| La auditoría no se altera | Triggers `BEFORE UPDATE/DELETE` |

## Fechas

Texto UTC de ancho fijo: `2026-01-01T00:00:00.000000Z`. Como todos los valores tienen la misma forma, comparar el texto equivale a comparar las fechas, y los índices funcionan sin conversiones (ADR-0007).

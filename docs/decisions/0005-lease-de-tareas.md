# ADR-0005: lease de tareas

**Estado:** aceptada

## Contexto

Si un agente se cae o su sesión termina con una tarea `in_progress`, nadie más puede reclamarla nunca.

## Decisión

`claim_task` asigna `lease_expires_at` (por defecto 30 minutos). El dueño lo renueva volviendo a llamar a `claim_task` sobre su propia tarea (idempotente) o al cambiar entre `in_progress` y `blocked`. Una tarea con *lease* vencido puede ser reclamada por otro agente. Al hacerlo se audita el evento `task.lease_expired` con el dueño anterior.

## Por qué no expirarla automáticamente

No hay un proceso en segundo plano (ADR-0001). La expiración se evalúa **de forma perezosa**: `list_tasks` muestra `lease_expired: true` y el traspaso ocurre dentro de la transacción del siguiente `claim_task` de otro agente. Las reservas siguen el mismo patrón.

## Consecuencias

- El trabajo hecho en el worktree del agente caído no se pierde, pero el nuevo dueño debe revisarlo. El mensaje de auditoría lo indica.

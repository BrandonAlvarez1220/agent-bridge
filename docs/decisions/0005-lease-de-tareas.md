# ADR-0005: lease de tareas

**Estado:** aceptada

## Contexto

Si un agente se cae o su sesión termina con una tarea `in_progress`, nadie más puede reclamarla nunca.

## Decisión

`claim_task` asigna `lease_expires_at` (por defecto 30 minutos). Cualquier operación del dueño sobre la tarea lo renueva. Una tarea con *lease* vencido puede ser reclamada por otro agente. Al hacerlo se audita el evento `task.lease_expired` con el dueño anterior.

## Por qué no expirarla automáticamente

No hay un proceso en segundo plano (ADR-0001). La expiración se evalúa **de forma perezosa**, dentro de la transacción del siguiente `claim_task` o `list_tasks`. Es el mismo patrón que usan las reservas.

## Consecuencias

- El trabajo hecho en el worktree del agente caído no se pierde, pero el nuevo dueño debe revisarlo. El mensaje de auditoría lo indica.

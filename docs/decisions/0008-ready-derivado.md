# ADR-0008: `ready` se calcula

**Estado:** aceptada

## Contexto

La especificación propone los estados `backlog` y `ready`. Si `ready` se almacenara, habría que actualizarlo en cascada cada vez que una dependencia pasa a `done` (o deja de estarlo). Cualquier olvido dejaría datos inconsistentes.

## Decisión

Se guarda `open`. Una tarea está **lista** si `status = 'open'` y todas sus dependencias están en `done`. Se calcula en la consulta. `claim_task` rechaza las tareas que no están listas e indica qué dependencias faltan.

`backlog` (idea no priorizada) se omite en el MVP. La prioridad 0–4 cubre esa necesidad.

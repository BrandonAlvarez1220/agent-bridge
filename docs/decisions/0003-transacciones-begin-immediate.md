# ADR-0003: escrituras en `BEGIN IMMEDIATE`

**Estado:** aceptada

## Contexto

Reclamar una tarea o reservar una ruta sigue el patrón "leer → decidir → escribir". Con un `BEGIN` normal (diferido), dos procesos pueden leer "libre" al mismo tiempo. Después ambos intentan escribir: uno recibe `SQLITE_BUSY` o, peor, los dos actúan sobre datos viejos.

Además, el módulo `sqlite3` de Python abre transacciones implícitas por su cuenta en el modo heredado, y eso dificulta saber dónde empieza cada una.

## Decisión

- Conexión con `autocommit=True` (Python ≥ 3.12): el módulo no abre transacciones solo.
- Todas las operaciones que escriben se envuelven en `transaction(conn)`, que emite `BEGIN IMMEDIATE`.
- `busy_timeout = 5000 ms`: si otro proceso tiene el lock, se espera en lugar de fallar.
- Cuando se pueda, la decisión se codifica en el propio `UPDATE` (`... WHERE assignee_id IS NULL`) y se revisa `rowcount`.

## Por qué funciona

`BEGIN IMMEDIATE` toma el lock de escritura **al empezar**. Mientras una transacción decide, ninguna otra puede empezar a escribir, así que las decisiones se ejecutan una tras otra. Es equivalente a `SELECT ... WITH (UPDLOCK, HOLDLOCK)` en SQL Server.

## Consecuencias

- Las transacciones deben ser cortas: nunca llamar a Git ni a la red dentro de una.
- `tests/concurrency/test_immediate_transactions.py` lanza 8 procesos contra la misma tarea y verifica que gana exactamente uno.

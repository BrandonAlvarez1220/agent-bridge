# Decisiones de arquitectura (ADR)

Cada ADR registra una decisión, el contexto que la motivó y sus consecuencias. Si una decisión cambia, no se borra: se escribe una nueva que la reemplaza.

| # | Decisión |
| --- | --- |
| [0001](0001-stdio-y-sqlite-compartido.md) | Transporte stdio con un proceso por agente y SQLite compartido |
| [0002](0002-identidad-fijada-por-proceso.md) | La identidad del agente la fija el proceso, no los parámetros |
| [0003](0003-transacciones-begin-immediate.md) | Escrituras en `BEGIN IMMEDIATE` con autocommit controlado |
| [0004](0004-rutas-sin-globs.md) | Reservas por archivo o directorio, sin globs |
| [0005](0005-lease-de-tareas.md) | Las tareas reclamadas tienen un *lease* con vencimiento |
| [0006](0006-estados-de-mensaje.md) | Mensajes: publicado, entregado, confirmado |
| [0007](0007-tiempo-utc-y-reloj-inyectable.md) | Tiempo UTC en texto de ancho fijo y reloj inyectable |
| [0008](0008-ready-derivado.md) | `ready` se calcula, no se almacena |
| [0009](0009-hooks-para-cumplimiento.md) | Hooks del cliente para que el protocolo se cumpla de verdad |
| [0010](0010-sdk-mcp-2x.md) | SDK oficial `mcp` 2.x |

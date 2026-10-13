# ADR-0007: tiempo UTC y reloj inyectable

**Estado:** aceptada

## Decisión

- Las fechas se guardan como texto UTC de ancho fijo: `YYYY-MM-DDTHH:MM:SS.ffffffZ`.
- La hora la genera Python mediante un `Clock` inyectable, no `CURRENT_TIMESTAMP` de SQLite.

## Por qué

- Con ancho fijo, `'a' < 'b'` en texto equivale a `a < b` en tiempo. Las consultas de vencimiento (`expires_at > ?`) funcionan con índices y sin funciones de fecha.
- Con un reloj inyectable, las pruebas de expiración avanzan el tiempo con `clock.advance(60)` en vez de dormir. Así son rápidas y deterministas.
- Todos los procesos corren en la misma máquina y comparten reloj, así que no hay desfase entre ellos.

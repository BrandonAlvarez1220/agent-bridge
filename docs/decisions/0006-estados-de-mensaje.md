# ADR-0006: estados de un mensaje

**Estado:** aceptada

## Contexto

La especificación pide distinguir entre mensaje publicado, entregado, leído y confirmado.

## Decisión

| Estado | Significado verificable |
| --- | --- |
| Publicado | Existe la fila en `messages` |
| Entregado | `delivered_at`: el servidor lo incluyó en una respuesta de `read_inbox` |
| Confirmado | `acknowledged_at`: el agente llamó a `acknowledge_message` |

**"Leído" no se registra.** Que un LLM haya "leído" un mensaje no es observable desde el servidor: lo único que sabemos es que se lo enviamos. Registrar "leído" sería afirmar algo que no podemos comprobar, y la especificación pide justamente no hacerlo.

## Otras reglas

- Los broadcast crean el recibo de forma perezosa, al entregarse a cada agente.
- `read_inbox` devuelve los mensajes envueltos como *datos de otro agente*, nunca como instrucciones (defensa contra *prompt injection*).
- Cuerpo máximo: 8000 caracteres.

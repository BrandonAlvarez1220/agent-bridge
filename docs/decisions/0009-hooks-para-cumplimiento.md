# ADR-0009: hooks del cliente para cumplir el protocolo

**Estado:** propuesta (se valida en la etapa 6)

## Contexto

Un mensaje persistente no hace que un agente lo lea. Una reserva no impide que un agente edite el archivo. Si el protocolo depende de que el LLM "se acuerde" de llamar a `read_inbox` o `list_reservations`, se va a incumplir.

## Propuesta

- **Claude Code:** hooks configurados en `.claude/settings.json`:
  - Al iniciar sesión o enviar un prompt: inyectar como contexto los mensajes no confirmados.
  - Antes de `Edit`/`Write`: consultar las reservas y **bloquear** la edición si la ruta está reservada por otro agente. Así la reserva deja de ser cooperativa para Claude.
- **OpenCode:** instrucciones en `AGENTS.md` y, si su sistema de plugins actual lo permite, un plugin equivalente.
- Un comando `agentbridge` (CLI) que los hooks puedan invocar sin pasar por MCP.

## Pendiente

Verificar contra la documentación vigente de ambos clientes los eventos y formatos disponibles antes de implementar.

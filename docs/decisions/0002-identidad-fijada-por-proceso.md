# ADR-0002: la identidad la fija el proceso

**Estado:** aceptada

## Contexto

La especificación propone que herramientas como `claim_task` o `release_reservation` reciban `agent_id`. Ese valor lo escribe el LLM, que puede equivocarse o hacerse pasar por otro agente. Entonces la regla "no puedes liberar la reserva de otro" no protege nada.

## Decisión

El proceso se lanza con `AGENTBRIDGE_PROJECT_ID` y `AGENTBRIDGE_AGENT_ID`, que vienen de la configuración del cliente (`env` en `.mcp.json` o `environment` en `opencode.json`). Las herramientas MCP **no aceptan** `agent_id` ni `project_id`. Los servicios internos sí los reciben, para poder probarlos.

## Consecuencias

- El modelo no puede cambiar de identidad desde una conversación.
- Las herramientas tienen menos parámetros y el modelo comete menos errores.
- No es autenticación: quien edite la configuración local puede elegir cualquier identidad. Para un MVP local de un solo usuario eso es aceptable.

# Seguridad

AgentBridge es una herramienta **local y de un solo usuario**. El modelo de amenazas no incluye a un atacante con acceso a tu máquina. Incluye tres riesgos más probables en el trabajo diario con agentes:

1. **Un LLM equivocado:** pasa una ruta absurda, se confunde de identidad o insiste en editar algo ajeno.
2. **Prompt injection entre agentes:** un agente procesa contenido malicioso (un README, una issue) y lo propaga en un mensaje a otro agente.
3. **Fallos de concurrencia:** dos procesos deciden a la vez sobre el mismo recurso.

## Defensas

| Riesgo | Defensa | Dónde |
| --- | --- | --- |
| Suplantación de agente | La identidad viene del entorno del proceso; ninguna herramienta acepta `agent_id` | ADR-0002, `test_tool_surface_never_accepts_identity` |
| Path traversal | Rutas relativas validadas: sin `..`, absolutas, comodines, caracteres de control ni `.git/` (tampoco `.GIT/`) | `domain/paths.py` |
| Escape por enlace simbólico | La ruta se resuelve en disco y debe quedar dentro de la raíz | `ensure_within`, `test_symlink_escape_is_rejected` |
| Inyección de comandos | Git y las pruebas se ejecutan con lista de argumentos, sin shell; el plugin usa el `$` de Bun, que escapa cada argumento | `gitutil.py`, `integration.py` |
| Inyección de argumentos | Ramas validadas con `git check-ref-format`; se rechaza el `-` inicial | `worktrees.validate_branch` |
| Prompt injection en mensajes | Los mensajes se entregan rotulados como `untrusted_peer_content`; el protocolo y las instrucciones del servidor dicen que son datos, no órdenes; el servidor nunca ejecuta nada que venga en un mensaje | ADR-0006, `PROTOCOL.md` |
| Condiciones de carrera | `BEGIN IMMEDIATE` y `UPDATE` condicionales; lock por proceso frente a los hilos del SDK | ADR-0003, `tests/concurrency` |
| Secretos en la auditoría | Claves con aspecto de secreto (`token`, `password`, `api_key`…) se redactan; el cuerpo de los mensajes no se copia a la auditoría | `services/audit.py` |
| Abuso de tamaño | Límites en mensajes (8000), textos, listas, TTL y *leases* | servicios y esquemas de herramientas |
| Fusión no revisada | `integrate merge` exige `--yes`, veredicto `ready`, `main` limpio y base sin cambios; nunca hace push | ADR-0011 |
| Identidad del agente en commits | La configuración por agente se escribe solo en archivos no versionados y se agrega a `info/exclude` | `clients.py` |

## Lo que AgentBridge no protege

- **Las reservas no son un bloqueo del sistema de archivos.** Un editor, la terminal o un cliente sin hooks puede escribir cualquier archivo. La protección final es la revisión del diff.
- **Política fail-open:** si AgentBridge falla, los hooks permiten la edición. Se prefiere no paralizar el trabajo.
- **Sin autenticación:** quien pueda editar la configuración local o la base SQLite puede hacerse pasar por cualquier agente.
- **No expongas el servidor por red.** Solo funciona por stdio. Un transporte HTTP futuro exigiría autenticación y TLS.
- **El comando de pruebas de `integrate` es código tuyo:** se ejecuta con tus permisos, sobre el código que escribieron los agentes. Revisa el diff antes de probar cambios de origen dudoso.

## Reportar un problema

Abre una issue en el repositorio sin incluir secretos ni rutas privadas.

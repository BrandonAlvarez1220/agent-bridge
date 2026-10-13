# AgentBridge

Infraestructura local para que varios agentes de programación —inicialmente **Claude Code** y **OpenCode**— colaboren en un mismo repositorio sin pisarse cambios, perder decisiones ni duplicar trabajo.

AgentBridge no une los modelos ni comparte sus conversaciones. Les da un **estado compartido y verificable**: tareas, mensajes, decisiones, dependencias, reservas temporales de rutas y una bitácora de auditoría. Todo vive en una base SQLite y se expone mediante un servidor MCP.

> **Estado:** en desarrollo (etapa 4 de 8). El núcleo funciona y está probado como biblioteca Python: tareas con reclamación atómica y *lease*, dependencias sin ciclos, mensajería con confirmaciones, reservas de rutas con vencimiento, decisiones y auditoría. El servidor MCP y la configuración de clientes **aún no están implementados**. Ver [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Qué garantiza y qué no

| Garantiza (probado en el núcleo; falta exponerlo por MCP) | No garantiza |
| --- | --- |
| Una tarea reclamada tiene un solo dueño, aunque dos agentes compitan por ella | Que un agente consulte su bandeja si nadie se lo pide (ver ADR-0009) |
| Los mensajes persisten aunque el destinatario esté apagado | Que un archivo reservado no se edite por fuera del protocolo |
| No se crean reservas solapadas sin una excepción auditada | Que no haya conflictos de Git o semánticos al integrar |

## Desarrollo

Requiere Python 3.12+ y [uv](https://docs.astral.sh/uv/).

```bash
uv sync            # crea .venv e instala dependencias
uv run pytest      # ejecuta todas las pruebas
uv run ruff check  # lint
```

## Documentación

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): componentes, flujo y diagrama.
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md): tablas, estados e invariantes.
- [`docs/decisions/`](docs/decisions/): decisiones de arquitectura (ADR) y el *por qué* de cada una.
- [`docs/ROADMAP.md`](docs/ROADMAP.md): etapas, riesgos y pruebas de aceptación.

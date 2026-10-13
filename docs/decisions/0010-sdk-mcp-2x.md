# ADR-0010: SDK oficial `mcp` 2.x

**Estado:** aceptada

## Contexto

El SDK oficial de Python publicó la versión 2.0 en julio de 2026. Esa versión renombró `FastMCP` a `MCPServer` (`mcp.server.mcpserver`) y cambió otras APIs. La mayoría de los ejemplos en internet todavía usan la 1.x.

## Decisión

Fijar `mcp>=2.2,<3`. La versión 2.2 lleva más de un mes publicada, y la 2.3.0 (2 de octubre de 2026) es compatible.

## Por qué

- Empezar en 1.x obligaría a migrar pronto.
- El cliente 2.x (`mcp.Client`) acepta una instancia de `MCPServer` en memoria, lo que permite pruebas de integración sin subprocesos.
- Toda la dependencia del SDK queda en `server.py`, así que una 3.x afectaría un solo archivo.

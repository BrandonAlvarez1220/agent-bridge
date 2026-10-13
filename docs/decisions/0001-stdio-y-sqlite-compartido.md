# ADR-0001: transporte stdio con SQLite compartido

**Estado:** aceptada

## Contexto

Claude Code y OpenCode pueden lanzar servidores MCP locales por stdio: cada cliente inicia **su propio proceso**. La alternativa es un servidor HTTP local de larga vida al que ambos se conecten.

## Decisión

Usar stdio. Cada agente tiene su propio proceso `agentbridge`, y todos abren el **mismo archivo SQLite**.

## Por qué

- No hay un servidor que arrancar, vigilar ni reiniciar. Si un cliente se cierra, su proceso muere y nada queda colgado.
- Las garantías de concurrencia ya no dependen de que exista un único proceso servidor, sino de SQLite. SQLite está diseñado para esto: WAL permite varios lectores con un escritor, y `BEGIN IMMEDIATE` ordena las escrituras una tras otra.
- No hay que abrir puertos de red, así que no hace falta autenticación ni TLS en el MVP.

## Consecuencias

- La frase "servidor común" de la especificación significa en realidad **estado común**.
- Las pruebas de concurrencia deben usar **procesos** separados, no solo hilos.
- Si en el futuro hiciera falta un panel web o agentes remotos, se añade un transporte HTTP sobre los mismos servicios sin cambiar el modelo.

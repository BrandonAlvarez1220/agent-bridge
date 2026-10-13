# ADR-0004: reservas por archivo o directorio, sin globs

**Estado:** aceptada

## Contexto

Hay que detectar reservas solapadas. Entre rutas literales es trivial. Entre globs (`src/**/*.ts` frente a `src/*/login.*`) es un problema de intersección de lenguajes, difícil de hacer bien y fácil de hacer mal.

Además, cada agente trabaja en un worktree distinto, así que las rutas absolutas no coinciden entre agentes.

## Decisión

- Se guardan **rutas relativas a la raíz del repositorio**, con `/` como separador.
- Se aceptan dos formas: archivo (`src/auth/login.py`) o directorio (`src/auth/`, con `/` final, que cubre todo lo que contiene).
- Dos reservas se solapan si son iguales o si una es un directorio prefijo de la otra.
- Se rechazan rutas absolutas, `..`, rutas vacías, comodines (`* ? [`), `.git/` y rutas que, resueltas en el worktree, salgan del repositorio por un enlace simbólico.

## Consecuencias

- La detección de solapamiento es exacta y fácil de probar.
- Reservar "todos los `.ts`" no es posible. Se reserva el directorio, que es más grueso pero seguro.

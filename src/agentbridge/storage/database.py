"""Conexión a SQLite, migraciones y transacciones.

Decisiones clave (ver docs/decisions):

* Cada proceso MCP (uno por agente, transporte stdio) abre su propia conexión al
  mismo archivo. La concurrencia real ocurre aquí, no en el servidor (ADR-0001).
* ``journal_mode=WAL`` permite lectores concurrentes con un escritor.
* ``busy_timeout`` hace que un escritor espere en lugar de fallar al instante.
* Las operaciones de escritura usan ``BEGIN IMMEDIATE``: toman el lock de
  escritura al empezar, de modo que "leer, decidir, escribir" no puede
  intercalarse con otro proceso (ADR-0003).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

BUSY_TIMEOUT_MS = 5000


def connect(path: str | Path) -> sqlite3.Connection:
    """Abre una conexión configurada. ``path`` puede ser ``":memory:"`` en pruebas."""
    # autocommit=True: el módulo sqlite3 no abre transacciones implícitas; las
    # controlamos nosotros con transaction(). Con el modo heredado, Python emite
    # BEGIN diferidos por su cuenta y eso rompe la atomicidad que buscamos.
    conn = sqlite3.connect(
        str(path),
        autocommit=True,
        timeout=BUSY_TIMEOUT_MS / 1000,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
    """Transacción explícita. ``immediate=True`` para cualquier operación que escriba."""
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _migration_files() -> list[tuple[int, str, str]]:
    folder = resources.files("agentbridge.storage") / "migrations"
    found = []
    for entry in folder.iterdir():
        name = entry.name
        if not name.endswith(".sql"):
            continue
        version = int(name.split("_", 1)[0])
        found.append((version, name, entry.read_text(encoding="utf-8")))
    found.sort()
    versions = [v for v, _, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError(f"migraciones no consecutivas: {versions}")
    return found


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    """Aplica las migraciones pendientes. Idempotente. Devuelve la versión final.

    La versión se guarda en ``PRAGMA user_version``. Cada migración y su cambio
    de versión van en la misma transacción, así que una migración fallida no deja
    el esquema a medias. BEGIN IMMEDIATE evita que dos procesos migren a la vez.
    """
    for version, _name, sql in _migration_files():
        with transaction(conn):
            if schema_version(conn) >= version:
                continue
            for statement in _split_statements(sql):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {version}")
    return schema_version(conn)


def _split_statements(sql: str) -> list[str]:
    """Divide un script en sentencias completas.

    No usamos ``executescript`` porque emite un COMMIT implícito y sacaría la
    migración de nuestra transacción. ``sqlite3.complete_statement`` sabe
    manejar los ``;`` dentro de cuerpos de triggers.
    """
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        if not buffer and line.strip().startswith("--"):
            continue
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise RuntimeError(f"sentencia SQL incompleta al final de la migración: {buffer[:80]!r}")
    return statements


def open_database(path: str | Path) -> sqlite3.Connection:
    """Conecta y deja el esquema al día."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    migrate(conn)
    return conn

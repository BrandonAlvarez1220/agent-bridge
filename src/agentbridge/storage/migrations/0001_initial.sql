-- AgentBridge — esquema inicial.
-- Convenciones:
--   * Fechas en UTC, texto ISO-8601 con sufijo 'Z' (ver ADR-0007). Se comparan
--     como texto, lo cual es correcto porque el formato es de ancho fijo.
--   * Las reglas que SQLite no puede expresar de forma declarativa (ciclos de
--     dependencias, reservas solapadas) se aplican en transacciones
--     BEGIN IMMEDIATE dentro de los servicios (ver ADR-0003).

CREATE TABLE projects (
    id              TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 64),
    name            TEXT NOT NULL,
    repository_root TEXT NOT NULL UNIQUE,
    default_branch  TEXT NOT NULL DEFAULT 'main',
    rules           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);

CREATE TABLE agents (
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    id           TEXT NOT NULL CHECK (length(id) BETWEEN 1 AND 64),
    agent_type   TEXT NOT NULL,
    workspace    TEXT,
    branch       TEXT,
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (project_id, id)
);

CREATE TABLE tasks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title            TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    description      TEXT NOT NULL DEFAULT '',
    -- 'ready' no se almacena: se deriva de las dependencias (ADR-0008).
    status           TEXT NOT NULL DEFAULT 'open'
                     CHECK (status IN ('open', 'in_progress', 'blocked', 'review', 'done', 'cancelled')),
    priority         INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 0 AND 4),
    assignee_id      TEXT,
    -- Lease de la tarea reclamada (ADR-0005). NULL si no está reclamada.
    lease_expires_at TEXT,
    created_by       TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    completed_at     TEXT,
    FOREIGN KEY (project_id, assignee_id) REFERENCES agents(project_id, id),
    -- Una tarea en curso o bloqueada siempre tiene dueño; una abierta nunca.
    CHECK ((status IN ('in_progress', 'blocked')) = (assignee_id IS NOT NULL)
           OR status IN ('review', 'done', 'cancelled'))
);
CREATE INDEX idx_tasks_project_status ON tasks(project_id, status);
CREATE INDEX idx_tasks_assignee ON tasks(project_id, assignee_id);

CREATE TABLE task_dependencies (
    task_id            INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, depends_on_task_id),
    CHECK (task_id <> depends_on_task_id)
);
CREATE INDEX idx_task_deps_reverse ON task_dependencies(depends_on_task_id);

CREATE TABLE task_allowed_paths (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    path    TEXT NOT NULL,
    PRIMARY KEY (task_id, path)
);

CREATE TABLE task_reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    agent_id     TEXT NOT NULL,
    summary      TEXT NOT NULL,
    changed_files TEXT NOT NULL DEFAULT '[]',  -- JSON array
    tests_run    TEXT NOT NULL DEFAULT '[]',   -- JSON array
    test_results TEXT NOT NULL DEFAULT '',
    blockers     TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE TABLE messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    sender_id    TEXT NOT NULL,
    recipient_id TEXT,              -- NULL = broadcast a todos los agentes del proyecto
    task_id      INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    message_type TEXT NOT NULL
                 CHECK (message_type IN ('info', 'question', 'dependency', 'conflict',
                                         'handoff', 'review_request', 'urgent')),
    body         TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 8000),
    created_at   TEXT NOT NULL,
    FOREIGN KEY (project_id, sender_id) REFERENCES agents(project_id, id),
    FOREIGN KEY (project_id, recipient_id) REFERENCES agents(project_id, id)
);
CREATE INDEX idx_messages_recipient ON messages(project_id, recipient_id, id);

-- Estados de un mensaje (ADR-0006):
--   publicado  = existe la fila en messages
--   entregado  = delivered_at: el servidor lo devolvió en una respuesta de read_inbox
--   confirmado = acknowledged_at: el agente llamó explícitamente a acknowledge_message
-- "Leído por el modelo" no es observable desde el servidor; no se registra.
CREATE TABLE message_receipts (
    message_id      INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    project_id      TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    delivered_at    TEXT,
    acknowledged_at TEXT,
    PRIMARY KEY (message_id, agent_id),
    FOREIGN KEY (project_id, agent_id) REFERENCES agents(project_id, id)
);

CREATE TABLE reservations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_id      TEXT NOT NULL,
    task_id       INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    -- Ruta relativa al repo, normalizada con '/'. Termina en '/' si es un
    -- directorio (prefijo). No se admiten globs (ADR-0004).
    path          TEXT NOT NULL,
    -- Justificación de una excepción a un solapamiento; NULL si no hubo excepción.
    override_reason TEXT,
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    released_at   TEXT,
    expired_logged_at TEXT,  -- cuándo se auditó su expiración
    FOREIGN KEY (project_id, agent_id) REFERENCES agents(project_id, id),
    CHECK (expires_at > created_at)
);
CREATE INDEX idx_reservations_active ON reservations(project_id, released_at, expires_at);

CREATE TABLE decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title           TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    decision        TEXT NOT NULL,
    rationale       TEXT NOT NULL,
    affected_paths  TEXT NOT NULL DEFAULT '[]',  -- JSON array
    task_id         INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    author_agent_id TEXT,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded')),
    superseded_by   INTEGER REFERENCES decisions(id),
    created_at      TEXT NOT NULL
);
CREATE INDEX idx_decisions_project ON decisions(project_id, status);

-- Bitácora de solo inserción. Ningún servicio actualiza ni borra filas aquí.
CREATE TABLE audit_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   TEXT NOT NULL,
    actor_id     TEXT,
    event_type   TEXT NOT NULL,
    entity_type  TEXT NOT NULL,
    entity_id    TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);
CREATE INDEX idx_audit_project_time ON audit_events(project_id, id);

CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events es de solo inserción'); END;

CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events es de solo inserción'); END;

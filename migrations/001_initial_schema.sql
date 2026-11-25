CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('solicitante', 'tecnico'))
);
ALTER TABLE users ADD COLUMN username TEXT COLLATE NOCASE;
ALTER TABLE users ADD COLUMN can_manage_users INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN provisioned INTEGER NOT NULL DEFAULT 1;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, protocol TEXT NOT NULL UNIQUE,
    requester TEXT NOT NULL, email TEXT NOT NULL, title TEXT NOT NULL,
    description TEXT NOT NULL, category TEXT NOT NULL, priority TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Aberto', created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, resolved_at TEXT
);
ALTER TABLE tickets ADD COLUMN owner_id INTEGER REFERENCES users(id);
ALTER TABLE tickets ADD COLUMN assignee_id INTEGER REFERENCES users(id);
ALTER TABLE tickets ADD COLUMN first_response_at TEXT;
ALTER TABLE tickets ADD COLUMN is_sample INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS timeline (
    id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    actor TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,
    internal INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_timeline_ticket ON timeline(ticket_id, id);
CREATE INDEX IF NOT EXISTS idx_tickets_owner ON tickets(owner_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created_at DESC);

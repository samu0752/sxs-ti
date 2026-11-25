ALTER TABLE users ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN locked_until TEXT;
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, action TEXT NOT NULL,
    outcome TEXT NOT NULL, actor_id INTEGER REFERENCES users(id), detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_audit_created ON audit_logs(created_at);
CREATE TABLE assets (
    id INTEGER PRIMARY KEY, tag TEXT NOT NULL UNIQUE COLLATE NOCASE,
    equipment TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id)
);
ALTER TABLE tickets ADD COLUMN asset_id INTEGER REFERENCES assets(id);
ALTER TABLE tickets ADD COLUMN ticket_type TEXT NOT NULL DEFAULT 'Incidente'
    CHECK(ticket_type IN ('Incidente', 'Requisição', 'Problema'));
ALTER TABLE tickets ADD COLUMN paused_minutes REAL NOT NULL DEFAULT 0;
ALTER TABLE tickets ADD COLUMN paused_at TEXT;
ALTER TABLE tickets ADD COLUMN sla_priority TEXT;
ALTER TABLE tickets ADD COLUMN escalated_at TEXT;
ALTER TABLE tickets ADD COLUMN csat INTEGER CHECK(csat BETWEEN 1 AND 5);
UPDATE tickets SET sla_priority = priority;
UPDATE tickets SET status = 'Aguardando solicitante', paused_at = updated_at
WHERE status = 'Aguardando usuário';
CREATE TABLE sla_pauses (
    id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL, ended_at TEXT
);
CREATE UNIQUE INDEX idx_sla_open_pause ON sla_pauses(ticket_id) WHERE ended_at IS NULL;
INSERT INTO sla_pauses(ticket_id, started_at) SELECT id, paused_at FROM tickets
WHERE paused_at IS NOT NULL;
CREATE TABLE attachments (
    id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    uploader_id INTEGER NOT NULL REFERENCES users(id), filename TEXT NOT NULL,
    mime_type TEXT NOT NULL, content BLOB NOT NULL CHECK(length(content) BETWEEN 1 AND 2097152),
    created_at TEXT NOT NULL
);
CREATE INDEX idx_attachments_ticket ON attachments(ticket_id);
CREATE TABLE kb_articles (
    id INTEGER PRIMARY KEY, category TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
    author_id INTEGER REFERENCES users(id), UNIQUE(category, title)
);
INSERT INTO kb_articles(category, title, body) VALUES
    ('Rede e conectividade', 'VPN desconectando',
     'Confirme a conexão à internet e reconecte à VPN. Se persistir, informe o horário do erro.'),
    ('Acesso e contas', 'Acesso à conta corporativa',
     'Confirme o identificador da conta e registre a mensagem de erro. Nunca informe sua senha.');
CREATE TABLE macros (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL UNIQUE COLLATE NOCASE, body TEXT NOT NULL
);
INSERT INTO macros(title, body) VALUES
    ('Solicitar detalhes', 'Pode enviar o horário da ocorrência e a mensagem exibida na tela?'),
    ('Confirmar solução', 'Aplicamos a correção. Pode confirmar se o acesso voltou a funcionar?');
CREATE TABLE ticket_links (
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    related_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('duplicado', 'relacionado')),
    CHECK(ticket_id < related_id), PRIMARY KEY(ticket_id, related_id)
);
CREATE VIEW vw_relatorio_chamados AS
SELECT t.id AS chamado_id, t.protocol AS protocolo, t.ticket_type AS tipo,
       t.category AS categoria, t.priority AS prioridade,
       COALESCE(t.sla_priority, t.priority) AS prioridade_sla,
       t.status, t.created_at AS aberto_em, t.first_response_at AS primeira_resposta_em,
       t.resolved_at AS resolvido_em, t.paused_minutes AS minutos_pausados,
       t.csat, t.is_sample AS demonstracao, a.tag AS patrimonio, u.name AS tecnico
FROM tickets AS t LEFT JOIN assets AS a ON a.id = t.asset_id
LEFT JOIN users AS u ON u.id = t.assignee_id;

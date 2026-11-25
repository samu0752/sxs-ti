# Changelog

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
O projeto usa [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [1.1.0] - 2026-11-25

### Adicionado

- Painel administrativo de trilha de auditoria (`/administracao/auditoria`) com filtros por ação e resultado.
- Registro de auditoria para logout e exportações CSV de usuários autenticados.
- Cabeçalhos de segurança `Referrer-Policy` e `Permissions-Policy`, além de rate limiting em `/criar-conta`.
- Tratador global de erro HTTP 500 com log estruturado sem vazamento de detalhes internos.
- Relatório analítico completo em `scripts/analise_chamados.py` com KPIs de SLA, MTTR, 1ª resposta, CSAT, `--escopo` e `--exportar-json`.
- Testes unitários independentes para núcleo de SLA, migrações e analytics (`tests/test_core_and_analytics.py`).

### Corrigido e Estabilizado

- SQLite configurado com `PRAGMA journal_mode = WAL`, `synchronous = NORMAL` e `busy_timeout = 10000` para maior estabilidade sob concorrência.
- Normalização de timestamps legados via `parse_date()` em `datetime_br()` e `average_minutes()`, evitando `TypeError` entre datas naive e aware.
- Fallback seguro de prioridade em `sla_for()`, normalização de e-mail em minúsculas na abertura de chamados, expiração de sessão (8h) e limpeza de sessão órfã.

## [1.0.0] - 2026-11-25

### Adicionado

- Classificação ITIL, SLA em horário comercial, pausa e escalonamento.
- Anexos validados, inventário, vínculos, macros, artigos e CSAT.
- Indicadores, visão SQL, CSV, análise dos dados fictícios e reset do demo.
- Auditoria, bloqueio por conta, limitação por IP e cabeçalhos de segurança.
- Migrações versionadas, testes de segurança, CI e cobertura.

### Alterado

- O deploy usa Gunicorn a partir das dependências fixadas e consulta `/health`.
- O status de espera aparece como **Aguardando solicitante**. Chamados antigos são migrados.

# Changelog

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
O projeto usa [Versionamento Semântico](https://semver.org/lang/pt-BR/).

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

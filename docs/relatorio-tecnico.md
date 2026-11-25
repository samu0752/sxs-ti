# Arquitetura técnica

O Flask recebe formulários e renderiza as páginas Jinja2. `app.py` contém as rotas, controle de escopo, eventos e operações do atendimento. `sxs_core.py` concentra migrações, agenda de SLA e validação de arquivos. O JavaScript estático usa o endpoint de sugestões e preenche macros escolhidas pelo técnico.

O SQLite habilita chaves estrangeiras por conexão. A criação do chamado inclui o registro de histórico e o eventual anexo na mesma transação. Alterações do status e das pausas também são atômicas. Os binários ficam junto ao chamado e são servidos por uma rota autorizada.

O apoio ao deploy inclui `/health`, logs JSON e configuração Gunicorn para o Render. O esquema começa em `migrations/001_initial_schema.sql` e recebe as extensões de atendimento pela 002. A view `vw_relatorio_chamados` é a base dos relatórios. [Relatórios](RELATORIOS.md)

# Operação e persistência

## Ambiente

Use Python 3.11+ e instale `requirements.txt`. Exporte as variáveis documentadas em `.env.example` no shell ou na plataforma. O Flask pode ler arquivos dotenv quando python-dotenv estiver instalado.

- Operação local usa `instance/chamados.db`, ou o caminho de `TICKETS_DATABASE`.
- O modo de demonstração usa `instance/demonstracao.db`, ou `DEMO_DATABASE`. Mantenha os bancos separados.
- Com `LOAD_SAMPLE_DATA=1`, um banco vazio recebe exemplos. Na demonstração, o reset repõe esses exemplos e preserva os demais registros.
- Em HTTPS, configure uma `SECRET_KEY` persistente, `SESSION_COOKIE_SECURE=1` e, somente atrás do proxy confiável do Render, `TRUST_RENDER_PROXY=1`.

Crie um administrador ou técnico com `python -m flask --app app criar-usuario`. Os solicitantes podem se cadastrar em `/criar-conta`. `preparar-teste` é destinado ao banco local padrão.

## Banco e atualização

Faça uma cópia do SQLite antes de atualizar. O início da aplicação executa as migrações pendentes dentro de uma transação e registra cada versão em `schema_version`. A migração 001 também aceita colunas já criadas pelo SXS TI antigo.

`/health` e `/saude` verificam o acesso ao esquema. Use `python -m flask --app app processar-sla` em agendador da instalação operacional quando precisar de escalonamento mesmo sem tráfego. O painel e a ficha também disparam o processamento.

No Render, a configuração inclui Gunicorn em um processo, com quatro threads e `/health` como verificação. O plano Free tem disco efêmero e se destina a exemplos. Para operação persistente, faça backup com a aplicação parada ou use a ferramenta de backup do SQLite.

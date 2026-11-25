# Testes e validação

Execute `python -m unittest discover -s tests -v`, ou `make test` para medir cobertura com ramificações.

| Suíte | Cenários |
|---|---|
| `test_app.py` | Compatibilidade com abertura, busca, resposta, administração, demo e rotas originais |
| `test_security_idor.py` | Isolamento de chamados, bloqueio por conta, limite por IP e cabeçalhos |
| `test_attachments.py` | Extensão, assinatura, nomes, tamanho, atomicidade e acesso ao download |
| `test_sla_and_features.py` | Fim de semana, pausas, escalonamento, CSAT, ativos, sugestões, CSV, migração e reset |

O CI executa as verificações em Python 3.11 e 3.13 e publica cobertura XML.

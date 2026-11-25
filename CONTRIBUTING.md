# Contribuição

1. Crie um ambiente virtual de Python 3.11+ e instale `requirements-dev.txt`.
2. Execute `python -m pre_commit install` e abra uma branch da tarefa.
3. Use `make lint` e `make test`, ou os comandos do [README](README.md).
4. Para mudanças de esquema, adicione uma migração numerada e teste com o banco legado e um banco vazio.
5. Use Conventional Commits e descreva os fluxos afetados, a verificação feita e qualquer mudança de configuração no PR.

A suíte original em `tests/test_app.py` serve como referência de compatibilidade.

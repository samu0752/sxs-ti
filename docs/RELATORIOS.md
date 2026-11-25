# Relatórios de chamados

A migração 002 cria a view `vw_relatorio_chamados`. Ela retorna uma linha por chamado, mesmo sem responsável ou ativo vinculado. Datas da aplicação são gravadas em ISO 8601 e os registros novos usam UTC.

| Campos | Significado |
|---|---|
| `chamado_id`, `protocolo` | Chave interna e identificador exibido |
| `tipo`, `categoria`, `status` | Classificação e estado atual |
| `prioridade`, `prioridade_sla` | Prioridade de fila e prioridade usada no orçamento original |
| `aberto_em`, `primeira_resposta_em`, `resolvido_em` | Marcos do chamado |
| `minutos_pausados` | Minutos úteis de pausas já encerradas |
| `csat` | Nota de 1 a 5, quando respondida |
| `demonstracao` | 1 para exemplos fictícios |
| `patrimonio`, `tecnico` | Referências opcionais ao ativo e ao responsável |

`GET /relatorios/exportar.csv` aceita técnicos. Na demonstração, exporta somente os exemplos fictícios, com responsável e patrimônio omitidos. O CSV usa UTF-8 com BOM, separador ponto e vírgula e inclui `sla_cumprido` calculado em Python a partir das pausas. O valor fica em branco para chamados ainda sem resolução. Valores que poderiam ser interpretados como fórmulas são tratados como texto.

O percentual do painel considera chamados concluídos. Para cumprir o SLA, a primeira resposta e a resolução devem respeitar seus prazos. A média do CSAT considera apenas notas enviadas. As aberturas dos últimos 7 dias são agrupadas em BRT.

```bash
python scripts/analise_chamados.py instance/demonstracao.db
```

O script abre o SQLite para leitura e resume os registros fictícios por categoria, status, prioridade e dia UTC.

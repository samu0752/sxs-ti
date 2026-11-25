# Regras de atendimento

- O solicitante abre incidentes, requisições e problemas, vê seus chamados, envia mensagens e arquivos e avalia chamados próprios fechados. A equipe técnica vê a fila, atribui atendimento, registra solução, cria artigos e vincula ativos e chamados.
- Notas internas e vínculos ficam restritos à equipe. Um ativo precisa pertencer ao solicitante do chamado.
- O fim do atendimento exige solução. A primeira mensagem pública do técnico ou o registro da solução marca a primeira resposta. Chamados fechados precisam ser reabertos antes de receber novos arquivos e mensagens.
- Ao entrar em **Aguardando solicitante**, os prazos pendentes pausam. A resposta pública do próprio solicitante ou a saída do status pelo técnico retoma o relógio. O histórico de pausas permite calcular cada etapa sem descontar períodos posteriores à sua conclusão.
- A agenda conta segunda a sexta, 08h–18h BRT. A partir de 85% do prazo pendente, o chamado ganha um alerta e sua prioridade operacional sobe um nível uma vez. O prazo pactuado na abertura permanece como referência. Chamados críticos recebem o alerta visual; o nível crítico é o último.
- O CSAT aceita uma nota inteira de 1 a 5 após fechamento. Notificações de abertura, resposta pública técnica e resolução são eventos JSON que simulam e-mail.

| Prioridade SLA | Primeira resposta (horas úteis) | Resolução (horas úteis) |
|---|---:|---:|
| Crítica | 1 | 4 |
| Alta | 2 | 8 |
| Média | 4 | 24 |
| Baixa | 8 | 48 |

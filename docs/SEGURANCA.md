# Modelo de ameaças STRIDE

O navegador atravessa a fronteira HTTP do Flask e o servidor decide o acesso antes de consultar SQLite. O ambiente operacional e a demonstração usam bancos distintos. Os fluxos mais sensíveis são sessão, acesso a chamados, administração e anexos.

| Categoria | Ameaça | Controle e limite |
|---|---|---|
| Spoofing | Tentativas de assumir outra conta | Hash Werkzeug, renovação da sessão no login, limite por IP e bloqueio de 15 minutos após 5 falhas por conta. |
| Tampering | Envio forçado de formulários ou alteração de registros | CSRF com comparação em tempo constante, SQL parametrizado, autorização no servidor e transações. |
| Repudiation | Negação de ação administrativa ou de atendimento | `audit_logs` guarda logins e operações administrativas. A ficha registra o histórico de atendimento. O banco requer acesso operacional restrito. |
| Information disclosure | Leitura de chamado, nota ou anexo alheio | Escopo pelo dono para solicitante, notas e vínculos internos para técnicos, anexos com download autorizado e demo filtrado. Logs omitem dados pessoais e conteúdo. |
| Denial of service | Rajadas de login e uploads grandes | Limitação por IP em memória, lock persistente por conta e limite de 2 MB por anexo. Um login bloqueado pode exigir espera, inclusive em abuso direcionado. |
| Elevation of privilege | Solicitante executa ação técnica ou administrativa | Verificação de perfil em cada rota. Cadastro público cria solicitantes. A conta administrativa é provisionada em CLI. |

Em HTTPS, use `SESSION_COOKIE_SECURE=1` e uma chave persistente. A aplicação envia HSTS em HTTPS e CSP, proteção contra enquadramento e `nosniff` nas respostas. O proxy só deve ser confiado quando controlado pela implantação. Anexos são baixados como arquivos e o projeto verifica suas assinaturas sem analisar conteúdo interno. O armazenamento local precisa de backup e de controle de acesso.

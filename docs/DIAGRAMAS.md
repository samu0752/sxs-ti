# Diagramas do SXS TI

## Abertura e atendimento

~~~mermaid
flowchart TD
    A["Solicitante abre chamado"] --> B["Protocolo é gerado"]
    B --> C["Técnico assume ou atribui"]
    C --> D["Diagnóstico e mensagens"]
    D --> E{"Precisa de informações?"}
    E -->|Sim| F["Aguardando solicitante"]
    F --> D
    E -->|Não| G["Registra solução"]
    G --> H["Resolvido ou fechado"]
~~~

O fluxo mostra um roteiro típico. A interface permite ao técnico escolher entre os cinco status. A passagem de um status ativo para **Resolvido** ou **Fechado** exige uma solução.

## Perfis e permissões

~~~mermaid
flowchart TD
    Cadastro["Cadastro pela tela de login"] --> S["Solicitante"]
    Admin["Administrador"] --> Criar["Contas de acesso"]
    Criar --> S
    Criar --> T["Técnico"]
    S --> Proprios["Consulta e responde aos próprios chamados"]
    T --> Fila["Gerencia a fila e registra soluções"]
    Admin --> Fila
~~~

O administrador usa o perfil técnico e tem permissão adicional para gerenciar contas. A primeira conta administrativa é criada no terminal.

## Modelo de dados

~~~mermaid
erDiagram
    USERS o|--o{ TICKETS : solicita
    USERS o|--o{ TICKETS : atende
    TICKETS ||--o{ TIMELINE : registra
    USERS {
        integer id PK
        string username
        string email
        string role
        integer can_manage_users
    }
    TICKETS {
        integer id PK
        string protocol
        integer owner_id FK
        integer assignee_id FK
        string priority
        string status
    }
    TIMELINE {
        integer id PK
        integer ticket_id FK
        string actor
        string kind
        integer internal
    }
~~~

A autoria de cada evento é guardada como texto. Chamados antigos e exemplos podem ficar sem conta de solicitante vinculada. Um chamado também pode ficar sem técnico responsável.

## Atribuição com histórico

~~~mermaid
sequenceDiagram
    actor T as Técnico
    participant F as Flask
    participant D as SQLite
    T->>F: Salvar responsável
    F->>F: Validar sessão e formulário
    F->>D: Alterar responsável e registrar evento
    D-->>F: Confirmar transação
    F-->>T: Exibir ficha atualizada
~~~

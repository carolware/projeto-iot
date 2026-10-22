# Modelo de dados

Banco único **PostgreSQL 16 + TimescaleDB**, com partes relacionais (catálogo,
alarmes, auditoria) e séries temporais (hypertables). O esquema é criado pelas
migrations Alembic em `plataforma/central/migrations/versions/`:

| Migration | Conteúdo |
|---|---|
| `0001_mer_inicial` | catálogo, dispositivos, mensagens, séries, gêmeo, comandos, FIRMS, DLQ, auditoria, agregado `telemetria_1h` |
| `0002_regras_alarmes` | regras, ações, alarmes, execuções de ações, assinaturas de alerta |
| `0003_bordas_sites` | sites atendidos por cada nó de borda |
| `0004_tags_dispositivo` | tags e grupos de dispositivos (`text[]` com índice GIN) |

O MER foi derivado dos JSONs que já circulavam na 1ª entrega (leitura do sensor,
resultado FIRMS, alerta do gateway, comando do painel e lista de zonas).

## 1. MER

```mermaid
erDiagram
  INQUILINO ||--o{ SITE : possui
  INQUILINO ||--o{ DOMINIO : define
  INQUILINO ||--o{ REGRA : define
  INQUILINO ||--o{ ASSINATURA_ALERTA : cadastra
  INQUILINO ||--o{ NO_BORDA : opera
  INQUILINO ||--o{ AUDITORIA : registra
  SITE ||--o{ AREA : contem
  SITE ||--o{ DISPOSITIVO : abriga
  SITE ||--o{ CONTATO_RESGATE : tem
  SITE ||--o{ VARREDURA_FIRMS : recebe
  AREA |o--o{ DISPOSITIVO : localiza
  DOMINIO ||--o{ PERFIL_DISPOSITIVO : agrupa
  DOMINIO ||--o{ REGRA : escopo
  PERFIL_DISPOSITIVO ||--o{ PERFIL_GRANDEZA : mede
  PERFIL_DISPOSITIVO ||--o{ DISPOSITIVO : tipa
  DISPOSITIVO ||--o{ MENSAGEM : envia
  MENSAGEM ||--o{ TELEMETRIA : contem
  DISPOSITIVO ||--o{ ULTIMO_ESTADO : tem
  DISPOSITIVO ||--o| GEMEO : tem
  DISPOSITIVO ||--o{ EVENTO : emite
  DISPOSITIVO ||--o{ COMANDO : recebe
  VARREDURA_FIRMS ||--o{ FOCO_FIRMS : detecta
  REGRA ||--o{ REGRA_ACAO : executa
  REGRA ||--o{ ALARME : gera
  DISPOSITIVO ||--o{ ALARME : origina
  ALARME |o--o{ ALARME : "entregue como"
  ASSINATURA_ALERTA ||--o{ ALARME : roteia
  ALARME ||--o{ COMANDO : dispara
  ALARME ||--o{ EXECUCAO_ACAO : registra
  REGRA_ACAO ||--o{ EXECUCAO_ACAO : tentativa

  INQUILINO { uuid id PK
    text slug UK
    text nome
    text cenario "urbano | rural" }
  SITE { uuid id PK
    uuid inquilino_id FK
    text slug
    float lat
    float lon
    float raio_km }
  AREA { uuid id PK
    uuid site_id FK
    text slug
    float lat
    float lon
    float raio_km }
  DOMINIO { uuid id PK
    uuid inquilino_id FK
    text slug "incendio, alerta_publico"
    text descricao }
  PERFIL_DISPOSITIVO { uuid id PK
    uuid dominio_id FK
    text slug
    int intervalo_esperado_s }
  PERFIL_GRANDEZA { uuid perfil_id FK
    text grandeza
    text unidade "UCUM"
    float valor_min
    float valor_max }
  DISPOSITIVO { uuid id PK
    uuid inquilino_id FK
    uuid perfil_id FK
    uuid site_id FK
    uuid area_id FK
    text slug UK
    text tipo "estacao | atuador | terminal | conector"
    text fabricante
    text firmware
    text_array tags "grupo = tag grupo:xxx"
    text credencial_hash
    timestamptz revogado_em
    timestamptz ultimo_contato_em }
  NO_BORDA { uuid id PK
    uuid inquilino_id FK
    text slug
    text versao
    text credencial_hash
    int fila_pendente
    timestamptz ultimo_sync_em
    text_array sites }
  MENSAGEM { uuid id_mensagem PK "deduplicação"
    uuid dispositivo_id FK
    text tipo
    timestamptz medido_em
    timestamptz recebido_em
    text via "nó de borda"
    text fonte }
  TELEMETRIA { timestamptz tempo "hypertable"
    uuid dispositivo_id FK
    text grandeza
    float valor
    text unidade
    bool suspeita
    uuid id_mensagem FK }
  ULTIMO_ESTADO { uuid dispositivo_id PK
    text grandeza PK
    float valor
    timestamptz tempo }
  GEMEO { uuid dispositivo_id PK
    jsonb desejado
    jsonb relatado }
  EVENTO { timestamptz tempo "hypertable"
    uuid dispositivo_id FK
    text tipo
    uuid comando_id FK
    jsonb dados }
  COMANDO { uuid id PK "= id_mensagem do comando"
    uuid dispositivo_id FK
    uuid alarme_id FK
    text acao
    text origem "regra | painel"
    text estado "enviado | confirmado | falhou"
    jsonb resposta }
  VARREDURA_FIRMS { uuid id PK
    uuid site_id FK
    int focos
    float frp_max
    float distancia_km }
  FOCO_FIRMS { uuid id PK
    uuid varredura_id FK
    float latitude
    float longitude
    float frp
    text satellite }
  REGRA { uuid id PK
    uuid inquilino_id FK
    uuid dominio_id FK
    text nome
    jsonb condicao "todas | alguma | ausencia (+ area)"
    int janela_s
    text severidade
    text escopo "central | borda"
    bool ativa }
  REGRA_ACAO { uuid id PK
    uuid regra_id FK
    text tipo
    jsonb config
    int ordem }
  ALARME { uuid id PK
    uuid inquilino_id FK
    uuid regra_id FK
    uuid dispositivo_id FK
    uuid assinatura_id FK
    uuid origem_alarme_id FK
    text severidade
    text estado "ativo | reconhecido | resolvido"
    timestamptz ocorrido_em
    int ocorrencias
    jsonb dados }
  ASSINATURA_ALERTA { uuid id PK
    uuid inquilino_id FK "prefeitura"
    text dominio_slug
    float lat
    float lon
    float raio_km
    text severidade_min }
  EXECUCAO_ACAO { uuid id PK
    uuid alarme_id FK
    uuid regra_acao_id FK
    text tipo
    int tentativas
    text status "pendente | sucesso | falhou"
    timestamptz proxima_tentativa_em }
  CONTATO_RESGATE { uuid id PK
    uuid site_id FK
    text nome
    text telefone
    text email }
  AUDITORIA { bigint id PK
    timestamptz quando
    text autor "sub do Keycloak"
    text entidade
    text acao
    jsonb antes
    jsonb depois }
  DLQ { bigint id PK
    timestamptz recebido_em
    text origem
    text payload
    text motivo }
```

## 2. Decisões

- **Hypertables**: `telemetria` (partição diária) e `evento` (semanal), ambas
  pela coluna `tempo` = horário da medição. Assim, um dado atrasado entra na
  posição certa do histórico.
- **Compressão** da `telemetria` depois de 7 dias, segmentada por dispositivo e
  grandeza.
- **Agregado contínuo `telemetria_1h`**: média, mínimo, máximo e número de
  amostras por hora (RF-PRO-04), com `materialized_only = false`, o que mantém a
  última hora sempre atualizada. Consultado com `agregacao=1h` em
  `GET /api/v1/telemetria` e `GET /api/v1/dispositivos/{slug}/telemetria`.
- **Deduplicação**: `mensagem.id_mensagem` é a chave primária. A inserção usa
  `ON CONFLICT DO NOTHING` e a telemetria só é gravada para os ids novos, na
  mesma transação. `mensagem` é tabela comum, porque numa hypertable o `tempo`
  teria de fazer parte da chave.
- **Último estado** (RF-DAD-02): `ultimo_estado` só é atualizado quando a
  leitura nova é mais recente que a gravada. Um lote atrasado não sobrescreve um
  valor mais novo.
- **Isolamento por inquilino** (RF-SEG-03): toda tabela de negócio chega a
  `inquilino_id`, direto ou via site/dispositivo, e toda consulta da API filtra
  pelo inquilino do token.
- **Alarmes entregues** a outros inquilinos (assinaturas) são linhas novas com
  `origem_alarme_id` apontando para o alarme rural e `dispositivo_id` vazio. A
  prefeitura não enxerga o dispositivo da brigada.
- **Um alarme aberto por (dispositivo, regra)**: índice único parcial
  `WHERE estado <> 'resolvido'`. Repetições incrementam `ocorrencias`.
- **Geometria**: ponto + raio (Haversine em `nucleo/geo.py`). Polígonos
  (PostGIS) ficam para evolução (RF-CEN-04, ver
  [rastreabilidade](rastreabilidade.md)).
- **Usuários ficam no Keycloak**: o banco guarda só o `sub` em
  `auditoria.autor`, `reconhecido_por` e `resolvido_por`.
- **Credenciais**: só o hash, no formato `$7$` do Mosquitto (PBKDF2-SHA512).

## 3. Borda (SQLite)

Cada nó de borda tem o próprio banco SQLite em modo WAL (escrita transacional:
uma queda de energia não corrompe a fila, RNF-07):

| Tabela | Conteúdo |
|---|---|
| `fila` | `id_mensagem` (PK), prioridade (10 alarme, 1 estado, 0 telemetria), `payload` **cifrado com Fernet**, criado/enviado em |
| `invalidas` | mensagens recusadas na validação local, com o motivo |

Sem a chave (`BORDA_CHAVE_*`), o disco retirado do nó não revela os dados
(RF-SEG-05). Mensagens confirmadas pela central ficam uma hora e depois são
apagadas.

# Arquitetura

O Sentinela é uma plataforma IoT para **detecção de incêndio no Cerrado** que
integra dois cenários:

- **rural**: estações de detecção nas fazendas e áreas de preservação, atrás de
  um **nó de borda** que funciona sem internet;
- **urbano**: **terminais de alerta** nas prefeituras, Defesa Civil e bombeiros,
  que recebem os alertas de incêndio próximos da cidade e devolvem o
  reconhecimento do operador.

Não há hardware: todos os dispositivos são simuladores em contêiner (o PDF de
requisitos põe hardware fora do escopo, §1.2). Tudo sobe com um
`docker compose up`.

## 1. Visão em camadas

Adaptação da Figura 3.1 do documento de requisitos.

```mermaid
flowchart TB
  subgraph APP["Aplicação"]
    PAINEL["Painel web (React)<br/>rural e prefeitura"]
    API["API REST /api/v1 (OpenAPI 3)<br/>WebSocket /tempo-real"]
    PROM["Prometheus<br/>métricas e alertas operacionais"]
  end
  subgraph PROC["Processamento"]
    MOTOR["Motor de regras<br/>limite · janela · ausência · área"]
    ACOES["Ações (Strategy)<br/>alarme · comando · webhook · publicar · notificar"]
    ASSIN["Assinaturas entre inquilinos<br/>(alerta rural → prefeitura)"]
  end
  subgraph DADOS["Dados"]
    PG[("PostgreSQL + TimescaleDB<br/>catálogo · séries · alarmes · auditoria")]
  end
  subgraph ING["Conectividade e ingestão"]
    MQ["Mosquitto central<br/>MQTT 3.1.1/5 sobre TLS"]
    INGM["central-ingestao<br/>pipeline em micro-lotes"]
    INGH["Ingestão HTTP e lote"]
  end
  subgraph BORDA["Borda (fog)"]
    NB["Nós de borda<br/>Mosquitto local · fila SQLite cifrada · regras locais"]
  end
  subgraph DISP["Dispositivos (edge, simulados)"]
    EST["Estações de incêndio (Python)"]
    ATU["Atuadores da brigada (Python)"]
    TERM["Terminais de alerta (Go)"]
    FIRMS["Conector NASA FIRMS (Python)"]
  end
  SEG["Segurança transversal: Keycloak (OIDC, papéis, 2FA) · CA própria · ACL por dispositivo"]

  EST --> NB --> INGH
  ATU <--> NB
  FIRMS --> MQ
  TERM <--> MQ
  MQ --> INGM --> PG
  INGH --> PG
  INGM -- "barramento plataforma/..." --> MOTOR
  INGH --> MOTOR
  MOTOR --> ACOES --> MQ
  MOTOR --> ASSIN
  PG --> API --> PAINEL
  MOTOR --> API
  SEG -.- API
  SEG -.- MQ
```

| Camada | Componentes | Requisitos principais |
|---|---|---|
| Dispositivos | `estacoes-*`, `atuadores-*`, `terminais` (Go), `conector-firms` | RF-INT-02, RF-INT-04, RF-DIS-05 |
| Borda | `borda-*` + `mosquitto-borda-*` | RF-EDG-01..06, RF-SEG-05 |
| Ingestão | `mosquitto-central`, `central-ingestao`, `POST /ingestao` e `/ingestao/lote` | RF-ING-01..04, 06, 08 |
| Dados | `postgres` (TimescaleDB) | RF-DAD-01, 02, 06; RF-PRO-04 |
| Processamento | motor de regras, ações, assinaturas (dentro de `central`) | RF-PRO-01..05 |
| Aplicação | API, WebSocket, painel `front`, Prometheus | RF-APP-01..07, RF-OBS-01..05 |
| Segurança | `keycloak`, `pki`, ACL do Mosquitto | RF-SEG-01..05, RNF-11 |

## 2. Edge → Fog → Cloud

Cada nível tem processamento próprio; o que roda onde:

```mermaid
flowchart LR
  subgraph EDGE["EDGE · dispositivos simulados"]
    E1["Estações TO<br/>Mateiros · Lagoa da Confusão · Sandolândia"]
    E2["Estações GO<br/>Anápolis · Formosa · Pirenópolis · Jaraguá"]
    E3["Estações diretas<br/>Novo Progresso (PA) · Mirador (MA)"]
    A1["Atuadores da brigada"]
    T["Terminais das prefeituras (Go)"]
  end
  subgraph FOG["FOG · nós de borda"]
    F1["borda-tocantins"]
    F2["borda-goias"]
  end
  subgraph CLOUD["CLOUD · instância central"]
    C["ingestão → regras → alarmes → assinaturas<br/>TimescaleDB · API · painel"]
  end
  E1 -- "MQTT/TLS local" --> F1
  E2 -- "MQTT/TLS local" --> F2
  F1 -- "aciona sem a central" --> A1
  F1 -- "lotes HTTP (link intermitente)" --> C
  F2 -- "lotes HTTP (link intermitente)" --> C
  C -- "comandos pela bridge MQTT" --> F1
  E3 -- "MQTT/TLS direto" --> C
  C -- "alerta (comando)" --> T
  T -- "recebido / reconhecido" --> C
```

| Nível | Onde roda | O que processa |
|---|---|---|
| **Edge** | estações, atuadores, terminais e conector (contêineres) | Leitura com ruído sobre dados reais (Open-Meteo e NASA FIRMS); `id_mensagem` e horário gerados na origem; **pré-filtro**: só envia quando o valor varia além de um limiar ou vence o intervalo do perfil (economiza banda). |
| **Fog** | `borda-tocantins`, `borda-goias` (referência: Raspberry Pi 5) | Validação contra o contrato; **fila SQLite (WAL) cifrada com Fernet**; **regras locais**: risco crítico aciona a brigada sem a central; prioridade alarme > estado > telemetria na sincronização; regras e ACL baixadas da central e guardadas no disco; métricas próprias. |
| **Cloud** | `central`, `central-ingestao`, `postgres` | Deduplicação e ordenação pelo horário da medição, enriquecimento pelo cadastro, histórico, regras com janela/ausência/área, ciclo de vida dos alarmes, correlação com satélite, **assinaturas que levam o alerta às prefeituras**, API, painel e segurança. |

Decisões (detalhes nos [ADRs](adr/)):

- **Dois nós de borda**, um por região rural, como na Figura 3.2 do PDF.
  Pará e Maranhão não têm borda: suas estações falam direto com a central, como
  um local de boa conexão. Nesses casos quem decide é a central.
- **O cenário urbano não usa borda**: as prefeituras têm link contínuo.
- **Mesmo código de domínio na fog e na cloud** (pacote `nucleo/`), com
  adaptadores diferentes: SQLite na borda e Postgres na central.
- **Subida borda → central por lotes HTTP** (`POST /api/v1/ingestao/lote`), com
  resultado por mensagem: só sai da fila o que a central confirmou. O fluxo
  normal e a sincronização depois de uma queda são o mesmo caminho.
- **Descida central → campo pela bridge do Mosquitto** (`cleansession false`):
  comandos emitidos com o link fora ficam guardados e chegam na volta.

## 3. Implantação

Os serviços do `docker-compose.yml`, por rede. As redes `campo-*` simulam a rede
local de cada fazenda; desconectar a borda da rede `nuvem` simula a queda do link
(`plataforma/simuladores/demo/link.sh`).

```mermaid
flowchart TB
  subgraph NUVEM["rede nuvem (instância central)"]
    pki["pki<br/>(gera a CA e os certificados)"]
    mqc["mosquitto-central :8883"]
    central["central :8000<br/>API · motor · WebSocket"]
    ing["central-ingestao :9103"]
    pg[("postgres :5433")]
    kc["keycloak :8080"]
    prom["prometheus :9090"]
    front["front :3000"]
    firms["conector-firms"]
    term["terminais (Go)"]
    dir["estacoes-diretas<br/>atuadores-diretos"]
  end
  subgraph TO["rede campo-tocantins"]
    mqt["mosquitto-borda-tocantins :8884"]
    bt["borda-tocantins :9101"]
    et["estacoes-tocantins<br/>atuadores-tocantins"]
  end
  subgraph GO["rede campo-goias"]
    mqg["mosquitto-borda-goias :8885"]
    bg["borda-goias :9102"]
    eg["estacoes-goias<br/>atuadores-goias"]
  end
  et --> mqt --> bt
  eg --> mqg --> bg
  bt -- "HTTP Basic" --> central
  bg -- "HTTP Basic" --> central
  mqc -- "bridge TLS" --- mqt
  mqc -- "bridge TLS" --- mqg
  firms --> mqc
  term <--> mqc
  dir <--> mqc
  mqc --> ing --> pg
  central --> pg
  ing -- "plataforma/..." --> mqc
  mqc -- "plataforma/..." --> central
  front --> central
  front --> kc
  central -- JWKS --> kc
  prom -.-> central
  prom -.-> ing
  prom -.-> bt
  prom -.-> bg
```

Portas publicadas na máquina: 3000 (painel), 8000 (API e Swagger em `/docs`),
8080 (Keycloak), 8883/8884/8885 (MQTT/TLS central, borda TO, borda GO),
9090 (Prometheus), 9101/9102 (métricas das bordas), 9103 (métricas da
ingestão) e 5433 (Postgres).

## 4. Arquitetura hexagonal de cada serviço

O domínio (`plataforma/nucleo/`) não conhece protocolo, banco nem cenário. A
central e a borda montam o mesmo caso de uso com adaptadores diferentes
(Figura 7.1 do PDF).

```mermaid
flowchart LR
  subgraph ENTRADA["Adaptadores de entrada"]
    am["MQTT<br/>central/mqtt.py · borda/app.py"]
    ah["HTTP / lote<br/>central/api/ingestao.py"]
  end
  subgraph NUCLEO["nucleo/ (domínio)"]
    pi["PipelineIngestao"]
    rg["Regra · vencedoras · sustentada"]
    mc["Mensagem canônica · validação · unidades · geo"]
    po{{"Portas:<br/>Catalogo · Repositorio<br/>FilaInvalidas · Barramento"}}
  end
  subgraph SAIDA["Adaptadores de saída"]
    sp["Postgres<br/>central/repositorios.py"]
    ss["SQLite cifrado<br/>borda/fila.py"]
    sb["Barramento MQTT · motor · tempo real<br/>(BarramentoComposto)"]
  end
  am --> pi
  ah --> pi
  pi --> mc
  pi --> po
  po --> sp
  po --> ss
  po --> sb
```

Padrões usados:

| Padrão | Onde |
|---|---|
| Pipes and Filters | `nucleo/ingestao.py`: validar → autenticar → enriquecer → deduplicar → ordenar → gravar → publicar |
| Idempotent Receiver | `mensagem.id_mensagem` é chave primária; `INSERT … ON CONFLICT DO NOTHING` na mesma transação da telemetria |
| Dead Letter Channel | tabela `dlq` (central) e `invalidas` (borda), sempre com o motivo |
| Observer | `BarramentoComposto`: cada mensagem aceita vai ao MQTT, ao tempo real e ao motor |
| Strategy | `central/acoes.py`: um tipo de ação = uma classe |
| Retry com backoff | `ExecutorAcoes`: 2 s, 4 s, 8 s… até 5 min, no máximo 6 tentativas |
| Repository | `central/repositorios.py` |
| Store-and-forward | `borda/fila.py` + `borda/sincronizador.py` |
| Type Object | domínios e perfis são dados (`dominio`, `perfil_dispositivo`), não classes |

## 5. Pipeline de ingestão

Figura 5.1 do PDF, como está implementada (`nucleo/ingestao.py`). O mesmo
pipeline atende MQTT (micro-lotes), HTTP (uma mensagem) e lotes da borda.

```mermaid
flowchart LR
  r["Receber<br/>MQTT · HTTP · lote"] --> v["Validar<br/>JSON Schema v1 + coerência com o tópico"]
  v --> a["Autenticar<br/>cadastrado · não revogado ·<br/>credencial pode falar por ele"]
  a --> e["Enriquecer<br/>unidade → UCUM do perfil ·<br/>faixa → suspeita · inquilino/site/área"]
  e --> d["Deduplicar<br/>id_mensagem no lote e no banco"]
  d --> o["Ordenar<br/>pelo horário da medição"]
  o --> g[("Gravar<br/>mensagem · telemetria · último estado")]
  g --> p["Publicar<br/>barramento · tempo real · motor"]
  v -. inválida .-> dlq[("DLQ com motivo")]
  a -. recusada .-> dlq
  e -. recusada .-> dlq
```

- **Dado atrasado** entra na posição certa da série (hypertable ordenada por
  `tempo`), não sobrescreve o último estado se for mais velho e não abre alarme
  "atual" se tiver mais de 5 minutos (RF-ING-04).
- **Micro-lotes no MQTT**: o callback só enfileira; uma thread grava de uma vez
  tudo o que se acumulou (lote de 1 com pouca carga, sem espera).
- **Ingestão separada**: o serviço `central-ingestao` faz o pipeline MQTT e
  publica as aceitas em `plataforma/{tipo}/…`; a API ouve esse barramento e
  alimenta o motor e o tempo real ([ADR 0009](adr/0009-ingestao-mqtt-separada.md)).

## 6. Mensagem canônica

Todas as mensagens circulam num formato único e versionado
(`plataforma/contratos/mensagem-canonica.v1.schema.json`, AsyncAPI em
`plataforma/contratos/asyncapi.yaml`):

```json
{
  "versao": "1.0",
  "id_mensagem": "8a1f0c2e-6b4d-4f1a-9c3e-2d7b5e9a0f11",
  "dispositivo": "est-mateiros-01",
  "tipo": "telemetria",
  "timestamp": "2026-10-19T14:05:00.000Z",
  "local": { "geo": { "type": "Point", "coordinates": [-46.4168, -10.5464] } },
  "medidas": [
    { "grandeza": "temperatura", "valor": 34.2, "unidade": "Cel" },
    { "grandeza": "umidade", "valor": 18.0, "unidade": "%" },
    { "grandeza": "fumaca", "valor": 62.5, "unidade": "1" }
  ]
}
```

- `tipo`: `telemetria`, `evento`, `estado` ou `comando`.
- `timestamp`: horário **da medição**, em UTC; é ele que ordena a série.
- `unidade`: código UCUM; a ingestão converte para a unidade do perfil.
- `via`: preenchido pelo nó de borda que encaminhou.
- `dados`: conteúdo livre de eventos, estado e comandos.

Tópico MQTT: `{inquilino}/{site}/{dominio}/{dispositivo}/{tipo}`; árvore
completa e ACL em [mqtt-e-seguranca.md](mqtt-e-seguranca.md).

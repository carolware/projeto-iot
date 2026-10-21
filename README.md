# SENTINELA — Sistema IoT de Monitoramento e Alerta de Incêndios Florestais

> Projeto de Disciplina — Internet das Coisas (IoT)

---

## Resumo

Este projeto apresenta a concepção, implementação e integração de uma arquitetura IoT distribuída para o monitoramento de incêndios florestais em tempo quase real. O sistema combina dados meteorológicos atuais da Open-Meteo, detecções orbitais de focos de calor da NASA FIRMS (*Fire Information for Resource Management System*), comunicação assíncrona via protocolo MQTT e raciocínio baseado em regras no gateway. A solução é complementada pelo dashboard web reativo **SENTINELA**, desenvolvido com React/TanStack Start. A arquitetura implementa o paradigma *edge-to-cloud* com cache das fontes externas e processamento contínuo da telemetria.

---

## 1. Introdução e Motivação

O Brasil possui aproximadamente 12% da superfície terrestre coberta por biomas altamente suscetíveis a incêndios — Cerrado, Amazônia, Caatinga e Pantanal — sendo que o período de estiagem prolongado eleva dramaticamente o índice de ocorrências. Segundo dados do INPE (Instituto Nacional de Pesquisas Espaciais), o país registra dezenas de milhares de focos de calor por ano, muitos dos quais poderiam ser mitigados com detecção antecipada.

No contexto da disciplina de Internet das Coisas, o presente trabalho propõe uma abordagem baseada em quatro princípios:

1. **Heterogeneidade de fontes**: fusão de estimativas meteorológicas atuais (temperatura e umidade relativa) com detecções orbitais de focos de calor e um índice qualitativo derivado de fumaça.
2. **Processamento na borda** (*edge computing*): a lógica de inferência de risco é executada no gateway, sem dependência de nuvem para decisões de latência crítica.
3. **Comunicação assíncrona orientada a eventos**: o protocolo MQTT (Message Queuing Telemetry Transport) garante entrega confiável com overhead mínimo de protocolo.
4. **Atuação automatizada**: comandos de resposta são emitidos pelo gateway e recebidos pelo atuador, fechando o laço de controle sem intervenção humana obrigatória.

---

## 2. Arquitetura do Sistema

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ARQUITETURA SENTINELA                        │
├─────────────────┬───────────────────────────────────────────────────┤
│  CAMADA DADOS   │  Open-Meteo + NASA FIRMS por zona geográfica       │
│                 │  Meteorologia real + índice estimado de fumaça     │
├─────────────────┼───────────────────────────────────────────────────┤
│  CAMADA REDE    │  Broker MQTT próprio (Eclipse Mosquitto)           │
│                 │  Tópicos hierárquicos por zona e tipo              │
├─────────────────┼───────────────────────────────────────────────────┤
│  CAMADA BORDA   │  Gateway: subscrição, avaliação de risco,          │
│  (Edge)         │  consulta NASA FIRMS, publicação de comandos       │
├─────────────────┼───────────────────────────────────────────────────┤
│  CAMADA         │  API REST NASA FIRMS (VIIRS/MODIS NRT)             │
│  EXTERNA        │  Validação geoespacial de focos de calor           │
├─────────────────┼───────────────────────────────────────────────────┤
│  CAMADA         │  Atuador: recebe comandos, executa ações           │
│  ATUAÇÃO        │  (brigada, monitoramento, silenciar)               │
├─────────────────┼───────────────────────────────────────────────────┤
│  CAMADA         │  Dashboard SENTINELA (React/TanStack)              │
│  VISUALIZAÇÃO   │  React/TanStack local ou deploy web compatível     │
└─────────────────┴───────────────────────────────────────────────────┘
```

### 2.1 Tecnologias Utilizadas e Responsabilidades

| Tecnologia | Camada | Utilização no projeto |
|------------|--------|-----------------------|
| **Python 3** | Backend/edge | Implementa o coletor de telemetria, o gateway e o atuador. |
| **requests** | Integração HTTP | Consulta os endpoints REST da Open-Meteo e da NASA FIRMS. |
| **python-dotenv** | Configuração | Carrega broker, namespace MQTT, chave FIRMS, raios e intervalos a partir do `.env`. |
| **paho-mqtt** | Mensageria backend | Publica e consome mensagens MQTT sobre TCP na porta 1883. |
| **Open-Meteo** | Fonte externa | Fornece temperatura e umidade atuais estimadas para as coordenadas monitoradas, sem exigir chave. |
| **NASA FIRMS / VIIRS** | Fonte orbital | Fornece focos de calor, coordenadas e FRP em tempo quase real; não mede diretamente fumaça ou temperatura do ar. |
| **Eclipse Mosquitto** | Infraestrutura MQTT | Broker próprio (`mosquitto-central` no compose) que intermedeia o publish/subscribe; substitui o HiveMQ público usado na 1ª entrega. |
| **React 19 + TypeScript** | Interface | Implementa os componentes reativos e a tipagem do dashboard. |
| **TanStack Start/Router** | Aplicação web | Estrutura rotas, renderização no servidor (SSR), hidratação e build da aplicação. |
| **Vite 8** | Ferramentas frontend | Executa o servidor de desenvolvimento e gera os bundles de produção. |
| **Tailwind CSS 4** | Apresentação | Define layout responsivo, cores, tipografia e estados visuais. |
| **mqtt.js** | Mensageria no navegador | Conecta o dashboard ao Mosquitto por MQTT sobre WebSocket (`ws://localhost:9001`). |
| **Leaflet + React-Leaflet 5** | Geovisualização | Renderiza o mapa interativo, marcadores, popups, zoom e modo expandido. |
| **OpenStreetMap + CARTO** | Cartografia | Fornecem os tiles e a base geográfica exibida pelo Leaflet. |
| **Radix UI** | Componentes auxiliares | Disponibiliza primitivas acessíveis instaladas no frontend; o dashboard atual utiliza principalmente componentes próprios. |
| **Lovable config** | Compatibilidade de projeto | Mantém a configuração de origem do frontend; a aplicação também funciona localmente sem depender da plataforma. |

### 2.2 Topologia de Tópicos MQTT

O namespace MQTT adota hierarquia em quatro níveis para possibilitar subscrições seletivas com wildcards:

| Direção      | Padrão de Tópico                                                     | Descrição                          |
|--------------|----------------------------------------------------------------------|------------------------------------|
| Sensor → GW  | `sentinela-iot-2026-joao-carol/monitoramento-br/{zona}/sensor/temperatura`             | Leitura de temperatura (°C)        |
| Sensor → GW  | `sentinela-iot-2026-joao-carol/monitoramento-br/{zona}/sensor/umidade`                 | Umidade relativa (%)               |
| Sensor → GW  | `sentinela-iot-2026-joao-carol/monitoramento-br/{zona}/sensor/fumaca`                  | Índice estimado de fumaça (0–100)  |
| GW → Atuador | `sentinela-iot-2026-joao-carol/monitoramento-br/{zona}/atuador/alerta`                 | Comando de alerta e ação           |
| GW → Dashboard | `sentinela-iot-2026-joao-carol/monitoramento-br/{zona}/satelite/firms`               | Focos FIRMS, coordenadas e metadados |

O gateway subscreve o padrão `sentinela-iot-2026-joao-carol/monitoramento-br/+/sensor/+` (wildcard `+` = um nível), recebendo automaticamente todos os sensores de todas as zonas cadastradas.

### 2.3 Diagrama de Sequência — Evento de Risco Crítico

```
Sensor          Broker MQTT        Gateway         NASA FIRMS       Atuador
  │                 │                 │                 │                │
  │── pub(temp) ───>│                 │                 │                │
  │── pub(umid) ───>│── msg(umid) ───>│                 │                │
  │── pub(fum)  ───>│── msg(fum)  ───>│                 │                │
  │                 │                 │── avalia_risco  │                │
  │                 │                 │   → CRITICO     │                │
  │                 │                 │── GET /api/area ─────────────>   │
  │                 │                 │<── CSV(focos) ──────────────      │
  │                 │                 │   firms=TRUE    │                │
  │                 │<─ pub(alerta) ──│                 │                │
  │                 │── msg(alerta) ──────────────────────────────────>  │
  │                 │                 │                 │  acionar_brigada│
```

---

## 3. Componentes do Sistema

### 3.1 Coletor de Telemetria (`sensor.py`)

O coletor representa a camada de aquisição IoT de nove zonas geográficas. Temperatura e umidade são obtidas da Open-Meteo para as coordenadas de cada zona. Os focos de calor e o FRP são consultados na NASA FIRMS e convertidos em um **índice qualitativo estimado de fumaça** para manter compatibilidade com o motor acadêmico de regras. Pequeno ruído gaussiano representa a incerteza que existiria em sensores físicos.

> **Limitação científica:** a NASA FIRMS não mede “percentual de fumaça”. Ela detecta anomalias térmicas e fogo ativo. Portanto, o campo `fumaca` é um índice derivado/proxy, e não uma concentração atmosférica medida. Uma medição real de fumaça local exigiria hardware como MQ-2 ou um serviço de qualidade do ar com PM2.5.

**Parâmetros publicados a cada 3 segundos por zona:**

| Campo        | Unidade | Origem | Atualização da fonte |
|--------------|---------|--------|----------------------|
| temperatura  | °C      | Open-Meteo | 10 minutos |
| umidade      | %       | Open-Meteo | 10 minutos |
| fumaca       | índice 0–100 | proxy derivado da NASA FIRMS | 30 minutos |

**Formato de payload (JSON):**
```json
{
  "valor": 72.3,
  "timestamp": "2026-09-09T18:45:00.123456+00:00",
  "fonte": "open-meteo"
}
```

### 3.2 Gateway (`gateway.py`)

Componente central da arquitetura. Responsável por:

#### 3.2.1 Agregação de Estado por Zona
Mantém um objeto `EstadoZona` thread-safe por zona, acumulando as três leituras antes de executar qualquer avaliação. Isso evita avaliações de risco com dados parciais (e.g., receber apenas fumaça sem umidade).

#### 3.2.2 Motor de Regras — Avaliação de Risco

A função `avaliar_risco(fumaca, umidade, temperatura)` implementa um conjunto de regras baseado em limiares físicos empiricamente estabelecidos:

```
SE  indice_fumaca ≥ 85  E  umidade < 15%            → CRÍTICO
SE  indice_fumaca ≥ 60  E  umidade < 25%            → ALTO
SE  indice_fumaca ≥ 40  OU umidade < 35%  OU temp > 33°C → MÉDIO
SENÃO                                          → BAIXO
```

Neste protótipo acadêmico, a combinação entre índice elevado e baixa umidade aumenta o nível de risco. Trata-se de uma heurística determinística para demonstração do processamento de borda, não de um modelo físico validado nem de diagnóstico conclusivo de incêndio.

#### 3.2.3 Integração NASA FIRMS

O gateway consulta periodicamente a API FIRMS para **todas as zonas**, independentemente do risco meteorológico, usando um bounding box de ±0.15° (≈16,7 km) ao redor do centro monitorado. Isso permite que o mapa revele um foco orbital mesmo quando temperatura e umidade locais ainda estão normais. A fonte padrão é **VIIRS_SNPP_NRT** (Visible Infrared Imaging Radiometer Suite — Suomi NPP, Near Real-Time), com resolução nominal de 375 m e latência variável em função da passagem orbital e do processamento.

**Endpoint consultado:**
```
GET https://firms.modaps.eosdis.nasa.gov/api/area/csv
    /{MAP_KEY}/VIIRS_SNPP_NRT
    /{west},{south},{east},{north}/{days}
```

O CSV retornado contém campos `latitude`, `longitude`, `brightness`, `frp` (Fire Radiative Power, em MW), `confidence`, `satellite`, `instrument`, `acq_date`, `acq_time` e `daynight`. O gateway preserva cada detecção individual e calcula:
- **coordenadas exatas reportadas pelo produto orbital**;
- **data e horário de aquisição**;
- **satélite, instrumento e confiança**;
- **FRP individual e FRP máximo da área**;
- **distância à cidade**, calculada pela fórmula de Haversine;
- **número total de focos** dentro da caixa pesquisada.

O resultado completo é publicado com `retain=true` no tópico `satelite/firms`, permitindo que dashboards recém-conectados recebam a última varredura. O mapa desenha o raio de pesquisa, as cidades e os focos reais, com filtros de 6, 12 e 24 horas. Um cooldown padrão de **600 segundos** limita requisições repetidas.

#### 3.2.4 Publicação de Alertas

Quando o risco é **alto ou crítico**, o gateway publica no tópico `atuador/alerta` da zona um payload JSON completo com todos os dados do evento. Na implementação atual, o resultado FIRMS enriquece o alerta por meio de `firms_confirmado`, mas a ausência de foco orbital não bloqueia sua publicação; isso preserva a resposta a indícios locais e considera a latência e as limitações de cobertura do satélite:

```json
{
  "zona": "mateiros",
  "risco": "critico",
  "temperatura": 47.2,
  "umidade": 11.0,
  "fumaca": 91.5,
  "timestamp": "2026-09-09T18:45:00+00:00",
  "firms_confirmado": true,
  "firms_focos": 3,
  "firms_frp_max": 2140.0,
  "firms_dist_km": 1.2,
  "acao": "acionar_brigada"
}
```

### 3.3 Atuador (`atuador.py`)

Recebe os pacotes de alerta do gateway e despacha ações de resposta. A arquitetura do atuador é extensível: cada ação é mapeada a uma função Python separada, facilitando a integração com sistemas externos (API de despacho de equipes, IoT de sirenes, SMS, etc.).

| Ação                    | Risco disparador | Descrição                                    |
|-------------------------|-----------------|----------------------------------------------|
| `acionar_brigada`       | crítico         | Mobilização imediata de brigada de combate   |
| `reforcar_monitoramento`| alto            | Aumento de frequência + notificação de equipe|
| `silenciar_alerta`      | — (manual)      | Desativação de alarmes ativos                |

Um mecanismo de **cooldown por zona × ação** (padrão: 30 s) previne o disparo repetido de ações idênticas em janelas curtas de tempo.

### 3.4 Dashboard SENTINELA (Frontend)

Interface web desenvolvida com **React 19**, **TypeScript**, **TanStack Router/Start**, **Vite 8** e **Tailwind CSS v4**. A geovisualização utiliza **Leaflet/React-Leaflet 5**, e a integração em tempo real usa **mqtt.js** sobre WebSocket seguro. O projeto preserva configuração compatível com Lovable, mas pode ser executado e implantado independentemente da plataforma.

O dashboard opera por padrão em modo real: o módulo `iot.ts` estabelece uma conexão MQTT sobre WebSocket seguro (WSS), subscreve os tópicos de telemetria, alertas e resultados orbitais FIRMS e atualiza a interface imediatamente. O módulo `sim.ts` permanece disponível como modo de demonstração independente; para utilizá-lo, defina `VITE_DATA_MODE=sim` em `front/.env`.

**Funcionalidades do dashboard:**
- Visão geral de todas as zonas monitoradas com níveis de risco em tempo real
- Mapa Leaflet interativo e expansível, com círculos das áreas pesquisadas e cidades coloridas por risco
- Focos FIRMS nas coordenadas orbitais reais, com tamanho/cor por FRP e popup de horário, satélite, instrumento, confiança e distância
- Filtro temporal no mapa para as últimas 6, 12 ou 24 horas
- Feed de eventos ao vivo (crítico, regra, satélite, telemetria, sistema, atuador)
- Log de eventos estilo terminal
- Botões de comando MQTT (acionar brigada, reforçar, silenciar) com feedback visual
- Gráfico de histórico de fumaça por zona (sparkline)

---

## 4. Modelo de Dados e Protocolos

### 4.1 Protocolo MQTT

MQTT (*Message Queuing Telemetry Transport*) é um protocolo de mensageria publish/subscribe projetado para redes com largura de banda limitada e dispositivos com restrições de energia — características típicas de ambientes IoT. Opera sobre TCP/IP com overhead de pacote fixo de apenas 2 bytes no cabeçalho mínimo.

**Configurações utilizadas:**

| Parâmetro     | Valor             | Justificativa                           |
|---------------|-------------------|-----------------------------------------|
| Broker        | Eclipse Mosquitto (central + um por borda) | Brokers próprios, ver `plataforma/infra/mosquitto` |
| Porta         | 1883              | TCP sem TLS (ambiente de desenvolvimento)|
| QoS (sensor)  | 0 (at most once)  | Dados de telemetria — perda ocasional aceitável |
| QoS (alerta)  | 1 (at least once) | Broker confirma o recebimento; pode haver duplicação |
| Keep-alive    | 60 s              | Detecção de desconexão dentro de 1 minuto |

### 4.2 API NASA FIRMS

A NASA disponibiliza o sistema FIRMS gratuitamente para usos acadêmicos e de pesquisa. A API REST suporta consultas por área geográfica (bounding box) e retorna dados NRT (*Near Real-Time*) das missões:

- **VIIRS SNPP NRT**: resolução 375 m, cobertura global a cada ≈12h, latência ≈3h
- **VIIRS NOAA-20 NRT**: resolução 375 m, complementar ao SNPP
- **MODIS NRT**: resolução 1 km, maior cobertura histórica

O FRP (Fire Radiative Power, em MW) é um indicador físico da potência radiativa emitida pelo fogo, correlacionada com a taxa de consumo de biomassa. Valores típicos para incêndios florestais variam de dezenas a milhares de MW.

---

## 5. Configuração e Execução

### 5.1 Pré-requisitos

- Python 3.11+
- Node.js 20+ (para o frontend)
- Chave de acesso NASA FIRMS: [https://firms.modaps.eosdis.nasa.gov/api/](https://firms.modaps.eosdis.nasa.gov/api/)

### 5.2 Instalação do Backend

```bash
# Clonar o repositório
git clone <url-do-repositorio>
cd projeto-iot

# Criar e ativar ambiente virtual
python -m venv venv
source venv/bin/activate        # Linux/macOS
# venv\Scripts\activate         # Windows

# Instalar dependências (+ o pacote nucleo/ compartilhado em modo editável)
pip install -r plataforma/requirements.txt -e plataforma

# Configurar variáveis de ambiente
cp .env.example .env
# Editar .env e preencher FIRMS_MAP_KEY com sua chave da NASA
```

### 5.3 Execução (Docker Compose)

Com `.env` preenchido (passo 5.2; `cp .env.example .env` já traz chaves de
desenvolvimento para as bordas), suba tudo de uma vez:

```bash
docker compose up -d --build
docker compose logs -f borda-tocantins atuadores-tocantins   # acompanhar uma região
docker compose down                                          # parar tudo
```

A topologia reproduz as três camadas (edge → fog → cloud):

| Camada | Serviços | O que fazem |
|---|---|---|
| **Edge** (campo) | `estacoes-*`, `atuadores-*` | Estações simuladas (Open-Meteo + FIRMS + ruído) publicam a mensagem canônica com pré-filtro de envio; atuadores executam comandos e respondem |
| **Fog** (borda) | `mosquitto-borda-{tocantins,goias}`, `borda-{tocantins,goias}` | Broker local, validação, **fila SQLite cifrada**, **regras locais** (aciona a brigada sem a central) e sincronização em lotes; a bridge do Mosquitto desce comandos da central |
| **Cloud** (central) | `mosquitto-central`, `central`, `postgres`, `conector-firms`, `terminais` | Ingestão, cadastro, TimescaleDB, API; conector NASA FIRMS (nível 3); terminais de alerta das prefeituras (Go) |

Novo Progresso (PA) e Mirador (MA) não têm nó de borda: as estações delas
(`estacoes-diretas`) falam direto com a central, como um local com boa conexão.

O front fica em `http://localhost:3000` e fala com o broker em
`ws://localhost:9001`. Enquanto a etapa A5 não migra o painel, o serviço
`ponte-front` traduz o barramento novo para os tópicos antigos que ele usa.
As variáveis `VITE_*` são compiladas no build da imagem — se mudar alguma,
recrie a imagem do front (`docker compose up -d --build front`).

Para rodar um componente fora do Docker (ex.: depurar a borda), instale o
pacote (`pip install -r plataforma/requirements.txt -e plataforma`) e rode a
partir de `plataforma/`, ex.: `python -m borda.app`.

### 5.3.1 Roteiro de demonstração

```bash
# 1. Domínio urbano criado só pela API (RNF-08): alerta_publico, terminais das
#    prefeituras e assinaturas "incêndios a até 80 km da cidade"
python plataforma/simuladores/demo/cadastrar_alerta_publico.py

# 2. Incêndio em Pirenópolis: a borda de Goiás decide localmente e aciona a
#    brigada; a central abre o alarme, notifica (SMS simulado) e entrega o
#    alerta às prefeituras dentro do raio — os terminais (Go) exibem e reconhecem
python plataforma/simuladores/mock_incendio.py on pirenopolis
docker compose logs -f borda-goias central terminais
curl -s 'localhost:8000/api/v1/alarmes?abertos=true' -H 'X-Inquilino: pref-anapolis'

# 3. Fim do incêndio: o alarme resolve sozinho, as entregas também, e os
#    terminais recebem "alerta encerrado"
python plataforma/simuladores/mock_incendio.py off

# 4. Queda do link da fazenda: a borda continua agindo e guarda tudo na fila cifrada
sh plataforma/simuladores/demo/link.sh off tocantins
python plataforma/simuladores/mock_incendio.py on lagoa-da-confusao
curl -s localhost:9101/metrics | grep borda_fila_pendente      # fila crescendo

# 5. Link volta: a fila é sincronizada em lotes, sem perda nem duplicata, e o
#    alarme aparece na central com o horário em que ocorreu
sh plataforma/simuladores/demo/link.sh on tocantins
curl -s 'localhost:8000/api/v1/alarmes?dispositivo=est-lagoa-da-confusao-01' -H 'X-Inquilino: brigada-cerrado'
python plataforma/simuladores/mock_incendio.py off

# 6. Zona sem borda (Mirador): quem decide é a central, que comanda o atuador
python plataforma/simuladores/mock_incendio.py on mirador
```

Regras são configuração (`/api/v1/regras` no Swagger): uma regra de escopo
`borda` alterada na central chega aos nós de borda em até 30 s. Alarmes têm
ciclo ativo → reconhecido → resolvido (`/api/v1/alarmes/{id}/reconhecer` e
`/resolver`); o detalhe do alarme mostra as ações executadas, os comandos com a
resposta de cada dispositivo e as entregas às prefeituras.

Métricas Prometheus dos nós de borda: `localhost:9101/metrics` (Tocantins) e
`localhost:9102/metrics` (Goiás).

### 5.3.2 Instância central (API)

O serviço `central` aplica as migrations, carrega o seed (idempotente) e sobe a
API em `http://localhost:8000`:

- Swagger (OpenAPI 3): `http://localhost:8000/docs`
- Contratos: `http://localhost:8000/contratos/asyncapi.yaml` e
  `http://localhost:8000/contratos/mensagem-canonica.v1.schema.json`
- Ingestão: `POST /api/v1/ingestao` e `POST /api/v1/ingestao/lote`; também
  assina MQTT nos tópicos `{inquilino}/{site}/{dominio}/{dispositivo}/{telemetria|evento|estado}`
- Rotas por inquilino exigem o cabeçalho `X-Inquilino` (ex.: `brigada-cerrado`,
  `pref-anapolis`) — provisório até a autenticação OIDC (etapa A4)

```bash
curl -X POST localhost:8000/api/v1/ingestao -H 'content-type: application/json' \
     --data @plataforma/contratos/exemplos/telemetria-estacao.json
curl localhost:8000/api/v1/dispositivos/est-mateiros-01/estado -H 'X-Inquilino: brigada-cerrado'
```

Testes (unidade + integração; os de integração usam o TimescaleDB do compose
num banco descartável `sentinela_teste` e são pulados se ele estiver fora do ar):

```bash
cd plataforma && pip install -r requirements-dev.txt -e . && pytest --cov
```

### 5.4 Frontend (Desenvolvimento Local)

```bash
cd front
npm install          # ou: bun install
npm run dev
# Acesse o endereço indicado pelo Vite (neste projeto, normalmente http://localhost:8080)
```

O frontend possui configuração própria em `front/.env`. O navegador não utiliza a porta MQTT TCP 1883; ele se conecta ao endpoint WSS definido por `VITE_MQTT_URL`. O valor de `VITE_MQTT_TOPIC_BASE` deve ser idêntico ao `MQTT_TOPIC_BASE` do `.env` localizado na raiz.

### 5.5 Variáveis de Ambiente

| Variável             | Padrão              | Descrição                                |
|----------------------|---------------------|------------------------------------------|
| `MQTT_BROKER`        | localhost           | Endereço do broker MQTT (o compose sobrescreve por serviço) |
| `MQTT_PORT`          | 1883                | Porta TCP do broker                      |
| `MQTT_TOPIC_BASE`    | sentinela-iot-2026-joao-carol/monitoramento-br | Namespace isolado do projeto |
| `FIRMS_MAP_KEY`      | —                   | **Obrigatório**: chave de acesso NASA FIRMS |
| `FIRMS_SOURCE`       | VIIRS_SNPP_NRT      | Fonte de dados orbital                   |
| `FIRMS_DAYS`         | 1                   | Janela temporal de busca (dias)          |
| `FIRMS_RAIO_GRAUS`   | 0.15                | Raio de busca em graus (~16 km)          |
| `SENSOR_REFRESH_METEO_MIN` | 10             | Atualização Open-Meteo no coletor        |
| `SENSOR_REFRESH_FIRMS_MIN` | 30             | Atualização FIRMS no coletor              |
| `FIRMS_COOLDOWN_SEG` | 600                 | Cooldown entre varreduras FIRMS por zona |
| `ALERTA_COOLDOWN_SEG`| 30                  | Intervalo mínimo entre alertas da mesma zona |
| `ACAO_COOLDOWN_SEG`  | 30                  | Cooldown entre ações repetidas (atuador) |
| `MOCK_ZONA_INCENDIO` | — (vazio)           | Zona que ignora Open-Meteo/FIRMS e publica valores fixos de incêndio (ver 5.6) |
| `MOCK_TEMPERATURA`   | 42.0                | Temperatura (°C) usada pela zona mockada |
| `MOCK_UMIDADE`       | 10.0                | Umidade (%) usada pela zona mockada      |
| `MOCK_FUMACA`        | 93.0                | Índice de fumaça (%) usado pela zona mockada |
| `MOCK_LAT` / `MOCK_LON` | — (vazio)        | Coordenada opcional que reposiciona o marcador da zona mockada (ver 5.6) |

### 5.6 Simulação de Incêndio (Mock)

As estações simuladas permitem forçar uma zona a publicar valores fixos de
incêndio crítico (fumaça alta, umidade baixa, temperatura alta), ignorando
Open-Meteo/FIRMS só para ela — as demais continuam com dados reais.

Zonas disponíveis (ver `plataforma/simuladores/zonas.py`): `anapolis`,
`formosa`, `pirenopolis`, `jaragua`, `sandolandia`, `mateiros`,
`lagoa-da-confusao` (atrás de nós de borda) e `novo-progresso`, `mirador`
(conexão direta).

**Em tempo real, via `mock_incendio.py`** (não precisa reiniciar nada):

```bash
python plataforma/simuladores/mock_incendio.py on mateiros
python plataforma/simuladores/mock_incendio.py on mateiros --temperatura 45 --umidade 5 --fumaca 98
python plataforma/simuladores/mock_incendio.py on mateiros --lat -10.55 --lon -46.30   # foco FIRMS no ponto
python plataforma/simuladores/mock_incendio.py off
```

O script publica no tópico de controle `simulacao/mock` do broker por onde a
zona fala com a plataforma (borda da região, portas 1884/1885 na máquina, ou a
central, 1883) e também no da central, onde o conector FIRMS escuta. Com
`lat`/`lon`, a coordenada vai no campo `local.geo` das mensagens da estação e o
conector **fabrica uma detecção de satélite** exatamente ali, publicada na hora.
A coordenada precisa cair dentro do raio de busca FIRMS da zona
(`FIRMS_RAIO_GRAUS`); fora disso o script recusa antes de publicar.

**No boot, via `.env`** (`MOCK_ZONA_INCENDIO=mateiros` etc.): vale até recriar o
contêiner das estações da região (`docker compose up -d --force-recreate estacoes-tocantins`).

Nos logs das estações, a zona mockada aparece com `[MOCK INCÊNDIO]`; as
mensagens trazem `"fonte": "mock"`.

---

## 6. Mapeamento de Zonas

As zonas monitoradas cobrem localidades de Goiás, Tocantins, Pará e Maranhão:

| Zona              | UF | Lat       | Lon       | Sensor ID | Critério |
|-------------------|----|-----------|-----------|-----------|----------|
| Anápolis          | GO | -16.3267  | -48.9530  | GO-AN-01  | Zona solicitada |
| Formosa           | GO | -15.5372  | -47.3372  | GO-FO-02  | Zona solicitada |
| Pirenópolis       | GO | -15.8558  | -48.9597  | GO-PI-03  | Zona solicitada |
| Jaraguá           | GO | -15.7529  | -49.3344  | GO-JA-04  | Zona solicitada |
| Sandolândia       | TO | -12.5408  | -49.9192  | TO-SA-05  | Zona solicitada |
| Novo Progresso    | PA | -7.1261   | -55.3853  | PA-NP-06  | Representante da Amazônia paraense |
| Mirador           | MA | -6.3745   | -44.3683  | MA-MI-07  | Destaque nacional de área queimada em 2025 |
| Mateiros          | TO | -10.5464  | -46.4168  | TO-MA-08  | Destaque nacional de área queimada em 2025 |
| Lagoa da Confusão | TO | -10.7906  | -49.6199  | TO-LC-09  | Destaque nacional de área queimada em 2025 |

A seleção dos três destaques adicionais usa o recorte de **área queimada municipal em 2025** divulgado pelo MapBiomas Fogo, e não uma afirmação permanente sobre a quantidade de focos. Rankings variam conforme período e indicador. Como “Amazônia” é uma região extensa, Novo Progresso (PA) permanece como ponto representativo. Para alterar coordenadas, edite `plataforma/simuladores/zonas.py` e o seed da central (`plataforma/central/seeds/seed.py`); o front ainda tem cópias em `iot.ts` e `sim.ts` até a etapa A5.

---

## 7. Fluxo de Decisão Completo

```
Open-Meteo ──► coletor ──► MQTT sensor ──► gateway ──► regra de risco
                                               │              │
                                               │              └─ alto/crítico ─► alerta MQTT ─► atuador
                                               │
                                               └─ a cada 600 s ─► NASA FIRMS
                                                                      │
                                                                      ├─ coordenadas dos focos
                                                                      ├─ FRP / confiança / satélite
                                                                      ├─ horário / período
                                                                      └─ distância por Haversine
                                                                                │
                                                                                ▼
                                                                  MQTT satelite/firms (retido)
                                                                                │
                                                                                ▼
                                                                  dashboard + mapa interativo
```

---

## 8. Extensibilidade e Trabalhos Futuros

- **TLS/mTLS no broker MQTT**: substituir broker público por instância privada com autenticação mútua por certificado X.509.
- **Banco de dados de séries temporais**: integrar InfluxDB ou TimescaleDB para persistência e análise histórica das leituras.
- **Geofencing dinâmico**: permitir configuração de zonas e coordenadas via API REST sem necessidade de redeploy.
- **Machine Learning**: substituir o motor de regras estático por modelo de classificação treinado com dados históricos INPE/FIRMS.
- **Hardware de campo**: substituir ou complementar as fontes remotas com ESP32, sensor MQ-2/PM2.5 para fumaça e DHT22 para temperatura/umidade, usando firmware MicroPython e obtendo medições ambientais locais.
- **Integração com Defesa Civil**: webhook para notificação automática de órgãos públicos em caso de risco crítico confirmado.

---

## 9. Estrutura do Repositório

```
projeto-iot/
├── plataforma/               # Edge (simuladores), fog (borda), cloud (central), contratos e infra
│   ├── contratos/            # Contrato canônico: JSON Schema, AsyncAPI e exemplos
│   ├── nucleo/               # Domínio compartilhado central + borda (mensagem, tópicos, validação, geo)
│   ├── central/              # Instância central (FastAPI): ingestão, catálogo, dispositivos, migrations, seed
│   ├── borda/                # Nó de borda (fog): fila cifrada, regras locais, sincronização
│   ├── simuladores/          # Dispositivos simulados (edge) e roteiro de demonstração
│   │   ├── zonas.py              # Tabela única das zonas e da região (borda) de cada uma
│   │   ├── estacao/sensor.py     # Estações: Open-Meteo/FIRMS + ruído, mensagem canônica
│   │   ├── atuador/atuador.py    # Atuadores da brigada: comando → ação → evento
│   │   ├── conector_firms/       # Conector NASA FIRMS (nível 3) → formato canônico
│   │   ├── terminal_go/          # Terminal de alerta das prefeituras (Go)
│   │   ├── demo/                 # Cadastro do domínio urbano e corte de link
│   │   └── mock_incendio.py      # CLI para ligar/desligar o mock de incêndio (ver 5.6)
│   ├── legado/ponte_front.py # TEMPORÁRIO: barramento novo → tópicos do painel antigo
│   ├── infra/mosquitto/      # Brokers da central e das bordas (com bridge)
│   ├── tests/                # Testes do núcleo e de contrato (pytest)
│   ├── Dockerfile            # Imagem Python compartilhada (estação/gateway/atuador)
│   ├── pyproject.toml        # Pacote nucleo + configuração de pytest/ruff
│   └── requirements.txt / requirements-dev.txt
├── docker-compose.yml  # Mosquitto + TimescaleDB + plataforma + front
├── .env.example        # Template de variáveis de ambiente
├── .env                # Configuração local (não versionado)
├── .gitignore
├── README.md           # Este documento
└── front/              # Dashboard SENTINELA (React/TanStack/Vite)
    ├── Dockerfile          # Build multi-stage (Nitro/node-server) + runtime
    ├── src/
    │   ├── routes/
    │   │   └── index.tsx       # Página principal do dashboard
    │   ├── components/
    │   │   └── dashboard/
    │   │       ├── ZoneCard.tsx    # Cartões de zona
    │   │       ├── SidePanel.tsx      # Painel lateral (mapa, feed, comandos)
    │   │       ├── FireMap.tsx        # Wrapper client-only do mapa
    │   │       ├── LeafletMapCore.tsx # Mapa Leaflet interativo
    │   │       └── StatusDot.tsx      # Indicador de status animado
    │   └── lib/
    │       ├── iot.ts              # MQTT WebSocket em tempo real
    │       └── sim.ts              # Fallback de demonstração
    └── package.json
```

---

## 10. Referências

- **MQTT Protocol Specification v3.1.1** — OASIS Standard, 2014. Disponível em: [https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/)
- **NASA FIRMS — Fire Information for Resource Management System**. Disponível em: [https://firms.modaps.eosdis.nasa.gov/](https://firms.modaps.eosdis.nasa.gov/)
- **Open-Meteo Weather API** — dados meteorológicos atuais por coordenada. Disponível em: [https://open-meteo.com/](https://open-meteo.com/)
- **VIIRS 375m Active Fire Product**. Schroeder, W. et al. *Remote Sensing of Environment*, 2014.
- **INPE — Programa Queimadas**. Instituto Nacional de Pesquisas Espaciais. Disponível em: [https://queimadas.dgi.inpe.br/](https://queimadas.dgi.inpe.br/)
- **MapBiomas Fogo — Mapeamento Anual**. Estatísticas de área queimada por município, inclusive 2025. Disponível em: [https://brasil.mapbiomas.org/iniciativas-e-produtos/fogo/mapeamento-anual/anual/](https://brasil.mapbiomas.org/iniciativas-e-produtos/fogo/mapeamento-anual/anual/)
- **paho-mqtt** — Eclipse Foundation. Python Client for MQTT. Disponível em: [https://github.com/eclipse/paho.mqtt.python](https://github.com/eclipse/paho.mqtt.python)
- **TanStack Start** — Documento oficial. Disponível em: [https://tanstack.com/start](https://tanstack.com/start)
- **Lovable Platform**. Disponível em: [https://lovable.dev/](https://lovable.dev/)

---

*Projeto desenvolvido para a disciplina de Internet das Coisas — 2026.*

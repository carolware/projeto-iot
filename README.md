# SENTINELA — Sistema IoT de Monitoramento e Alerta de Incêndios Florestais

> Projeto de Disciplina — Internet das Coisas (IoT)

---

## Resumo

Este projeto apresenta a concepção, implementação e integração de uma arquitetura IoT distribuída para a detecção precoce de incêndios florestais em tempo real. O sistema combina sensores virtuais multiparamétricos, comunicação assíncrona via protocolo MQTT, raciocínio baseado em regras no componente gateway e validação geoespacial por meio da API pública NASA FIRMS (*Fire Information for Resource Management System*). A solução é complementada por um dashboard web reativo denominado **SENTINELA**, desenvolvido com React/TanStack Start e exibido em plataforma Lovable. A arquitetura implementa o paradigma *edge-to-cloud* com filtragem progressiva de dados, reduzindo tráfego de rede e latência de resposta a eventos críticos.

---

## 1. Introdução e Motivação

O Brasil possui aproximadamente 12% da superfície terrestre coberta por biomas altamente suscetíveis a incêndios — Cerrado, Amazônia, Caatinga e Pantanal — sendo que o período de estiagem prolongado eleva dramaticamente o índice de ocorrências. Segundo dados do INPE (Instituto Nacional de Pesquisas Espaciais), o país registra dezenas de milhares de focos de calor por ano, muitos dos quais poderiam ser mitigados com detecção antecipada.

No contexto da disciplina de Internet das Coisas, o presente trabalho propõe uma abordagem baseada em quatro princípios:

1. **Heterogeneidade de fontes**: fusão de dados de sensores locais (temperatura, umidade relativa e concentração de fumaça) com dados orbitais de satélites.
2. **Processamento na borda** (*edge computing*): a lógica de inferência de risco é executada no gateway, sem dependência de nuvem para decisões de latência crítica.
3. **Comunicação assíncrona orientada a eventos**: o protocolo MQTT (Message Queuing Telemetry Transport) garante entrega confiável com overhead mínimo de protocolo.
4. **Atuação automatizada**: comandos de resposta são emitidos pelo gateway e recebidos pelo atuador, fechando o laço de controle sem intervenção humana obrigatória.

---

## 2. Arquitetura do Sistema

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ARQUITETURA SENTINELA                        │
├─────────────────┬───────────────────────────────────────────────────┤
│  CAMADA FÍSICA  │  Sensores (simulados) por zona geográfica          │
│                 │  Parâmetros: temperatura, umidade, fumaça          │
├─────────────────┼───────────────────────────────────────────────────┤
│  CAMADA REDE    │  Broker MQTT (HiveMQ public / próprio)             │
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
│  VISUALIZAÇÃO   │  Exibição em tempo real no Lovable                 │
└─────────────────┴───────────────────────────────────────────────────┘
```

### 2.1 Topologia de Tópicos MQTT

O namespace MQTT adota hierarquia em quatro níveis para possibilitar subscrições seletivas com wildcards:

| Direção      | Padrão de Tópico                                                     | Descrição                          |
|--------------|----------------------------------------------------------------------|------------------------------------|
| Sensor → GW  | `sentinela-iot-2026-joao-carol/chapada-veadeiros/{zona}/sensor/temperatura`             | Leitura de temperatura (°C)        |
| Sensor → GW  | `sentinela-iot-2026-joao-carol/chapada-veadeiros/{zona}/sensor/umidade`                 | Umidade relativa (%)               |
| Sensor → GW  | `sentinela-iot-2026-joao-carol/chapada-veadeiros/{zona}/sensor/fumaca`                  | Concentração de fumaça (%)         |
| GW → Atuador | `sentinela-iot-2026-joao-carol/chapada-veadeiros/{zona}/atuador/alerta`                 | Comando de alerta e ação           |

O gateway subscreve o padrão `sentinela-iot-2026-joao-carol/chapada-veadeiros/+/sensor/+` (wildcard `+` = um nível), recebendo automaticamente todos os sensores de todas as zonas cadastradas.

### 2.2 Diagrama de Sequência — Evento de Risco Crítico

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

### 3.1 Sensor (`sensor.py`)

Simula dispositivos físicos distribuídos por quatro zonas geográficas. Cada zona mantém um estado persistente de temperatura, umidade e fumaça que evolui segundo passeio aleatório (*random walk*) com deriva periódica simulando eventos de ignição (probabilidade ~2% por ciclo).

**Parâmetros publicados a cada 3 segundos por zona:**

| Campo        | Unidade | Faixa normal | Faixa de evento |
|--------------|---------|-------------|-----------------|
| temperatura  | °C      | 20–35       | 35–52           |
| umidade      | %       | 30–70       | 5–25            |
| fumaca       | %       | 0–20        | 40–100          |

**Formato de payload (JSON):**
```json
{
  "valor": 72.3,
  "timestamp": "2026-09-09T18:45:00.123456+00:00"
}
```

### 3.2 Gateway (`gateway.py`)

Componente central da arquitetura. Responsável por:

#### 3.2.1 Agregação de Estado por Zona
Mantém um objeto `EstadoZona` thread-safe por zona, acumulando as três leituras antes de executar qualquer avaliação. Isso evita avaliações de risco com dados parciais (e.g., receber apenas fumaça sem umidade).

#### 3.2.2 Motor de Regras — Avaliação de Risco

A função `avaliar_risco(fumaca, umidade, temperatura)` implementa um conjunto de regras baseado em limiares físicos empiricamente estabelecidos:

```
SE  fumaca ≥ 85%  E  umidade < 15%            → CRÍTICO
SE  fumaca ≥ 60%  E  umidade < 25%            → ALTO
SE  fumaca ≥ 40%  OU umidade < 35%  OU temp > 33°C → MÉDIO
SENÃO                                          → BAIXO
```

A combinação fumaça alta + umidade baixa é o indicador mais confiável de combustão ativa: a fumaça indica material particulado em suspensão (sub-produto da oxidação) enquanto a umidade baixa indica ausência de vapor d'água — condição que favorece a propagação das chamas e reduz a capacidade de autoextinção da vegetação.

#### 3.2.3 Integração NASA FIRMS

Quando o risco é **médio ou superior**, o gateway consulta a API FIRMS com um bounding box de ±0.15° (≈16 km) ao redor das coordenadas da zona. A consulta usa a fonte **VIIRS_SNPP_NRT** (Visible Infrared Imaging Radiometer Suite — Suomi NPP, Near Real-Time), com resolução espacial de 375 m e latência de ≈3 horas.

**Endpoint consultado:**
```
GET https://firms.modaps.eosdis.nasa.gov/api/area/csv
    /{MAP_KEY}/VIIRS_SNPP_NRT
    /{west},{south},{east},{north}/{days}
```

O CSV retornado contém campos `latitude`, `longitude`, `brightness`, `frp` (Fire Radiative Power, em MW), `confidence` e outros. O gateway extrai:
- **Número de focos** na área
- **FRP máximo** (W/m²) — proxy da intensidade do incêndio
- **Distância mínima** ao sensor (cálculo euclidiano em graus, convertido para km com fator 111 km/°)

Um mecanismo de **cooldown** (padrão: 60 s) evita requisições excessivas à API para a mesma zona.

#### 3.2.4 Publicação de Alertas

Quando o risco é **alto ou crítico**, o gateway publica no tópico `atuador/alerta` da zona um payload JSON completo com todos os dados do evento:

```json
{
  "zona": "alto-paraiso",
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

Interface web desenvolvida com **React 19**, **TanStack Router/Start**, **Tailwind CSS v4** e componentes **Radix UI/Shadcn**. O frontend está integrado à plataforma **Lovable** para deploy contínuo.

O dashboard opera por padrão em modo real: o módulo `iot.ts` estabelece uma conexão MQTT sobre WebSocket seguro (WSS), subscreve os tópicos de telemetria e alerta e atualiza a interface imediatamente. O módulo `sim.ts` permanece disponível como modo de demonstração independente; para utilizá-lo, defina `VITE_DATA_MODE=sim` em `front/.env`.

**Funcionalidades do dashboard:**
- Visão geral de todas as zonas monitoradas com níveis de risco em tempo real
- Painel de confirmação via satélite NASA FIRMS com animação de varredura
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
| Broker        | broker.hivemq.com | Broker público gratuito para prototipagem|
| Porta         | 1883              | TCP sem TLS (ambiente de desenvolvimento)|
| QoS (sensor)  | 0 (at most once)  | Dados de telemetria — perda ocasional aceitável |
| QoS (alerta)  | 1 (at least once) | Comandos críticos — confirmação necessária |
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

# Instalar dependências
pip install -r requirements.txt

# Configurar variáveis de ambiente
cp .env.example .env
# Editar .env e preencher FIRMS_MAP_KEY com sua chave da NASA
```

### 5.3 Execução — Ordem Recomendada

Abra três terminais separados na raiz do projeto:

```bash
# Terminal 1 — Atuador (deve estar pronto antes do gateway publicar)
python atuador.py

# Terminal 2 — Gateway (motor de regras + FIRMS)
python gateway.py

# Terminal 3 — Sensor (gerador de dados)
python sensor.py
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
| `MQTT_BROKER`        | broker.hivemq.com   | Endereço do broker MQTT                  |
| `MQTT_PORT`          | 1883                | Porta TCP do broker                      |
| `MQTT_TOPIC_BASE`    | sentinela-iot-2026-joao-carol/chapada-veadeiros | Namespace isolado do projeto |
| `FIRMS_MAP_KEY`      | —                   | **Obrigatório**: chave de acesso NASA FIRMS |
| `FIRMS_SOURCE`       | VIIRS_SNPP_NRT      | Fonte de dados orbital                   |
| `FIRMS_DAYS`         | 1                   | Janela temporal de busca (dias)          |
| `FIRMS_RAIO_GRAUS`   | 0.15                | Raio de busca em graus (~16 km)          |
| `FIRMS_COOLDOWN_SEG` | 60                  | Cooldown entre consultas FIRMS por zona  |
| `ALERTA_COOLDOWN_SEG`| 30                  | Intervalo mínimo entre alertas da mesma zona |
| `ACAO_COOLDOWN_SEG`  | 30                  | Cooldown entre ações repetidas (atuador) |

---

## 6. Mapeamento de Zonas

As zonas monitoradas correspondem a localidades brasileiras reais na região da **Chapada dos Veadeiros, em Goiás**, área do bioma Cerrado sujeita a incêndios durante a estação seca:

| Zona                  | Lat          | Lon          | Sensor ID |
|-----------------------|-------------|-------------|-----------|
| Alto Paraíso de Goiás | -14.1330    | -47.5170    | GO-AP-01  |
| Vila de São Jorge     | -14.1775    | -47.8140    | GO-SJ-02  |
| Cavalcante            | -13.7975    | -47.4583    | GO-CV-03  |
| Colinas do Sul        | -14.1528    | -48.0760    | GO-CS-04  |

Para alterar as coordenadas, edite o dicionário `ZONA_COORDS` em `gateway.py`.

---

## 7. Fluxo de Decisão Completo

```
                    ┌──────────────────────┐
                    │  Nova leitura MQTT   │
                    │  (temp/umid/fumaca)  │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  Leitura completa?   │
                    │  (3 sensores ok)     │
                    └──────────┬───────────┘
                          Sim  │
                    ┌──────────▼───────────┐
                    │  Avaliar Risco       │
                    │  (motor de regras)   │
                    └──────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
           Baixo               Médio          Alto/Crítico
              │                │                │
           Ignorar     ┌───────▼────────┐  ┌───▼───────────┐
                        │ Cooldown       │  │ Cooldown       │
                        │ expirou?       │  │ expirou?       │
                        └───────┬────────┘  └───────┬────────┘
                            Sim │               Sim  │
                        ┌───────▼────────┐  ┌───────▼────────┐
                        │ Consulta FIRMS │  │ Consulta FIRMS │
                        └───────┬────────┘  └───────┬────────┘
                                │                   │
                           Log resultado    ┌───────▼────────┐
                                            │ Pub alerta     │
                                            │ MQTT atuador   │
                                            └───────┬────────┘
                                                    │
                                            ┌───────▼────────┐
                                            │  Atuador       │
                                            │  executa ação  │
                                            └────────────────┘
```

---

## 8. Extensibilidade e Trabalhos Futuros

- **TLS/mTLS no broker MQTT**: substituir broker público por instância privada com autenticação mútua por certificado X.509.
- **Banco de dados de séries temporais**: integrar InfluxDB ou TimescaleDB para persistência e análise histórica das leituras.
- **Geofencing dinâmico**: permitir configuração de zonas e coordenadas via API REST sem necessidade de redeploy.
- **Machine Learning**: substituir o motor de regras estático por modelo de classificação treinado com dados históricos INPE/FIRMS.
- **Hardware real**: migrar do sensor simulado para microcontroladores com sensores MQ-2 (fumaça), DHT22 (temperatura/umidade) via ESP32 com firmware MicroPython.
- **Integração com Defesa Civil**: webhook para notificação automática de órgãos públicos em caso de risco crítico confirmado.
- **Dashboard com MQTT real**: substituir o `sim.ts` por conexão WebSocket ao broker usando `mqtt.js`, consumindo dados reais do sensor e gateway.

---

## 9. Estrutura do Repositório

```
projeto-iot/
├── sensor.py           # Gerador de dados de sensores (publica via MQTT)
├── gateway.py          # Motor de regras + integração NASA FIRMS + alertas
├── atuador.py          # Recebe comandos e executa ações de resposta
├── requirements.txt    # Dependências Python
├── .env.example        # Template de variáveis de ambiente
├── .env                # Configuração local (não versionado)
├── .gitignore
├── README.md           # Este documento
└── front/              # Dashboard SENTINELA (React/TanStack/Lovable)
    ├── src/
    │   ├── routes/
    │   │   └── index.tsx       # Página principal do dashboard
    │   ├── components/
    │   │   └── dashboard/
    │   │       ├── ZoneCard.tsx    # Cartões de zona
    │   │       ├── SidePanel.tsx   # Painel lateral (FIRMS, feed, comandos)
    │   │       └── StatusDot.tsx   # Indicador de status animado
    │   └── lib/
    │       └── sim.ts          # Simulação do fluxo IoT no frontend
    └── package.json
```

---

## 10. Referências

- **MQTT Protocol Specification v3.1.1** — OASIS Standard, 2014. Disponível em: [https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/)
- **NASA FIRMS — Fire Information for Resource Management System**. Disponível em: [https://firms.modaps.eosdis.nasa.gov/](https://firms.modaps.eosdis.nasa.gov/)
- **VIIRS 375m Active Fire Product**. Schroeder, W. et al. *Remote Sensing of Environment*, 2014.
- **INPE — Programa Queimadas**. Instituto Nacional de Pesquisas Espaciais. Disponível em: [https://queimadas.dgi.inpe.br/](https://queimadas.dgi.inpe.br/)
- **paho-mqtt** — Eclipse Foundation. Python Client for MQTT. Disponível em: [https://github.com/eclipse/paho.mqtt.python](https://github.com/eclipse/paho.mqtt.python)
- **TanStack Start** — Documento oficial. Disponível em: [https://tanstack.com/start](https://tanstack.com/start)
- **Lovable Platform**. Disponível em: [https://lovable.dev/](https://lovable.dev/)

---

*Projeto desenvolvido para a disciplina de Internet das Coisas — 2026.*

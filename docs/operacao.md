# Operação

## 1. Pré-requisitos

- Docker com Compose v2 (testado com Docker 27).
- Para os scripts de demonstração e os testes: Python 3.12+.
- Para desenvolver o painel fora do Docker: Node 22+.
- Opcional: chave gratuita da NASA FIRMS (https://firms.modaps.eosdis.nasa.gov/api/).
  Sem ela, as estações usam só o Open-Meteo e o conector FIRMS fica ocioso; o
  modo de incêndio forçado funciona do mesmo jeito.

## 2. Subir e parar

```bash
cp .env.example .env              # segredos de desenvolvimento; preencha FIRMS_MAP_KEY se tiver
docker compose up -d --build      # sobe tudo
docker compose ps                 # 19 em execução (o 20º, pki, termina após gerar os certificados)
docker compose logs -f central borda-goias
docker compose down               # para (os volumes ficam)
docker compose down -v            # para e apaga banco, filas e sessões
```

O serviço `central` aplica as migrations e o seed (idempotentes) antes de subir
a API. O `pki` gera a CA e os certificados na primeira execução, em
`plataforma/infra/pki/gerado/`.

## 3. Serviços

| Serviço | Porta na máquina | Papel |
|---|---|---|
| `front` | 3000 | painel web |
| `central` | 8000 | API REST, Swagger (`/docs`), WebSocket, motor de regras, `/metrics/` |
| `central-ingestao` | 9103 | ingestão MQTT em micro-lotes, `/metrics` |
| `keycloak` | 8080 | identidade (realm `sentinela`; console com `admin` / `KEYCLOAK_ADMIN_SENHA`) |
| `postgres` | 5433 | PostgreSQL 16 + TimescaleDB |
| `mosquitto-central` | 8883 | broker MQTT/TLS da central |
| `mosquitto-borda-tocantins` / `-goias` | 8884 / 8885 | brokers locais das bordas |
| `borda-tocantins` / `borda-goias` | 9101 / 9102 | nós de borda, `/metrics` |
| `borda-tocantins` / `borda-goias` | 8081 / 8082 | HTTP local da borda: `POST /api/v1/ingestao` e painel do técnico (`/`, `/estado`; senha `BORDA_PAINEL_SENHA`) |
| `prometheus` | 9090 | métricas e alertas operacionais |
| `jaeger` | 16686 | traces distribuídos (OpenTelemetry) |
| `estacoes-tocantins`, `estacoes-goias`, `estacoes-diretas` | — | estações simuladas |
| `atuadores-tocantins`, `atuadores-goias`, `atuadores-diretos` | — | atuadores da brigada |
| `conector-firms` | — | adaptador NASA FIRMS |
| `terminais` | — | terminais de alerta das prefeituras (Go) |
| `pki` | — | gera a CA (roda e termina) |

## 4. Variáveis de ambiente (`.env`)

| Variável | Para quê |
|---|---|
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_PORT` | banco (porta 5433 na máquina) |
| `MQTT_BROKER`, `MQTT_PORT` | broker usado pelos scripts na máquina (`localhost:8883`) |
| `SEGREDO_PROVISIONAMENTO` | deriva a credencial dos dispositivos simulados (HMAC) |
| `CREDENCIAL_BORDA_TOCANTINS`, `CREDENCIAL_BORDA_GOIAS` | credencial de cada nó de borda (API e bridge) |
| `BORDA_CHAVE_TOCANTINS`, `BORDA_CHAVE_GOIAS` | chave Fernet da fila local de cada borda |
| `MQTT_SENHA_CENTRAL`, `MQTT_SENHA_SIMULACAO` | usuários de serviço dos brokers |
| `KEYCLOAK_ADMIN_SENHA` | console de administração do Keycloak |
| `FIRMS_MAP_KEY`, `FIRMS_SOURCE`, `FIRMS_DAYS`, `FIRMS_RAIO_GRAUS`, `FIRMS_COOLDOWN_SEG` | NASA FIRMS |
| `SENSOR_REFRESH_METEO_MIN`, `SENSOR_REFRESH_FIRMS_MIN` | frequência das consultas externas das estações |
| `MOCK_ZONA_INCENDIO`, `MOCK_TEMPERATURA`, `MOCK_UMIDADE`, `MOCK_FUMACA`, `MOCK_LAT`, `MOCK_LON` | incêndio forçado desde o boot (ver §6) |
| `ALERTA_COOLDOWN_SEG`, `ACAO_COOLDOWN_SEG` | intervalo mínimo entre alertas/ações repetidos |

Variáveis definidas no próprio `docker-compose.yml` (não precisam de `.env`):
`LOG_FORMATO=json`, `MQTT_CA`, `CENTRAL_INGESTAO_SEPARADA`,
`CENTRAL_MQTT_CLIENT_ID`, `CENTRAL_MQTT_GRUPO`, `BORDA_ID`, `BORDA_SITES`,
`CENTRAL_URL`, `OIDC_EMISSOR`, `OIDC_JWKS_URL`, `AUTO_RECONHECER_S` (terminais).
Outras úteis: `LOG_NIVEL=DEBUG` (loga cada mensagem), `CORS_ORIGENS`.

> Os valores do `.env.example` são só para desenvolvimento: troque todos fora
> da sua máquina.

## 5. Zonas

| Zona | UF | Nó de borda | Estação | Atuador |
|---|---|---|---|---|
| anapolis, formosa, pirenopolis, jaragua | GO | `borda-goias` | `est-{zona}-01` | `atuador-{zona}-01` |
| mateiros, lagoa-da-confusao, sandolandia | TO | `borda-tocantins` | idem | idem |
| novo-progresso | PA | — (direto) | idem | idem |
| mirador | MA | — (direto) | idem | idem |

Cada zona também tem um conector `firms-{zona}`. As prefeituras (`pref-anapolis`,
`pref-formosa`, `pref-pirenopolis`, `pref-jaragua`) têm os sites `prefeitura`,
`defesa-civil` e `bombeiros`.

## 6. Simular incêndio

Em tempo real, sem reiniciar nada:

```bash
python plataforma/simuladores/mock_incendio.py on mateiros
python plataforma/simuladores/mock_incendio.py on mateiros --temperatura 45 --umidade 5 --fumaca 98
python plataforma/simuladores/mock_incendio.py on mateiros --lat -10.55 --lon -46.30   # foco FIRMS nesse ponto
python plataforma/simuladores/mock_incendio.py off
```

O script publica no tópico de controle `simulacao/mock` (usuário `simulacao`,
TLS) do broker da região e da central. Com `--lat/--lon`, a posição vai em
`local.geo` e o conector FIRMS fabrica uma detecção de satélite ali (dentro do
raio de busca da zona). As mensagens geradas trazem `"fonte": "mock"`.

Queda de link de uma região:

```bash
sh plataforma/simuladores/demo/link.sh off tocantins   # ou goias
sh plataforma/simuladores/demo/link.sh on tocantins
```

## 7. Testes e qualidade

```bash
cd plataforma
pip install -r requirements-dev.txt -e .
ruff check nucleo central borda tests && ruff format --check nucleo central borda tests
pytest --cov=nucleo                    # unidade + integração
```

Os testes de integração usam o TimescaleDB do compose num banco descartável
(`sentinela_teste`) e são pulados se ele estiver fora do ar; os de MQTT real
pedem um broker sem TLS e normalmente ficam pulados. O GitHub Actions
(`.github/workflows/ci.yml`) roda ruff, pytest com cobertura mínima de 70% no
`nucleo`, o build das imagens, os testes do terminal Go e o build do painel.

## 8. Logs e métricas

- Logs JSON, uma linha por evento, com `id_mensagem` e `alarme_id` quando
  houver: `docker compose logs borda-goias central | grep <id_mensagem>`.
- Métricas: `localhost:8000/metrics/` (API e motor), `localhost:9103/metrics`
  (ingestão), `localhost:9101` e `:9102` (bordas).

| Métrica | O que mede |
|---|---|
| `sentinela_mensagens_total{canal,status}` | mensagens aceitas, duplicadas e recusadas por canal (mqtt, http, lote) |
| `sentinela_ingestao_latencia_segundos` | da medição até a mensagem ser aceita |
| `sentinela_regra_latencia_segundos` | da medição até o alarme aberto pela central |
| `sentinela_alarmes_total`, `sentinela_acoes_total` | alarmes abertos, ações por tipo e resultado |
| `sentinela_motor_fila`, `sentinela_tempo_real_conexoes` | fila do motor, painéis conectados |
| `sentinela_http_duracao_segundos` | duração por rota HTTP |
| `borda_fila_pendente`, `borda_link_central_online`, `borda_ultimo_sync_timestamp` | saúde de cada nó de borda |

Alertas operacionais (`plataforma/infra/prometheus/alertas.yml`): serviço fora,
borda sem sincronizar, fila da borda crescendo, mensagens recusadas, motor
atrasado, latência alta e ações falhando.

## 9. Teste de carga

```bash
python plataforma/simuladores/carga/carga.py --taxa 1000 --dispositivos 50 --duracao 60
```

Cria pela API o domínio `carga`, 50 dispositivos no site `mirador` e a regra
`pico-carga`; publica por MQTT/TLS; mede publicação → painel e pico → alarme pelo
WebSocket; confere pela métrica da ingestão que nada se perdeu. Opções:
`--sem-painel` (só vazão), `--picos`, `--metricas`.

## 10. Rodar um componente fora do Docker

```bash
pip install -r plataforma/requirements.txt -e plataforma
cd plataforma && python -m borda.app        # ou: uvicorn central.app:app --reload
```

Fora do compose a API ingere o MQTT ela mesma (modo embutido); para separar,
use `CENTRAL_INGESTAO_SEPARADA=1` e rode `python -m central.ingestao_mqtt`.

O painel em modo de desenvolvimento: `cd front && npm install && npm run dev`
(usa `VITE_API_URL` e `VITE_KEYCLOAK_URL`, que na imagem são fixados no build;
mudou, recrie com `docker compose up -d --build front`).

## 11. Problemas conhecidos

| Sintoma | Causa e solução |
|---|---|
| Mensagens somem depois de mudar o número de réplicas da `central-ingestao` | a sessão persistente da réplica removida continua no grupo `$share` e recebe a parte dela. Conecte uma vez com o client id dela e `clean_session=True`, ou recrie o volume `mosquitto-central-data` |
| `SUBACK` aceito, mas nada chega | o Mosquitto 2.0 confirma a assinatura de um tópico proibido pela ACL e não entrega nada; confira os tópicos do dispositivo |
| Painel com mapa vazio | os tiles vêm da Esri; sem internet o mapa fica sem fundo, mas os pontos aparecem |

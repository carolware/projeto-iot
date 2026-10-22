# Kit do integrador

Como conectar um dispositivo ou sistema novo à plataforma Sentinela usando só
protocolos padrão (MQTT e HTTP) e o contrato publicado. Não há biblioteca
proprietária (RF-INT-02, RF-INT-07).

Conteúdo:

| Arquivo | Para quê |
|---|---|
| este guia | passo a passo |
| [`colecao.http`](colecao.http) | coleção de requisições (VS Code REST Client ou IntelliJ) |
| [`exemplos/python`](exemplos/python/dispositivo.py) | dispositivo com `paho-mqtt` + `requests` |
| [`exemplos/typescript`](exemplos/typescript/dispositivo.ts) | dispositivo com `mqtt` + `fetch` (Node 22.18+) |
| [`exemplos/go`](exemplos/go/main.go) | dispositivo com `paho.mqtt.golang` + `net/http` |

Os três exemplos fazem a mesma coisa: publicam três leituras por MQTT/TLS,
enviam uma por HTTP, assinam o próprio `/comando` e respondem a cada comando com
um evento `executado`.

## 1. O que você precisa

- A plataforma no ar (`docker compose up -d`, ver o [README](../../README.md)).
- O certificado da CA da plataforma: `plataforma/infra/pki/gerado/ca.crt`
  (gerado pelo serviço `pki`). O broker só aceita TLS 1.2+.
- Um usuário com papel **integrador** ou **gestor**. No ambiente de
  demonstração: `integrador.brigada`, senha `sentinela`.

## 2. Contrato

- **Mensagem canônica v1.0** (JSON Schema 2020-12):
  `http://localhost:8000/contratos/mensagem-canonica.v1.schema.json`.
- **Interface MQTT** (AsyncAPI 3): `http://localhost:8000/contratos/asyncapi.yaml`.
- **API REST** (OpenAPI 3): `http://localhost:8000/docs`.
- Exemplos válidos: `http://localhost:8000/contratos/exemplos/…` (lista em
  `plataforma/contratos/exemplos/`).
- Histórico de versões: [`plataforma/contratos/CHANGELOG.md`](../../plataforma/contratos/CHANGELOG.md).

Regras que mais dão erro:

| Campo | Regra |
|---|---|
| `id_mensagem` | UUID gerado **no dispositivo**. Reenviar o mesmo id é seguro: a plataforma grava uma vez só. |
| `timestamp` | ISO 8601 **com fuso** (use UTC, `…Z`); é o horário da medição, não do envio. |
| `medidas[].unidade` | código UCUM (`Cel`, `%`, `1`, `ug/m3`, `km`…); a plataforma converte para a unidade do perfil. |
| `medidas[].grandeza` | precisa existir no perfil do dispositivo. |
| tópico × mensagem | o `dispositivo` e o `tipo` da mensagem precisam bater com o tópico. |

## 3. Passo a passo

### 3.1 Escolher o perfil e o local

Um **perfil** define as grandezas, unidades, faixas válidas e o intervalo
esperado de envio (usado para marcar online/offline).

```bash
TOKEN=$(curl -s localhost:8080/realms/sentinela/protocol/openid-connect/token \
  -d grant_type=password -d client_id=cli -d username=integrador.brigada -d password=sentinela | jq -r .access_token)
curl -s 'localhost:8000/api/v1/catalogo/perfis?dominio=incendio' -H "Authorization: Bearer $TOKEN" | jq
curl -s 'localhost:8000/api/v1/sites' -H "Authorization: Bearer $TOKEN" | jq '.itens[].slug'
```

Domínio novo é só configuração: um **gestor** cria o domínio
(`POST /api/v1/catalogo/dominios`) e o integrador cria o perfil
(`POST /api/v1/catalogo/perfis`), sem alterar código (RNF-08).

### 3.2 Cadastrar o dispositivo

```bash
curl -s -X POST localhost:8000/api/v1/dispositivos -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{
    "slug": "sensor-teste-01", "nome": "Sensor de teste", "tipo": "estacao",
    "dominio": "incendio", "perfil": "estacao-incendio", "site": "mirador",
    "fabricante": "Equipe X", "firmware": "0.1.0"}' | jq
```

A resposta traz a **credencial**, mostrada **uma única vez**; a plataforma
guarda só o hash. Perdeu? `POST /api/v1/dispositivos/{slug}/credencial` emite
outra, e a anterior deixa de valer. Em poucos segundos o broker passa a aceitar
o dispositivo, com ACL restrita aos tópicos dele.

> O site `mirador` fala direto com a central (porta 8883). Um dispositivo de um
> site atrás de nó de borda (ex.: `mateiros`) conecta no broker da borda
> (8884 Tocantins, 8885 Goiás). A credencial é a mesma e chega à borda em até 30 s.

### 3.3 Publicar por MQTT

| Item | Valor |
|---|---|
| Broker | `localhost:8883` (TLS, verificar com `ca.crt`) |
| Usuário / senha | slug / credencial |
| Client id | o slug |
| Publica em | `{inquilino}/{site}/{dominio}/{slug}/telemetria` (também `/evento` e `/estado`) |
| Assina | `{inquilino}/{site}/{dominio}/{slug}/comando` |
| QoS | 1 |

Exemplo: `brigada-cerrado/mirador/incendio/sensor-teste-01/telemetria`.

### 3.4 Ou publicar por HTTP

```bash
curl -i -X POST localhost:8000/api/v1/ingestao -u sensor-teste-01:SUA_CREDENCIAL \
  -H 'content-type: application/json' -d "{
    \"versao\": \"1.0\", \"id_mensagem\": \"$(uuidgen)\", \"dispositivo\": \"sensor-teste-01\",
    \"tipo\": \"telemetria\", \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%S.000Z)\",
    \"medidas\": [{\"grandeza\": \"temperatura\", \"valor\": 31.5, \"unidade\": \"Cel\"}]}"
```

A credencial de um dispositivo só envia mensagens dele mesmo.

`202` = aceita (ou `duplicada`, se o id já existia). `400` = recusada, com a
lista de erros por campo; a mensagem fica na DLQ com o motivo. Para vários de
uma vez: `POST /api/v1/ingestao/lote` devolve o resultado de cada mensagem.

### 3.5 Responder a comandos

Um comando chega no `/comando` como mensagem canônica `tipo: comando`, com
`dados.acao`. Responda publicando no próprio `/evento`:

```json
{ "versao": "1.0", "id_mensagem": "<novo uuid>", "dispositivo": "sensor-teste-01",
  "tipo": "evento", "timestamp": "2026-10-22T01:30:00.000Z",
  "dados": { "evento": "executado", "comando_id": "<id_mensagem do comando>" } }
```

A plataforma associa a resposta ao comando, que passa de `enviado` para
`confirmado` (`GET /api/v1/dispositivos/{slug}/comandos`).

### 3.6 Conferir

```bash
curl -s localhost:8000/api/v1/dispositivos/sensor-teste-01/estado -H "Authorization: Bearer $TOKEN" | jq
curl -s 'localhost:8000/api/v1/dispositivos/sensor-teste-01/telemetria?grandeza=temperatura' -H "Authorization: Bearer $TOKEN" | jq
```

## 4. Rodar os exemplos

Da raiz do repositório, com `SLUG` e `CREDENCIAL` do passo 3.2:

```bash
export SLUG=sensor-teste-01 CREDENCIAL=...

# Python
pip install paho-mqtt==1.6.1 requests
python docs/integrador/exemplos/python/dispositivo.py

# TypeScript (Node 22.18+ executa .ts direto)
(cd docs/integrador/exemplos/typescript && npm install)
node docs/integrador/exemplos/typescript/dispositivo.ts

# Go 1.23+
(cd docs/integrador/exemplos/go && go run .)
```

Enquanto o exemplo espera, mande um comando pelo painel (operador) ou pela API e
veja-o chegar e ser confirmado:

```bash
TOKEN_OP=$(curl -s localhost:8080/realms/sentinela/protocol/openid-connect/token \
  -d grant_type=password -d client_id=cli -d username=operador.brigada -d password=sentinela | jq -r .access_token)
curl -s -X POST localhost:8000/api/v1/dispositivos/$SLUG/comandos -H "Authorization: Bearer $TOKEN_OP" \
  -H 'content-type: application/json' -d '{"acao": "reforcar_monitoramento"}'
```

Variáveis aceitas pelos três: `SLUG`, `CREDENCIAL`, `INQUILINO`
(`brigada-cerrado`), `SITE` (`mirador`), `DOMINIO` (`incendio`), `MQTT_HOST`
(`localhost`), `MQTT_PORT` (`8883`), `MQTT_CA`
(`plataforma/infra/pki/gerado/ca.crt`) e `API` (`http://localhost:8000`).

## 5. Adaptadores e conectores (outros protocolos ou sistemas)

Um sistema que não fala o contrato entra por um **adaptador**: um contêiner
separado que traduz o formato de origem para a mensagem canônica e publica como
um dispositivo comum (RF-INT-03). O núcleo não é recompilado nem reiniciado.

O conector NASA FIRMS (`plataforma/simuladores/conector_firms/`) é o exemplo
pronto: consulta a API de focos de calor por satélite em intervalos e publica
eventos e telemetria canônicos com a credencial do dispositivo `firms-{zona}`.
Para criar um novo:

1. cadastre um perfil com as grandezas que ele vai publicar e um dispositivo
   `tipo: conector`;
2. escreva o adaptador na linguagem que quiser, usando o passo 3.3 ou 3.4;
3. suba o contêiner (um serviço novo no compose, ou `docker run` na rede `nuvem`).

Para receber dados da plataforma: **webhook** de saída numa regra
(`{"tipo": "webhook", "url": "https://…"}`, com novas tentativas e espera
crescente) ou o **WebSocket** `ws://localhost:8000/api/v1/tempo-real?token=…`,
filtrado pelo inquilino do token.

## 6. Problemas comuns

| Sintoma | Causa provável |
|---|---|
| `CONNACK 5` / não autorizado | credencial errada, revogada, ou o broker ainda não recarregou (espere alguns segundos) |
| conecta mas nada aparece | publicou num tópico que não é o seu (o Mosquitto aceita e descarta em silêncio); confira inquilino, site e domínio do cadastro |
| `400` com `topico` | inquilino/site/domínio do tópico diferem do cadastro |
| `400` com `medidas/0/grandeza` | grandeza fora do perfil |
| leitura marcada `suspeita` | valor fora da faixa do perfil (é gravada mesmo assim) |
| erro de certificado | falta `ca.crt`, ou conectou em um host que não está no certificado (use `localhost` ou o nome do serviço no compose) |

# Roteiro de demonstração

Roteiro para a apresentação. Cobre os critérios de aceite da §9.3 do documento
de requisitos e é referenciado pela [matriz de rastreabilidade](rastreabilidade.md).
Tempo estimado: 20 a 25 minutos. Todos os comandos rodam na raiz do repositório.

## Preparação (antes da apresentação)

```bash
cp .env.example .env                # e preencha FIRMS_MAP_KEY (opcional: sem ela as estações só usam Open-Meteo)
docker compose up -d --build        # ~2 min na primeira vez
pip install -r plataforma/requirements.txt   # para os scripts de demonstração
```

Confira: painel em http://localhost:3000, Swagger em http://localhost:8000/docs,
Prometheus em http://localhost:9090 (`Status → Targets` com tudo `UP`).

Tokens para os `curl` (usuários de demonstração, senha `sentinela`):

```bash
tk() { curl -s localhost:8080/realms/sentinela/protocol/openid-connect/token \
  -d grant_type=password -d client_id=cli -d username=$1 -d password=sentinela | jq -r .access_token; }
TOKEN=$(tk gestor.brigada)
TOKEN_ANAPOLIS=$(tk gestor.anapolis)
```

Janelas abertas: painel logado como `gestor.brigada`, outra aba (anônima) como
`gestor.anapolis`, e um terminal com
`docker compose logs -f borda-goias borda-tocantins terminais`.

---

## 1. Domínio novo só por configuração (RNF-08, RF-CEN-01)

O cenário urbano não existe no seed. Ele é criado agora, pela API, sem tocar no
código: domínio `alerta_publico`, perfil do terminal, dois terminais por cidade
e a assinatura "incêndios de severidade alta ou crítica a até 80 km".

```bash
python plataforma/simuladores/demo/cadastrar_alerta_publico.py
```

Mostrar: aba **Cadastro** do painel (gestor da prefeitura) e
`GET /api/v1/catalogo/dominios` no Swagger. Os terminais em Go passam a aparecer
online na visão da prefeitura.

## 2. Incêndio no campo → alerta na prefeitura (projeto rural + urbano)

```bash
python plataforma/simuladores/mock_incendio.py on pirenopolis
```

O que acontece (diagrama em [fluxos.md §1](fluxos.md#1-ponta-a-ponta-incêndio-na-fazenda--alerta-na-prefeitura)):

1. A estação de Pirenópolis manda fumaça 93 e umidade 10 para a **borda de Goiás**.
2. A borda decide sozinha (regra local `risco-critico`) e aciona o atuador da
   brigada. Nos logs: `ALARME LOCAL CRITICO em pirenopolis`.
3. A central abre o alarme crítico (painel da brigada fica vermelho em tempo real)
   e registra o SMS simulado.
4. Pirenópolis fica a 52 km de Anápolis: a assinatura entrega o alerta à
   prefeitura. O terminal da Defesa Civil exibe e, 20 s depois, o operador
   (simulado) reconhece. Na aba da prefeitura o alarme aparece **reconhecido**.

```bash
curl -s 'localhost:8000/api/v1/alarmes?abertos=true' -H "Authorization: Bearer $TOKEN_ANAPOLIS" | jq '.itens[] | {resumo, severidade, estado}'
```

## 3. Fim do incêndio

```bash
python plataforma/simuladores/mock_incendio.py off
```

A leitura volta ao normal, a central resolve o alarme da brigada, as entregas às
prefeituras também são resolvidas, e os terminais recebem `encerrar_alerta`.

## 4. Queda do link da fazenda (RF-EDG-01, 03)

```bash
sh plataforma/simuladores/demo/link.sh off tocantins
python plataforma/simuladores/mock_incendio.py on lagoa-da-confusao
curl -s localhost:9101/metrics | grep -E '^borda_(fila_pendente|link_central_online)'
```

Mostrar:
- a borda de Tocantins continua decidindo: `ALARME LOCAL CRITICO em
  lagoa-da-confusao` e o atuador local é acionado, **sem a central**;
- a fila cifrada crescendo (`borda_fila_pendente`) e `borda_link_central_online 0`;
- o **painel local** da borda (http://localhost:8081, usuário qualquer, senha
  `BORDA_PAINEL_SENHA`): "SEM LINK", fila pendente e o alarme local, tudo sem a
  central (RF-EDG-07);
- na tela **Bordas** do painel, "sem sincronizar há …" aumentando; no Prometheus,
  o alerta `BordaSemSincronizar` depois de ~1 min.

## 5. Volta do link: sem perda, sem duplicata (RF-EDG-02, RNF-04)

```bash
sh plataforma/simuladores/demo/link.sh on tocantins
curl -s localhost:9101/metrics | grep '^borda_fila_pendente'          # volta a 0 em segundos
curl -s 'localhost:8000/api/v1/alarmes?dispositivo=est-lagoa-da-confusao-01' \
  -H "Authorization: Bearer $TOKEN" | jq '.itens[0] | {regra, ocorrido_em, origem: .dados.origem}'
docker compose exec postgres psql -U sentinela -c \
  "select count(*), count(distinct id_mensagem) from mensagem m join dispositivo d on d.id = m.dispositivo_id
   where d.slug = 'est-lagoa-da-confusao-01' and medido_em > now() - interval '10 minutes'"
python plataforma/simuladores/mock_incendio.py off
```

- O alarme chega **antes** da telemetria acumulada e aparece com o horário em que
  ocorreu, com origem `borda`.
- As duas contagens são iguais: nenhuma duplicata.
- O critério de **72 h** é coberto pelo teste automatizado
  `test_desconexao_nao_perde_nem_duplica`:
  `cd plataforma && pytest tests/test_borda.py -k desconexao -v`.

## 6. Zona sem borda: a central decide

```bash
python plataforma/simuladores/mock_incendio.py on mirador
docker compose logs --since 1m atuadores-diretos | grep -A2 'BRIGADA ACIONADA'
python plataforma/simuladores/mock_incendio.py off
```

Mirador (MA) fala direto com a central: a mesma regra roda na nuvem e o comando
vai ao atuador pela central.

## 7. Integração em três linguagens (RF-INT-02, RF-INT-03)

- **Python**: estações, atuadores e o conector NASA FIRMS (um adaptador em
  contêiner próprio, que traduz a API de satélite para o contrato).
- **Go**: os terminais das prefeituras (`docker compose logs terminais`).
- **TypeScript**: o painel e o exemplo do kit do integrador.

Ao vivo, seguindo o [kit do integrador](integrador/README.md): cadastrar
`sensor-teste-01` pela API (ou pela `colecao.http`) e rodar um dos exemplos:

```bash
export SLUG=sensor-teste-01 CREDENCIAL=<da resposta do cadastro>
node docs/integrador/exemplos/typescript/dispositivo.ts      # ou python …/dispositivo.py, ou go run .
```

Durante a espera, enviar um comando como operador e mostrar que ele chega e é
confirmado (`GET /api/v1/dispositivos/sensor-teste-01/comandos`).

## 8. Segurança (RF-SEG-01..05, RF-DIS-02)

```bash
# revogar: a próxima conexão é recusada
curl -s -X POST localhost:8000/api/v1/dispositivos/sensor-teste-01/revogar -H "Authorization: Bearer $(tk integrador.brigada)"
python docs/integrador/exemplos/python/dispositivo.py
#   → MQTT: conexão recusada pelo broker (rc=5: Connection Refused: not authorised.)

# isolamento: a prefeitura não enxerga a brigada
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/api/v1/dispositivos/est-mateiros-01 -H "Authorization: Bearer $TOKEN_ANAPOLIS"   # 404

# papéis: o operador não altera regra
curl -s -o /dev/null -w '%{http_code}\n' -X PATCH localhost:8000/api/v1/regras/risco-alto \
  -H "Authorization: Bearer $(tk operador.brigada)" -H 'content-type: application/json' -d '{"ativa": false}'   # 403
```

- No painel: entrar como `leitor.brigada` (sem botões de comando nem cadastro)
  e como `admin` (o Keycloak exige configurar o TOTP).
- ACL: o broker só deixa cada dispositivo publicar nos próprios tópicos
  (`plataforma/central/acesso_mqtt.py`); todo MQTT é TLS com a CA própria.

## 9. Observabilidade (RF-OBS-01, 02, 03, 05)

```bash
# um alarme seguido da borda até a central pelo id_mensagem
ID=$(docker compose logs --no-log-prefix borda-goias | grep 'ALARME LOCAL' | tail -1 | jq -r .id_mensagem)
docker compose logs borda-goias central-ingestao central | grep "$ID"
```

No Prometheus (http://localhost:9090): `rate(sentinela_mensagens_total[1m])`,
`histogram_quantile(0.95, sum by (le) (rate(sentinela_ingestao_latencia_segundos_bucket[5m])))`
e a página **Alerts** com as 7 regras operacionais.

No Jaeger (http://localhost:16686): serviço `borda-goias`, operação
`borda.sincronizar_lote` → um trace que atravessa a borda, a rota
`POST /api/v1/ingestao/lote` da central, o `ingestao.pipeline` e as consultas
SQL. O `trace_id` de qualquer linha de log da central abre o mesmo trace.

## 10. Teste de carga (RNF-01, RNF-02)

```bash
python plataforma/simuladores/carga/carga.py --taxa 1000 --dispositivos 50 --duracao 60
python plataforma/simuladores/carga/carga.py --taxa 500 --dispositivos 50 --duracao 60
```

O script cadastra os dispositivos pela API, publica por MQTT/TLS e mede pelo
WebSocket do painel. Resultado registrado: sem perda a 1.000 msg/s oferecidas,
vazão sustentada de ~750 msg/s e latências dentro da meta abaixo de ~700 msg/s
(detalhes e limites no [ADR 0009](adr/0009-ingestao-mqtt-separada.md)).

---

## Se algo der errado

| Sintoma | O que fazer |
|---|---|
| Painel não carrega o login | Keycloak ainda subindo: `docker compose logs -f keycloak` até `started` |
| Estações sem dados reais | sem `FIRMS_MAP_KEY` só há Open-Meteo; o mock funciona mesmo assim |
| Alerta não chega à prefeitura | rode o passo 1 antes; confira `GET /api/v1/assinaturas` com o token da prefeitura |
| Link continua cortado | `sh plataforma/simuladores/demo/link.sh on tocantins` (e `goias`) |
| Mensagens somem depois de mexer em réplicas da ingestão | sessão persistente órfã no broker: ver o comentário do serviço `central-ingestao` no `docker-compose.yml` |

# Testes manuais

Checklist para validar a plataforma à mão antes da entrega. Cada item tem o que
fazer e o resultado esperado; marque `[x]` quando passar. Os comandos rodam na
raiz do repositório; a senha de todos os usuários de demonstração é `sentinela`.

Preparação:

```bash
docker compose down -v && cp -n .env.example .env && docker compose up -d --build
pip install -r plataforma/requirements.txt
tk() { curl -s localhost:8080/realms/sentinela/protocol/openid-connect/token \
  -d grant_type=password -d client_id=cli -d username=$1 -d password=sentinela | jq -r .access_token; }
```

## 1. Subida

| ✓ | Teste | Resultado esperado |
|---|---|---|
| [ ] | `docker compose ps` | 19 serviços `running` (o `pki` sai depois de gerar os certificados) |
| [ ] | abrir http://localhost:8000/docs | Swagger com as rotas `/api/v1/...` |
| [ ] | abrir http://localhost:9090/targets | `central`, `central-ingestao` e as duas bordas `UP` |
| [ ] | `curl localhost:8000/saude` | `{"status":"ok"}` |

## 2. Painel web e login

| ✓ | Teste | Resultado esperado |
|---|---|---|
| [ ] | abrir http://localhost:3000 | redireciona para o login do Keycloak |
| [ ] | entrar como `gestor.brigada` | mapa com as 9 zonas, indicador **TEMPO REAL conectado**, abas Painel, Alarmes, Eventos, Bordas e Cadastro |
| [ ] | esperar alguns segundos no painel | leituras das estações mudam sem recarregar a página |
| [ ] | aba **Bordas** | `borda-tocantins` e `borda-goias` com fila pendente e "sem sincronizar há" poucos segundos |
| [ ] | aba **Eventos** | logs por local (comandos, respostas, estados) |
| [ ] | sair e entrar como `leitor.brigada` | vê o painel, **sem** a aba Cadastro e sem botões de comando |
| [ ] | sair e entrar como `admin` | o Keycloak exige configurar o TOTP (segundo fator) |
| [ ] | abrir o painel no celular (ou modo responsivo do navegador) | layout se ajusta, sem rolagem horizontal |

## 3. Cenário urbano criado por configuração

| ✓ | Teste | Resultado esperado |
|---|---|---|
| [ ] | `python plataforma/simuladores/demo/cadastrar_alerta_publico.py` | cria domínio `alerta_publico`, perfil, 8 terminais e 4 assinaturas (rodar de novo mostra "já existia") |
| [ ] | entrar como `gestor.anapolis` | visão da prefeitura: terminais da Defesa Civil e dos bombeiros online, sem nenhum dado da brigada |

## 4. Incêndio rural → alerta na prefeitura

| ✓ | Teste | Resultado esperado |
|---|---|---|
| [ ] | `python plataforma/simuladores/mock_incendio.py on pirenopolis` | — |
| [ ] | `docker compose logs --since 1m borda-goias \| grep 'ALARME LOCAL'` | `ALARME LOCAL CRITICO em pirenopolis (risco-critico) → acionar_brigada` |
| [ ] | `docker compose logs --since 1m atuadores-goias` | banner `ALERTA CRITICO - BRIGADA ACIONADA` |
| [ ] | painel da brigada | Pirenópolis em vermelho (crítico) e alarme novo na aba Alarmes, sem recarregar |
| [ ] | detalhe do alarme | ações executadas (notificação SMS mascarada `***0001`), comando com a resposta do atuador e entregas às prefeituras |
| [ ] | painel de `gestor.anapolis` (em ~20 s) | alerta crítico "em Pirenópolis (GO) a 52 km", estado **reconhecido** pelo terminal |
| [ ] | `python plataforma/simuladores/mock_incendio.py off` | alarme da brigada **resolvido** sozinho; o da prefeitura também; `docker compose logs terminais` mostra o encerramento |

## 5. Queda do link da fazenda

| ✓ | Teste | Resultado esperado |
|---|---|---|
| [ ] | `sh plataforma/simuladores/demo/link.sh off tocantins` | `✂ … desconectado da central` |
| [ ] | `python plataforma/simuladores/mock_incendio.py on lagoa-da-confusao` | a borda de Tocantins continua acionando o atuador local (logs `ALARME LOCAL`) |
| [ ] | `curl -s localhost:9101/metrics \| grep -E '^borda_(fila_pendente\|link_central_online)'` | `borda_link_central_online 0` e a fila crescendo |
| [ ] | aba **Bordas** e http://localhost:9090/alerts | "sem sincronizar" aumentando; alerta `BordaSemSincronizar` depois de ~1 min |
| [ ] | http://localhost:8081 (senha `BORDA_PAINEL_SENHA` do `.env`) | painel local mostra **SEM LINK**, fila pendente subindo e o alarme local de Lagoa da Confusão |
| [ ] | `sh plataforma/simuladores/demo/link.sh on tocantins` | fila volta a 0 em segundos |
| [ ] | aba Alarmes (filtro Lagoa da Confusão) | alarme crítico com **origem borda** e o horário de quando o link estava fora |
| [ ] | `python plataforma/simuladores/mock_incendio.py off` | alarme resolvido |

## 6. Zona sem borda

| ✓ | Teste | Resultado esperado |
|---|---|---|
| [ ] | `python plataforma/simuladores/mock_incendio.py on mirador` | alarme crítico aberto pela central e `BRIGADA ACIONADA` em `docker compose logs atuadores-diretos` |
| [ ] | `python plataforma/simuladores/mock_incendio.py off` | alarme resolvido |

## 7. Comandos, regras e cadastro pelo painel

| ✓ | Teste | Resultado esperado |
|---|---|---|
| [ ] | como `operador.brigada`, enviar comando a um atuador pelo painel | comando aparece como **confirmado** com a resposta |
| [ ] | como `operador.brigada`, tentar alterar uma regra no Swagger (`PATCH /api/v1/regras/risco-alto`) | `403` |
| [ ] | como `gestor.brigada`, criar uma regra na aba Cadastro, ex.: `{"todas": [{"grandeza": "temperatura", "op": ">", "valor": 38}], "area": {"lat": -10.55, "lon": -46.42, "raio_km": 30}}` | `201`; a regra aparece na lista |
| [ ] | como `integrador.brigada`, cadastrar um dispositivo na aba Cadastro | credencial mostrada **uma única vez** |

## 8. Integração (kit do integrador)

| ✓ | Teste | Resultado esperado |
|---|---|---|
| [ ] | seguir [integrador/README.md](integrador/README.md) §3.2 para criar `sensor-teste-01` | resposta com a credencial |
| [ ] | rodar um exemplo (Python, TypeScript ou Go) | `MQTT conectado`, 3 publicações, `HTTP: 202 … aceita` |
| [ ] | enquanto ele espera, enviar comando como operador | `comando recebido` no exemplo; comando **confirmado** na API |
| [ ] | `GET /api/v1/dispositivos/sensor-teste-01/estado` | últimas leituras do sensor |
| [ ] | revogar (`POST /api/v1/dispositivos/sensor-teste-01/revogar`) e rodar o exemplo de novo | `conexão recusada pelo broker (rc=5 …)` |

## 9. Segurança e isolamento

| ✓ | Teste | Resultado esperado |
|---|---|---|
| [ ] | `curl -o /dev/null -w '%{http_code}' localhost:8000/api/v1/dispositivos/est-mateiros-01 -H "Authorization: Bearer $(tk gestor.anapolis)"` | `404` (a prefeitura não enxerga a brigada) |
| [ ] | `curl -o /dev/null -w '%{http_code}' localhost:8000/api/v1/alarmes` (sem token) | `401` |
| [ ] | publicar sem TLS: `mosquitto_pub -h localhost -p 8883 -t x -m y` (se tiver o cliente) | conexão recusada |
| [ ] | `GET /api/v1/regras` como leitor | destinos de notificação mascarados |

## 10. Dados e API

| ✓ | Teste | Resultado esperado |
|---|---|---|
| [ ] | `GET /api/v1/telemetria?site=mateiros&grandeza=fumaca&tamanho=5` (Swagger) | 5 pontos, com `dispositivo`, `site` e paginação |
| [ ] | o mesmo com `agregacao=1h` | média, mínimo, máximo e amostras por hora |
| [ ] | `POST /api/v1/ingestao` com uma mensagem sem `timestamp` | `400` com a lista de erros; aparece em `GET /api/v1/dlq` (admin) |
| [ ] | `GET /api/v1/auditoria` como gestor | as alterações feitas nos itens 7 e 8 com autor e valor anterior |
| [ ] | abrir http://localhost:8000/contratos/CHANGELOG.md | histórico de versões dos contratos |

## 11. Observabilidade

| ✓ | Teste | Resultado esperado |
|---|---|---|
| [ ] | `docker compose logs --no-log-prefix borda-goias \| grep 'ALARME LOCAL' \| tail -1 \| jq -r .id_mensagem`, depois `grep` desse id em `docker compose logs borda-goias central` | o mesmo `id_mensagem` na borda e na central |
| [ ] | Prometheus: `rate(sentinela_mensagens_total[1m])` | vazão por canal (`mqtt`, `lote`) |
| [ ] | Prometheus → Alerts | 7 regras operacionais listadas |
| [ ] | Jaeger (http://localhost:16686), serviço `borda-goias`, operação `borda.sincronizar_lote` | trace com spans da borda **e** da central (`ingestao.pipeline`, `SELECT`/`INSERT`) |

## 12. Carga (opcional, ~2 min)

| ✓ | Teste | Resultado esperado |
|---|---|---|
| [ ] | `python plataforma/simuladores/carga/carga.py --taxa 500 --duracao 30` | `aceitas pela central` ≈ 100% e p95 dentro das metas (2 s / 3 s) |
| [ ] | `python plataforma/simuladores/carga/carga.py --taxa 1000 --duracao 30` | nenhuma perda; p95 acima da meta (limite conhecido, ver ADR 0009) |

## 13. Testes automatizados

| ✓ | Teste | Resultado esperado |
|---|---|---|
| [ ] | `cd plataforma && pip install -r requirements-dev.txt -e . && pytest --cov=nucleo` | 209 testes passando (4 pulados), cobertura do `nucleo` ≥ 70% (96%) |

Encontrou algo diferente do esperado? Anote o passo, o comando e a saída.

# Matriz de rastreabilidade

Cada requisito do *Documento de Requisitos — Plataforma IoT Integrada v2.0*,
onde foi atendido e a evidência (teste automatizado em `plataforma/tests/` ou
passo do [roteiro de demonstração](demonstracao.md)).

Escopo combinado com a equipe: todos os **Essenciais** e os **Importantes** que
já tinham base no projeto. Os demais estão marcados como fora, com o motivo.

Legenda: ✅ atendido · 🟡 parcial · ⬜ fora do escopo desta entrega

## Resumo

| Prioridade | Total | ✅ | 🟡 | ⬜ |
|---|---|---|---|---|
| Essencial | 33 | 33 | 0 | 0 |
| Importante | 30 | 13 | 9 | 8 |
| Desejável | 6 | 1 | 0 | 5 |
| Não funcionais | 18 | 7 | 9 | 2 |

## 5.1 Cenários e catálogo

| Req. | Prior. | Status | Onde | Evidência |
|---|---|---|---|---|
| RF-CEN-01 domínios configuráveis | Essencial | ✅ | `POST /api/v1/catalogo/dominios`; tabela `dominio` | `test_dominio_novo_so_por_configuracao`; demonstração passo 1 |
| RF-CEN-02 perfis (grandezas, unidades, faixa, intervalo) | Essencial | ✅ | `perfil_dispositivo`, `perfil_grandeza`; `nucleo/ingestao.py` (suspeita) | `test_medida_fora_da_faixa_e_marcada_suspeita` |
| RF-CEN-03 hierarquia inquilino → local → área → dispositivo | Essencial | ✅ | `site`, `area`, `dispositivo` | `test_mesma_hierarquia_serve_cidade_e_campo` |
| RF-CEN-04 geometrias GeoJSON e consultas espaciais | Importante | 🟡 | ponto + raio em sites, áreas, assinaturas e regras (`nucleo/geo.py`); consulta por área | `test_series_por_area_e_intervalo`, `test_dentro_do_raio`. Falta: polígonos e linhas (PostGIS) |
| RF-CEN-05 modelos de cenário | Desejável | ⬜ | — | seed cria os dois cenários, mas não há modelo aplicável a um inquilino novo |
| RF-CEN-06 Smart Data Models | Importante | ⬜ | — | sem base no projeto |

## 5.2 Conectividade e ingestão

| Req. | Prior. | Status | Onde | Evidência |
|---|---|---|---|---|
| RF-ING-01 MQTT 3.1.1/5.0 sobre TLS, persistido em até 2 s | Essencial | ✅ | Mosquitto TLS 1.2+; `central-ingestao` | `test_telemetria_no_topico_canonico_e_republicada_enriquecida`; carga: p95 < 2 s abaixo de ~700 msg/s |
| RF-ING-02 HTTPS `POST /ingestao` (202/400) | Essencial | ✅ | `central/api/ingestao.py` | `test_ingestao_http_valida_202_invalida_400_com_erros` (TLS do HTTP: ver RNF-11) |
| RF-ING-03 lote com resultado por mensagem | Essencial | ✅ | `POST /ingestao/lote` | `test_lote_de_5000_com_3_invalidas_grava_4997` |
| RF-ING-04 dados atrasados pelo horário da medição | Essencial | ✅ | hypertable por `tempo`; `ATRASO_MAX` no motor | `test_dado_atrasado_entra_no_lugar_certo_sem_virar_ultimo_estado`, `test_dado_atrasado_nao_dispara_alarme_como_se_fosse_atual` |
| RF-ING-05 LoRaWAN | Importante | ⬜ | — | sem hardware nem servidor de rede |
| RF-ING-06 JSON Schema por versão, inválida → DLQ | Essencial | ✅ | `nucleo/validacao.py`; tabela `dlq` | `test_invalida_vai_para_a_fila_com_motivo`, `test_exemplos_publicados_sao_validos` |
| RF-ING-07 JSON e CBOR, lotes compactados | Importante | ⬜ | — | só JSON |
| RF-ING-08 descartar duplicatas pelo `id_mensagem` | Essencial | ✅ | PK `mensagem.id_mensagem` | `test_reenvio_e_armazenado_uma_unica_vez`, `test_duplicata_dentro_do_mesmo_lote` |
| RF-ING-09 CoAP/DTLS | Desejável | ⬜ | — | — |
| RF-ING-10 limite de taxa | Importante | ⬜ | — | não implementado |

## 5.3 Borda e conectividade limitada

| Req. | Prior. | Status | Onde | Evidência |
|---|---|---|---|---|
| RF-EDG-01 nó de borda com ingestão local (MQTT e HTTP) e fila persistente | Essencial | ✅ | `borda/` + Mosquitto local + `borda/fila.py`; HTTP local `POST :8081/api/v1/ingestao` (`borda/painel_local.py`), credencial do dispositivo conferida contra as senhas/ACL locais | `test_desconexao_nao_perde_nem_duplica` (72 h), `test_fila_sobrevive_a_reinicio`, `test_ingestao_http_local_entra_na_mesma_fila_que_o_mqtt`, `test_ingestao_http_local_recusa` |
| RF-EDG-02 sincronizar em lotes sem perdas nem duplicatas | Essencial | ✅ | `borda/sincronizador.py` | `test_resposta_perdida_reenvia_e_a_central_deduplica`; demonstração passo 5 |
| RF-EDG-03 regras locais sem a central | Importante | ✅ | `borda/decisao_local.py` (motor do `nucleo`) | `test_risco_critico_aciona_a_brigada_localmente`; demonstração passo 4 |
| RF-EDG-04 receber regras e configurações | Importante | ✅ | `GET /bordas/{slug}/regras` e `/acesso` | `test_borda_baixa_e_guarda_as_regras_da_central`, `test_sem_link_as_regras_ficam_como_estao` |
| RF-EDG-05 alarmes antes da telemetria | Importante | ✅ | prioridade na fila | `test_alarme_sai_antes_da_telemetria_atrasada` |
| RF-EDG-06 gestão remota (estado, versão, fila) | Importante | 🟡 | `PUT /bordas/{slug}/estado`; tela **Bordas**; métricas | `test_borda_informa_estado_e_painel_mostra_tempo_sem_sincronizar`. Falta: espaço livre em disco |
| RF-EDG-07 painel local da borda | Desejável | ✅ | `GET :8081/` (HTML) e `/estado` (JSON): últimas leituras, alarmes locais, fila, link e regras; senha local `BORDA_PAINEL_SENHA` | `test_painel_local_mostra_leituras_alarmes_fila_e_link`, `test_painel_local_fica_desligado_sem_senha` |

## 5.4 Gestão de dispositivos

| Req. | Prior. | Status | Onde | Evidência |
|---|---|---|---|---|
| RF-DIS-01 cadastro com perfil, domínio, local, área, fabricante, firmware | Essencial | ✅ | `POST /dispositivos`; tela **Cadastro** | `test_cadastro_duplicado_e_referencia_invalida`, `test_integrador_cadastra_dispositivo_mas_nao_regra` |
| RF-DIS-02 credencial única e revogável (central e borda) | Essencial | ✅ | `central/acesso_mqtt.py`, `nucleo/credenciais.py` | `test_credencial_emitida_uma_vez_renovada_e_revogada`, `test_revogado_sai_do_arquivo_do_broker`, `test_acesso_do_broker_da_borda` |
| RF-DIS-03 provisionamento em lote (CSV) | Importante | ✅ | `POST /dispositivos/lote` (corpo `text/csv`, relatório por linha, credenciais devolvidas uma vez, ACL regerada uma vez por lote) | `test_cadastro_em_lote_por_csv_com_relatorio_por_linha` (1.000 linhas + 3 inválidas, ~7 s), `test_lote_com_cabecalho_errado_e_recusado_inteiro` |
| RF-DIS-04 gêmeo (desejado, relatado, diferença) | Importante | ✅ | tabela `gemeo`; `/dispositivos/{slug}/gemeo` | `test_gemeo_desejado_relatado_e_diferenca` |
| RF-DIS-05 comandos com resposta associada | Importante | ✅ | `POST /dispositivos/{slug}/comandos`; tabela `comando` | `test_comando_pelo_painel_e_resposta`, `test_resposta_do_atuador_confirma_o_comando` |
| RF-DIS-06 online/offline pelo intervalo do perfil | Essencial | ✅ | `intervalo_esperado_s` | `test_online_offline_respeita_o_intervalo_do_perfil` |
| RF-DIS-07 grupos e tags | Importante | ✅ | coluna `dispositivo.tags` (índice GIN); `GET /dispositivos?tag=` (todas as tags pedidas); grupo = tag `grupo:xxx` | `test_tags_e_grupos_filtram_a_frota` |
| RF-DIS-08 OTA em ondas | Desejável | ⬜ | — | — |

## 5.5 Integração

| Req. | Prior. | Status | Onde | Evidência |
|---|---|---|---|---|
| RF-INT-01 contrato versionado (JSON Schema, AsyncAPI) por URL, com histórico | Essencial | ✅ | `/contratos/…`; `contratos/CHANGELOG.md` | `test_contratos_publicados_por_url`, `test_asyncapi_e_yaml_valido_com_os_quatro_canais` |
| RF-INT-02 três linguagens, só bibliotecas genéricas | Essencial | ✅ | Python (estações, atuadores), Go (terminal), TypeScript (painel); exemplos do [kit](integrador/) nas três | exemplos executados contra a plataforma; demonstração passo 7 |
| RF-INT-03 adaptadores plugáveis em contêiner | Essencial | ✅ | `conector-firms` (contêiner próprio, credencial de dispositivo) | [kit §5](integrador/README.md#5-adaptadores-e-conectores-outros-protocolos-ou-sistemas); demonstração passo 7 |
| RF-INT-04 conectores REST (coleta) e webhooks (envio) | Importante | ✅ | conector NASA FIRMS; ação `webhook` | `test_webhook_com_novas_tentativas_e_espera_crescente` |
| RF-INT-05 NGSI-LD | Importante | ⬜ | — | sem base no projeto |
| RF-INT-06 sandbox e validador | Importante | ⬜ | — | contrato e exemplos publicados, sem ambiente isolado |
| RF-INT-07 kit do integrador | Importante | ✅ | [`docs/integrador/`](integrador/) | guia + coleção + 3 exemplos executados |
| RF-INT-08 mapeamento sem código | Desejável | ⬜ | — | — |

## 5.6 Processamento e regras

| Req. | Prior. | Status | Onde | Evidência |
|---|---|---|---|---|
| RF-PRO-01 normalizar unidades e enriquecer | Essencial | ✅ | `nucleo/ingestao.py`, `nucleo/unidades.py` | `test_unidade_e_normalizada_para_a_do_perfil`, `test_mensagem_valida_e_aceita_enriquecida_e_publicada` |
| RF-PRO-02 limite, ausência, janela e área geográfica (≤ 3 s) | Essencial | ✅ | `nucleo/regras.py`, `central/motor.py` | `test_regra_com_janela_de_tempo`, `test_regra_de_ausencia_de_dados`, `test_regra_por_area_cadastrada_so_vale_dentro_dela`, `test_regra_por_circulo_usa_a_posicao_da_mensagem_ou_a_do_cadastro` |
| RF-PRO-03 ações: alarme, notificar, comando, webhook, publicar | Essencial | ✅ | `central/acoes.py` (Strategy) | `test_zona_direta_risco_critico_abre_alarme_comanda_atuador_e_notifica`, `test_webhook_…`, `test_acao_publicar_em_topico` |
| RF-PRO-04 agregações por hora e dia | Importante | 🟡 | agregado contínuo `telemetria_1h` | `test_agregacao_horaria_bate_com_os_dados_brutos`. Falta: agregado diário |
| RF-PRO-05 ciclo de vida do alarme, horário da borda | Essencial | ✅ | tabela `alarme` | `test_ciclo_ativo_reconhecido_resolvido`, `test_alarme_gerado_na_borda_offline_mantem_o_horario_em_que_ocorreu` |
| RF-PRO-06 funções externas | Desejável | ⬜ | — | — |

## 5.7 Dados

| Req. | Prior. | Status | Onde | Evidência |
|---|---|---|---|---|
| RF-DAD-01 séries por dispositivo, grandeza, área e intervalo (24 h ≤ 1 s) | Essencial | ✅ | `GET /api/v1/telemetria`, `/dispositivos/{slug}/telemetria` | `test_series_por_area_e_intervalo`, `test_consulta_24h_de_um_dispositivo_responde_rapido` |
| RF-DAD-02 último valor sem consultar o histórico | Essencial | ✅ | `ultimo_estado` | `test_dado_atrasado_entra_no_lugar_certo_sem_virar_ultimo_estado` |
| RF-DAD-03 retenção e downsampling | Importante | 🟡 | compressão após 7 dias; `telemetria_1h` | falta apagar os brutos vencidos (política de retenção) |
| RF-DAD-04 exportação CSV/JSON | Importante | ⬜ | — | API devolve JSON, mas não há exportação |
| RF-DAD-05 pseudonimizar dados pessoais | Essencial | ✅ | `acoes.mascarar`; `contato_resgate` não exposto | `test_destinos_de_notificacao_mascarados_para_quem_nao_e_gestor` |
| RF-DAD-06 auditoria (autor, data, valor anterior) | Importante | ✅ | tabela `auditoria` | `test_alteracao_de_dispositivo_fica_na_auditoria` |

## 5.8 Aplicação e serviços

| Req. | Prior. | Status | Onde | Evidência |
|---|---|---|---|---|
| RF-APP-01 API REST versionada em OpenAPI 3, testada no pipeline | Essencial | ✅ | `/docs`, `/openapi.json`; CI | `test_api.py` (rotas do OpenAPI) |
| RF-APP-02 tempo real (≤ 2 s) | Importante | ✅ | WebSocket `/api/v1/tempo-real` | `test_tempo_real_entrega_telemetria_e_alarmes_do_proprio_inquilino`, `test_tempo_real_nao_vaza_entre_inquilinos` |
| RF-APP-03 webhooks de saída com novas tentativas | Importante | ✅ | `AcaoWebhook` + `ExecutorAcoes` | `test_webhook_com_novas_tentativas_e_espera_crescente` |
| RF-APP-04 painéis configuráveis | Importante | 🟡 | painel com mapa, alarmes, eventos, bordas e cadastro | os painéis são fixos (não há montagem sem programar) |
| RF-APP-05 e-mail, mensageiro e SMS | Importante | 🟡 | ação `notificar` (e-mail, SMS, WhatsApp) | **simulada**: registra no log, sem gateway real |
| RF-APP-06 web responsiva, celular, conexão lenta | Importante | 🟡 | front React responsivo | verificado no celular (Playwright); carga em 1 Mbit/s não medida |
| RF-APP-07 paginação, filtros e ordenação padronizados | Essencial | ✅ | `central/api/comum.py` | `test_listagens_aceitam_a_mesma_paginacao`, `test_ordenacao_so_aceita_campos_permitidos` |

## 5.9 Segurança e acesso

| Req. | Prior. | Status | Onde | Evidência |
|---|---|---|---|---|
| RF-SEG-01 OIDC com 2º fator para admin | Essencial | ✅ | Keycloak, `CONFIGURE_TOTP` no admin | realm versionado; demonstração passo 8 |
| RF-SEG-02 papéis admin, gestor, operador, integrador, leitor | Essencial | ✅ | `central/api/seguranca.py` | `test_operador_nao_altera_regras_nem_dispositivos`, `test_leitor_consulta_mas_nao_altera` |
| RF-SEG-03 isolamento por inquilino | Essencial | ✅ | filtro por inquilino em toda consulta | `test_usuario_de_um_inquilino_nao_ve_dados_de_outro`, `test_painel_da_prefeitura_so_mostra_o_que_e_dela` |
| RF-SEG-04 dispositivo restrito aos próprios tópicos | Essencial | ✅ | ACL gerada (central e borda) | `test_acesso_do_broker_central`, `test_acesso_do_broker_da_borda`; demonstração passo 8 |
| RF-SEG-05 borda com credencial própria e fila cifrada | Essencial | ✅ | `CREDENCIAL_BORDA_*`; Fernet | `test_disco_nao_revela_os_dados`, `test_rotas_da_borda_exigem_a_credencial_dela` |
| RF-SEG-06 chaves de API com escopo e validade | Importante | ⬜ | — | integrações usam credencial de dispositivo ou token OIDC |

## 5.10 Observabilidade

| Req. | Prior. | Status | Onde | Evidência |
|---|---|---|---|---|
| RF-OBS-01 métricas Prometheus por módulo | Essencial | ✅ | `central/metricas.py`; métricas das bordas | `test_metricas_prometheus`; Prometheus em `:9090` |
| RF-OBS-02 logs JSON com correlação da borda à central | Essencial | ✅ | `nucleo/logs.py` (`id_mensagem`) | `test_logs_json_com_correlacao`; demonstração passo 9 |
| RF-OBS-03 OpenTelemetry | Importante | ✅ | `nucleo/rastreamento.py`; Jaeger em `:16686`; `traceparent` no lote borda→central; `trace_id` nos logs ([ADR 0012](adr/0012-opentelemetry-e-jaeger.md)) | `test_lote_da_borda_continua_o_trace_na_central`, `test_borda_envia_traceparent_no_lote`, `test_log_dentro_de_um_span_leva_o_trace_id`. MQTT 3.1.1 sem cabeçalho: o trace começa na ingestão |
| RF-OBS-04 saúde por integração e por borda | Importante | 🟡 | tela **Bordas**; alvos do Prometheus | por borda sim; por integração só via Prometheus |
| RF-OBS-05 alertas operacionais | Importante | 🟡 | `infra/prometheus/alertas.yml` (7 regras) | visíveis em `:9090/alerts`; sem envio ao administrador (Alertmanager) |

## 6. Requisitos não funcionais

| Req. | Status | Como / evidência |
|---|---|---|
| RNF-01 1.000 msg/s | 🟡 | `simuladores/carga/carga.py`: sem perda a 1.000 msg/s oferecidas, mas vazão sustentada de ~750 msg/s numa máquina; ingestão separada e escalável por `$share` ([ADR 0009](adr/0009-ingestao-mqtt-separada.md)) |
| RNF-02 p95 ≤ 2 s (persistência) e ≤ 3 s (regra) | 🟡 | atendido abaixo de ~700 msg/s; a 1.000 msg/s, p95 de ~6 s e ~8 s. Regra de borda: decisão local, independente do link |
| RNF-03 disponibilidade 99,5% | ⬜ | não se aplica a uma PoC local |
| RNF-04 72 h sem link sem perda | ✅ | `test_desconexao_nao_perde_nem_duplica` (432 leituras espalhadas por 72 h) |
| RNF-05 links de 64 kbit/s, cargas compactadas | 🟡 | pré-filtro de envio nas estações; lote sem compressão |
| RNF-06 borda em ARM 4 núcleos / 4 GB | 🟡 | imagem `python:3.12-slim` é multi-arquitetura; não medido em Raspberry Pi |
| RNF-07 queda de energia não corrompe | ✅ | SQLite WAL transacional; `test_fila_sobrevive_a_reinicio` |
| RNF-08 domínio novo só por configuração | ✅ | `alerta_publico` criado pela API (demonstração passo 1); `test_dominio_novo_so_por_configuracao` |
| RNF-09 serviços centrais escaláveis | 🟡 | ingestão escala por réplicas; a API guarda estado em memória (WebSocket, fila do motor) |
| RNF-10 nenhuma mensagem confirmada perdida | 🟡 | borda: só confirma após gravar; central: sessão MQTT persistente e deduplicação. Limite: a ingestão MQTT confirma ao broker antes de gravar o micro-lote (fila em memória, frações de segundo) |
| RNF-11 TLS em toda interface externa; segredos em repouso | 🟡 | MQTT só TLS 1.2+; fila da borda cifrada; credenciais só em hash. Falta: TLS no HTTP (proxy reverso) e cifragem do banco central |
| RNF-12 LGPD | 🟡 | minimização (alarmes entregues sem dados internos), mascaramento, auditoria; sem registro formal das operações de tratamento |
| RNF-13 contratos abertos | ✅ | OpenAPI, AsyncAPI, JSON Schema, GeoJSON |
| RNF-14 contêineres OCI | ✅ | Docker Compose; Kubernetes não incluído |
| RNF-15 cobertura ≥ 70% no núcleo; análise estática | ✅ | 96% em `nucleo/`; ruff e testes no GitHub Actions |
| RNF-16 português, responsiva, WCAG 2.1 AA | 🟡 | português e responsiva; WCAG não auditado |
| RNF-17 APIs versionadas | ✅ | `/api/v1`; política em `contratos/CHANGELOG.md` |
| RNF-18 backup diário, RPO/RTO | ⬜ | não implementado (o volume do Postgres não tem rotina de backup) |

## Critérios de aceite do MVP (§9.3)

| Critério | Status | Evidência |
|---|---|---|
| Projeto urbano e rural integrados; o rural com borda | ✅ | assinaturas de alerta ([ADR 0010](adr/0010-assinaturas-entre-inquilinos.md)); demonstração passos 1–3 |
| Desconexão de 72 h sem perda nem duplicação | ✅ | `test_desconexao_nao_perde_nem_duplica`; demonstração passos 4–5 (queda de minutos) |
| Domínio novo só por configuração | ✅ | demonstração passo 1 |
| Integração em três linguagens | ✅ | Python, Go e TypeScript; [kit do integrador](integrador/) |
| Teste de carga comprovando RNF-01 e RNF-02 | 🟡 | demonstração passo 10; resultados acima |

# Histórico dos contratos

Versionamento semântico (§7.3 do documento de requisitos): quebra de contrato
só numa versão MAJOR, e a versão anterior continua aceita por 12 meses
(RNF-17). Cada versão do schema é um arquivo próprio
(`mensagem-canonica.v{MAJOR}.schema.json`), publicado em
`/contratos/{arquivo}` pela central (este histórico em `/contratos/CHANGELOG.md`). O campo `versao` da mensagem diz contra
qual schema ela é validada.

| Contrato | Versão | Data | Mudanças |
|---|---|---|---|
| Mensagem canônica (`mensagem-canonica.v1.schema.json`) | 1.0 | 2026-10-21 | Versão inicial: `versao`, `id_mensagem`, `dispositivo`, `tipo` (telemetria, evento, estado, comando), `timestamp` UTC da medição, `via`, `fonte`, `local` (site, área, ponto GeoJSON), `medidas` (grandeza, valor, unidade UCUM; obrigatórias em telemetria), `dados` (obrigatório em evento, estado e comando; comando exige `dados.acao`). |
| Interface MQTT (`asyncapi.yaml`) | 1.0.0 | 2026-10-21 | Versão inicial: canais `{inquilino}/{site}/{dominio}/{dispositivo}/{telemetria\|evento\|estado\|comando}`, MQTT 3.1.1 (5.0 aceito) sobre TLS, credencial por dispositivo. Servidores corrigidos em 2026-10-22 para as portas TLS (o listener WebSocket 9001 foi removido). |
| API REST (`/openapi.json`) | 1.0.0 | 2026-10-22 | Prefixo `/api/v1`. Inclui `GET /api/v1/telemetria` (séries por área/site/intervalo) e a chave opcional `area` em `condicao` das regras, ambas compatíveis com o que já existia. |

## Como evoluir

- Campo novo **opcional** ou valor novo de enum: versão MINOR (`1.1`), no mesmo
  arquivo. Consumidores antigos continuam válidos.
- Remover campo, tornar obrigatório ou mudar significado: versão MAJOR
  (`mensagem-canonica.v2.schema.json`, `"versao": "2.0"`). A ingestão passa a
  escolher o schema pelo campo `versao`, e a v1 segue aceita por 12 meses.
- Toda mudança entra aqui, com os exemplos de `exemplos/` atualizados e o teste
  de contrato (`plataforma/tests/test_contrato.py`) passando.

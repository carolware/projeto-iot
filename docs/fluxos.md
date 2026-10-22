# Fluxos

## 1. Ponta a ponta: incêndio na fazenda → alerta na prefeitura

Incêndio em Pirenópolis (GO), atrás da borda de Goiás. A Prefeitura de Anápolis
assinou "incêndios de severidade alta ou crítica a até 80 km da cidade".

```mermaid
sequenceDiagram
  autonumber
  participant E as Estação<br/>est-pirenopolis-01
  participant BM as Mosquitto<br/>borda-goias
  participant B as borda-goias
  participant A as Atuador<br/>atuador-pirenopolis-01
  participant C as central<br/>(API + motor)
  participant DB as TimescaleDB
  participant MQ as Mosquitto central
  participant T as Terminal Go<br/>terminal-dc-anapolis
  actor Op as Operador da<br/>Defesa Civil

  E->>BM: telemetria (fumaça 93, umidade 8)
  BM->>B: entrega
  B->>B: valida, cifra e enfileira
  B->>B: regra local "risco-critico" atende
  B->>BM: comando acionar_brigada (sem esperar a central)
  BM->>A: comando
  A-->>BM: evento "executado" (comando_id)
  B->>B: enfileira o evento alarme_local (prioridade 10)
  B->>C: POST /ingestao/lote (alarme primeiro, depois telemetria)
  C->>DB: grava (deduplica pelo id_mensagem)
  C-->>B: resultado por mensagem → sai da fila
  C->>C: motor abre o alarme crítico (origem borda,<br/>horário em que ocorreu)
  C->>C: ações restantes: notificar SMS (simulado),<br/>o comando já foi dado pela borda
  C->>C: assinatura de pref-anapolis: dentro de 80 km e ≥ alto
  C->>DB: alarme entregue no inquilino pref-anapolis
  C->>MQ: comando exibir_alerta
  MQ->>T: comando
  T-->>MQ: evento "recebido" (comando_id)
  MQ-->>C: via central-ingestao e barramento
  Op->>T: reconhece (simulado: AUTO_RECONHECER_S = 20 s)
  T-->>MQ: evento "reconhecido"
  MQ-->>C: alarme da prefeitura → reconhecido
```

Quando a leitura volta ao normal, a central resolve o alarme da brigada. A
resolução encerra as entregas, e os terminais recebem `encerrar_alerta`.

## 2. Zona sem borda: a central decide

Mirador (MA) e Novo Progresso (PA) não têm nó de borda.

```mermaid
sequenceDiagram
  autonumber
  participant E as est-mirador-01
  participant MQ as Mosquitto central
  participant I as central-ingestao
  participant DB as TimescaleDB
  participant C as central (motor)
  participant P as Painel (WebSocket)
  participant A as atuador-mirador-01
  E->>MQ: telemetria (TLS, credencial própria)
  MQ->>I: assinatura compartilhada
  I->>DB: grava o micro-lote
  I->>MQ: plataforma/telemetria/… (enriquecida)
  MQ->>C: barramento
  C->>P: telemetria em tempo real
  C->>C: regras de escopo "borda" valem aqui<br/>(mensagem sem via): risco-critico atende
  C->>DB: alarme crítico
  C->>P: alarme aberto
  C->>MQ: comando acionar_brigada
  MQ->>A: comando
  A-->>MQ: evento "executado"
  MQ-->>C: comando confirmado
```

## 3. Desconexão e sincronização (store-and-forward)

Critério da §9.3: o link da borda cai por horas e, na volta, nada se perde nem
se duplica.

```mermaid
sequenceDiagram
  autonumber
  participant E as Estações
  participant B as Nó de borda
  participant F as Fila SQLite<br/>(cifrada, WAL)
  participant C as central
  participant DB as TimescaleDB

  Note over B,C: link cortado (link.sh off tocantins)
  loop a cada leitura
    E->>B: telemetria
    B->>F: enfileira (prioridade 0)
  end
  B->>B: regra local crítica → aciona o atuador local
  B->>F: alarme_local (prioridade 10)
  B--xC: tentativa de lote falha → espera e tenta de novo
  Note over B,C: link volta (link.sh on tocantins)
  B->>F: próximos 500, por prioridade e horário da medição
  F-->>B: alarme_local primeiro
  B->>C: POST /ingestao/lote
  C->>DB: grava pelo horário da medição,<br/>ids repetidos → "duplicada"
  C-->>B: resultado por mensagem
  B->>F: marca enviadas (aceita ou duplicada)
  B->>C: próximos lotes, até esvaziar
  C->>C: alarme aparece com o horário em que ocorreu
```

- Só sai da fila o que a central confirmou. Se o link cair no meio de um lote, o
  lote inteiro é reenviado, e a central marca as que já tinha como `duplicada`.
- Os comandos da central para o campo, emitidos com o link fora, ficam na sessão
  persistente da bridge e chegam na reconexão.
- O teste automatizado `tests/test_borda.py` injeta 72 h de leituras com o link
  fora e confere a contagem, a ordem e a ausência de duplicatas.

## 4. Regra alterada na central chega à borda

```mermaid
sequenceDiagram
  autonumber
  actor G as Gestor
  participant C as central
  participant B as Nó de borda
  G->>C: PATCH /regras/risco-alto (escopo borda)
  C->>C: valida e audita (autor, antes e depois)
  loop a cada 30 s, com link
    B->>C: GET /bordas/{slug}/regras
    C-->>B: regras de escopo borda do inquilino
    B->>B: grava no disco e passa a usar
  end
  Note over B: sem link, usa a última versão salva<br/>(ou regras-padrao.json no primeiro boot)
```

## 5. Comando pelo painel e gêmeo

```mermaid
sequenceDiagram
  autonumber
  actor O as Operador (painel)
  participant C as central
  participant MQ as Mosquitto
  participant D as Dispositivo
  O->>C: POST /dispositivos/{slug}/comandos {acao}
  C->>C: registra o comando (estado enviado, origem painel)
  C->>MQ: …/{slug}/comando
  MQ->>D: comando
  D-->>MQ: evento {comando_id, evento: executado}
  MQ-->>C: comando → confirmado (resposta associada)
  O->>C: PUT /dispositivos/{slug}/gemeo/desejado
  C-->>O: diferença = desejado − relatado<br/>(entregue quando o dispositivo voltar)
```

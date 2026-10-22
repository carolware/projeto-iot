# ADR 0004 — Fila SQLite cifrada no nó de borda

## Contexto
O nó de borda precisa operar 72 h sem link sem perder dados (RNF-04, RF-EDG-01),
resistir a queda de energia (RNF-07) e não revelar os dados se o disco for
retirado (RF-SEG-05).

## Decisão
Fila em **SQLite no modo WAL** (`borda/fila.py`), com o payload de cada
mensagem **cifrado com Fernet** (AES-128-CBC + HMAC). A chave vem do ambiente
(`BORDA_CHAVE_*`). A fila tem prioridade: alarme (10) > estado (1) >
telemetria (0).

## Alternativas
- **Persistência do próprio Mosquitto**: não deixa priorizar alarmes nem
  confirmar mensagem a mensagem.
- **Postgres na borda**: pesado para o hardware alvo.
- **Arquivos JSON por mensagem**: sem transação; uma queda de energia corromperia
  a fila.
- **SQLCipher**: cifra o banco inteiro, mas exige build nativo; o Fernet resolve
  com a biblioteca `cryptography`.

## Consequências
- Escrita transacional: a mensagem só é confirmada ao dispositivo depois de
  gravada.
- Metadados (prioridade, horário) ficam em claro para ordenar a fila; só o
  conteúdo é cifrado.
- A chave fica no ambiente do contêiner; em produção iria para um cofre (Vault,
  TPM do equipamento).

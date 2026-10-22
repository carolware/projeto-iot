# ADR 0010 — Cenário urbano por assinaturas de alerta entre inquilinos

## Contexto
O PDF pede um projeto urbano e um rural integrados (§9.3). A ideia original da
equipe separava os sensores que detectam incêndio dos dispositivos que avisam
as prefeituras. Decisão do grupo (opção "B"): o urbano são **terminais de
alerta** nas prefeituras, Defesa Civil e bombeiros, que só recebem e repassam.

## Decisão
Cada prefeitura é um inquilino (`pref-anapolis` etc.) e cadastra uma
**assinatura**: domínio de interesse, ponto, raio e severidade mínima. Quando um
alarme rural cai dentro da área, a central cria um alarme **no inquilino da
prefeitura** (`origem_alarme_id` aponta para o original) e comanda os
terminais dela; o reconhecimento volta pelo evento do terminal.

## Alternativas
- **Prefeitura ler os dados da brigada**: quebraria o isolamento (RF-SEG-03).
- **Sensores urbanos de qualidade do ar**: outro domínio, sem ligação com o
  incêndio.
- **Webhook para a prefeitura**: possível como ação, mas não daria o painel
  nem os terminais do inquilino urbano.

## Consequências
- O isolamento é mantido: o alarme entregue não carrega o dispositivo nem os
  dados internos da brigada.
- O domínio `alerta_publico` é criado só pela API no roteiro de demonstração,
  o que prova o RNF-08.
- A resolução na origem encerra as entregas e manda `encerrar_alerta`.

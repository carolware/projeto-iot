# Documentação — Sentinela (2ª entrega)

Ordem de leitura sugerida:

1. [Arquitetura](arquitetura.md): camadas, edge/fog/cloud, implantação, hexagonal, pipeline e mensagem canônica.
2. [Fluxos](fluxos.md): diagramas de sequência dos cenários principais.
3. [Modelo de dados](dados.md): MER e decisões do banco.
4. [MQTT, acesso e segurança](mqtt-e-seguranca.md): tópicos, ACL, credenciais, TLS, papéis e LGPD.
5. [Decisões de arquitetura (ADRs)](adr/).
6. [Kit do integrador](integrador/): guia, coleção de requisições e exemplos em três linguagens.
7. [Matriz de rastreabilidade](rastreabilidade.md): requisito → implementação → evidência.
8. [Roteiro de demonstração](demonstracao.md).
9. [Operação](operacao.md): execução, variáveis, testes, métricas e problemas conhecidos.

Os diagramas estão em Mermaid dentro de cada documento (o GitHub renderiza) e
exportados em PNG em [`diagramas/`](diagramas/) para slides e relatório.
Para regerar um PNG: `docker run --rm -v "$PWD":/data minlag/mermaid-cli:11.4.2 -i /data/diagrama.mmd -o /data/diagrama.png -b white -s 2`.

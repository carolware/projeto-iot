"""
Motor de regras, ações, ciclo de vida dos alarmes e assinaturas entre
inquilinos (RF-PRO-02/03/05, RF-DIS-04/05, RF-APP-03/05, RF-EDG-04).
Cada teste usa zonas/dispositivos próprios para não depender da ordem.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from nucleo import Medida, nova_mensagem
from tests.central.conftest import ADMIN, ANAPOLIS, BORDA_GOIAS, BRIGADA

AGORA = lambda: datetime.now(UTC)  # noqa: E731


def leitura(dispositivo, fumaca=5.0, umidade=60.0, temperatura=28.0, quando=None, via=None):
    return nova_mensagem(
        dispositivo,
        "telemetria",
        medidas=[
            Medida("temperatura", temperatura, "Cel"),
            Medida("umidade", umidade, "%"),
            Medida("fumaca", fumaca, "1"),
        ],
        timestamp=quando or AGORA(),
        via=via,
    )


def alarme_local(
    dispositivo, severidade="critico", regra="risco-critico", quando=None, via="borda-tocantins", comandos=()
):
    return nova_mensagem(
        dispositivo,
        "evento",
        timestamp=quando or AGORA(),
        via=via,
        dados={
            "evento": "alarme_local",
            "regra": regra,
            "severidade": severidade,
            "leituras": {"fumaca": 93, "umidade": 8, "temperatura": 41},
            "comandos": list(comandos),
        },
    )


def enviar(cliente, *mensagens):
    corpo = cliente.post("/api/v1/ingestao/lote", json={"mensagens": list(mensagens)}, headers=ADMIN).json()
    assert corpo["recusadas"] == 0, corpo
    return corpo


def abertos(cliente, dispositivo, cabecalho=BRIGADA):
    return cliente.get(
        "/api/v1/alarmes", params={"dispositivo": dispositivo, "abertos": True}, headers=cabecalho
    ).json()["itens"]


def detalhe(cliente, alarme_id, cabecalho=BRIGADA):
    return cliente.get(f"/api/v1/alarmes/{alarme_id}", headers=cabecalho).json()


# ── Zonas sem borda: a central decide ────────────────────────────────────────


def test_zona_direta_risco_critico_abre_alarme_comanda_atuador_e_notifica(cliente, publicadas):
    """RF-PRO-02/03: limiar → alarme + comando + SMS; o alarme sai no barramento."""
    publicadas.clear()
    enviar(cliente, leitura("est-mirador-01", fumaca=93, umidade=10))

    [alarme] = abertos(cliente, "est-mirador-01")
    assert (alarme["regra"], alarme["severidade"], alarme["estado"]) == ("risco-critico", "critico", "ativo")
    assert alarme["dados"]["leituras"]["fumaca"] == 93 and alarme["dados"]["origem"] == "central"

    [comando] = publicadas.em("brigada-cerrado/mirador/incendio/atuador-mirador-01/comando")
    assert comando["dados"]["acao"] == "acionar_brigada" and comando["dados"]["alarme_id"] == alarme["id"]
    assert any(c["evento"] == "aberto" for c in publicadas.em("plataforma/alarme/brigada-cerrado/mirador/"))

    info = detalhe(cliente, alarme["id"])
    assert {(a["tipo"], a["status"]) for a in info["acoes"]} == {("comando", "sucesso"), ("notificar", "sucesso")}
    assert info["comandos"][0]["estado"] == "enviado"


def test_resposta_do_atuador_confirma_o_comando(cliente):
    """RF-DIS-05: o comando chega ao tópico de comando e a resposta é associada a ele."""
    enviar(cliente, leitura("est-novo-progresso-01", fumaca=95, umidade=5))
    [alarme] = abertos(cliente, "est-novo-progresso-01")
    [comando] = detalhe(cliente, alarme["id"])["comandos"]

    resposta = nova_mensagem(
        "atuador-novo-progresso-01",
        "evento",
        dados={"evento": "executado", "comando_id": comando["id"], "acao": "acionar_brigada"},
    )
    enviar(cliente, resposta)
    [comando] = detalhe(cliente, alarme["id"])["comandos"]
    assert comando["estado"] == "confirmado" and comando["resposta"]["evento"] == "executado"


def test_condicao_que_deixa_de_valer_resolve_o_alarme(cliente, publicadas):
    enviar(cliente, leitura("est-mirador-01", fumaca=93, umidade=10))
    assert abertos(cliente, "est-mirador-01")
    publicadas.clear()
    enviar(cliente, leitura("est-mirador-01", fumaca=5, umidade=70))
    assert abertos(cliente, "est-mirador-01") == []
    resolvido = cliente.get(
        "/api/v1/alarmes",
        params={"dispositivo": "est-mirador-01", "estado": "resolvido", "tamanho": 1},
        headers=BRIGADA,
    ).json()["itens"][0]
    assert resolvido["resolvido_por"].startswith("automatico")
    assert any(c["evento"] == "resolvido" for c in publicadas.em("plataforma/alarme/"))


def test_escalada_encerra_o_alarme_menos_severo(cliente):
    enviar(cliente, leitura("est-novo-progresso-01", fumaca=5, umidade=70))  # limpa estado anterior
    enviar(cliente, leitura("est-novo-progresso-01", fumaca=65, umidade=20))
    assert [a["severidade"] for a in abertos(cliente, "est-novo-progresso-01")] == ["alto"]
    enviar(cliente, leitura("est-novo-progresso-01", fumaca=90, umidade=10))
    assert [a["severidade"] for a in abertos(cliente, "est-novo-progresso-01")] == ["critico"]
    alto = cliente.get(
        "/api/v1/alarmes",
        params={"dispositivo": "est-novo-progresso-01", "severidade": "alto", "estado": "resolvido"},
        headers=BRIGADA,
    ).json()["itens"][0]
    assert alto["resolvido_por"] == "escalado para critico"
    enviar(cliente, leitura("est-novo-progresso-01", fumaca=5, umidade=70))


def test_repeticao_conta_ocorrencias_sem_abrir_outro_alarme(cliente):
    for _ in range(3):
        enviar(cliente, leitura("est-mirador-01", fumaca=93, umidade=10))
    [alarme] = abertos(cliente, "est-mirador-01")
    assert alarme["ocorrencias"] >= 3
    enviar(cliente, leitura("est-mirador-01", fumaca=5, umidade=70))


def test_dado_atrasado_nao_dispara_alarme_como_se_fosse_atual(cliente):
    """RF-ING-04: medição de 48 h atrás entra no histórico e não gera alarme."""
    enviar(cliente, leitura("est-mirador-01", fumaca=99, umidade=3, quando=AGORA() - timedelta(hours=48)))
    assert abertos(cliente, "est-mirador-01") == []


# ── Zonas atrás de borda: a borda decide, a central registra ────────────────


def test_leitura_que_passou_pela_borda_nao_duplica_alarme(cliente):
    enviar(cliente, leitura("est-sandolandia-01", fumaca=93, umidade=10, via="borda-tocantins"))
    assert abertos(cliente, "est-sandolandia-01") == []


def test_alarme_da_borda_vira_alarme_sem_reenviar_comando(cliente, publicadas):
    publicadas.clear()
    cmd = {
        "id": "0b9c4a6e-1d2b-4f7a-8c9e-0b1a2c3d4e5f",
        "dispositivo": "atuador-sandolandia-01",
        "acao": "acionar_brigada",
    }
    enviar(cliente, alarme_local("est-sandolandia-01", comandos=[cmd]))
    [alarme] = abertos(cliente, "est-sandolandia-01")
    info = detalhe(cliente, alarme["id"])
    assert alarme["dados"]["decidido_na_borda"] and alarme["dados"]["via"] == "borda-tocantins"
    assert {a["tipo"] for a in info["acoes"]} == {"notificar"}  # o comando a borda já mandou
    assert publicadas.em("brigada-cerrado/sandolandia/incendio/atuador-sandolandia-01/comando") == []

    # o comando local da borda fica registrado e a resposta do atuador o confirma
    resposta = nova_mensagem(
        "atuador-sandolandia-01",
        "evento",
        via="borda-tocantins",
        dados={"evento": "executado", "comando_id": cmd["id"]},
    )
    enviar(cliente, resposta)
    comandos = cliente.get("/api/v1/dispositivos/atuador-sandolandia-01/comandos", headers=BRIGADA).json()["itens"]
    assert (comandos[0]["origem"], comandos[0]["estado"]) == ("borda", "confirmado")

    # a borda repete o alarme após o cooldown: conta ocorrência, não abre outro
    enviar(cliente, alarme_local("est-sandolandia-01"))
    [alarme] = abertos(cliente, "est-sandolandia-01")
    assert alarme["ocorrencias"] == 2


def test_alarme_gerado_na_borda_offline_mantem_o_horario_em_que_ocorreu(cliente):
    """RF-PRO-05: alarme gerado na borda durante a desconexão aparece com o horário em que ocorreu."""
    quando = (AGORA() - timedelta(hours=6)).replace(microsecond=0)
    enviar(cliente, alarme_local("est-lagoa-da-confusao-01", quando=quando))
    [alarme] = abertos(cliente, "est-lagoa-da-confusao-01")
    assert datetime.fromisoformat(alarme["ocorrido_em"]) == quando


# ── Ciclo de vida pela API ───────────────────────────────────────────────────


def test_ciclo_ativo_reconhecido_resolvido(cliente, banco):
    enviar(cliente, alarme_local("est-formosa-01", via="borda-goias", severidade="alto", regra="risco-alto"))
    [alarme] = abertos(cliente, "est-formosa-01")
    url = f"/api/v1/alarmes/{alarme['id']}"

    reconhecido = cliente.post(f"{url}/reconhecer", json={"comentario": "equipe a caminho"}, headers=BRIGADA).json()
    assert (reconhecido["estado"], reconhecido["reconhecido_por"]) == ("reconhecido", "teste@sentinela")
    assert cliente.post(f"{url}/reconhecer", headers=BRIGADA).status_code == 409

    resolvido = cliente.post(f"{url}/resolver", headers=BRIGADA).json()
    assert resolvido["estado"] == "resolvido" and resolvido["resolvido_em"]
    assert cliente.post(f"{url}/resolver", headers=BRIGADA).status_code == 409
    assert cliente.get(url, headers=ANAPOLIS).status_code == 404  # outro inquilino não vê

    with banco.connect() as conexao:
        acoes = conexao.execute(
            text("select acao from auditoria where entidade = 'alarme' and entidade_id = :i order by id"),
            {"i": alarme["id"]},
        ).scalars()
        assert list(acoes) == ["reconhecer", "resolver"]


# ── Regras configuráveis ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def silo(cliente):
    """UC-02: domínio de armazenagem criado só por configuração, com um silo em Mateiros."""
    cliente.post("/api/v1/catalogo/dominios", json={"slug": "armazenagem", "nome": "Armazenagem"}, headers=BRIGADA)
    cliente.post(
        "/api/v1/catalogo/perfis",
        headers=BRIGADA,
        json={
            "dominio": "armazenagem",
            "slug": "termometria-silo",
            "nome": "Termometria de silo",
            "intervalo_esperado_s": 900,
            "grandezas": [{"grandeza": "temperatura", "unidade": "Cel", "valor_min": -10, "valor_max": 80}],
        },
    )
    for numero in (2, 3):
        cliente.post(
            "/api/v1/dispositivos",
            headers=BRIGADA,
            json={
                "slug": f"silo-0{numero}",
                "nome": f"Silo {numero}",
                "tipo": "estacao",
                "dominio": "armazenagem",
                "perfil": "termometria-silo",
                "site": "mateiros",
            },
        )
    return "silo-02"


def temperatura_silo(valor, quando, silo="silo-02"):
    return nova_mensagem(silo, "telemetria", medidas=[Medida("temperatura", valor, "Cel")], timestamp=quando)


def test_regra_com_janela_de_tempo(cliente, silo):
    """UC-02: "temperatura do silo > 30 °C por 30 minutos → alarme crítico + SMS"."""
    regra = {
        "dominio": "armazenagem",
        "nome": "silo-quente",
        "severidade": "critico",
        "janela_s": 1800,
        "condicao": {"todas": [{"grandeza": "temperatura", "op": ">", "valor": 30}]},
        "acoes": [{"tipo": "alarme"}, {"tipo": "notificar", "canal": "sms", "destino": "+5564999998888"}],
    }
    assert cliente.post("/api/v1/regras", json=regra, headers=BRIGADA).status_code == 201

    agora = AGORA()
    enviar(
        cliente,
        temperatura_silo(25, agora - timedelta(minutes=45)),
        temperatura_silo(31, agora - timedelta(minutes=30)),
        temperatura_silo(32, agora - timedelta(minutes=15)),
    )
    enviar(cliente, temperatura_silo(33, agora - timedelta(minutes=1)))
    assert abertos(cliente, silo) == []  # 30 min acima de 30 °C ainda não se completaram desde a leitura de 25 °C

    enviar(cliente, temperatura_silo(34, agora))
    [alarme] = abertos(cliente, silo)
    assert alarme["regra"] == "silo-quente" and alarme["severidade"] == "critico"
    assert {a["tipo"] for a in detalhe(cliente, alarme["id"])["acoes"]} == {"notificar"}


def test_regra_de_ausencia_de_dados(cliente):
    cliente.post(
        "/api/v1/dispositivos",
        headers=BRIGADA,
        json={
            "slug": "est-calada-01",
            "nome": "Estação calada",
            "tipo": "estacao",
            "dominio": "incendio",
            "perfil": "estacao-incendio",
            "site": "mirador",
        },
    )
    ultimo = (AGORA() - timedelta(minutes=10)).replace(microsecond=0)
    enviar(cliente, leitura("est-calada-01", quando=ultimo))
    assert cliente.app.state.motor.verificar_ausencias() >= 1
    [alarme] = abertos(cliente, "est-calada-01")
    assert alarme["regra"] == "estacao-sem-dados" and alarme["severidade"] == "medio"
    assert datetime.fromisoformat(alarme["ocorrido_em"]) == ultimo + timedelta(seconds=300)

    enviar(cliente, leitura("est-calada-01"))
    assert abertos(cliente, "est-calada-01") == []


def test_webhook_com_novas_tentativas_e_espera_crescente(cliente, silo, webhook, banco):
    """RF-APP-03: falha no destino gera novas tentativas com espera crescente."""
    cliente.post(
        "/api/v1/regras",
        headers=BRIGADA,
        json={
            "dominio": "armazenagem",
            "nome": "silo-webhook",
            "severidade": "alto",
            "condicao": {"todas": [{"grandeza": "temperatura", "op": ">", "valor": 60}]},
            "acoes": [{"tipo": "alarme"}, {"tipo": "webhook", "url": "https://rotas.example/alertas"}],
        },
    )
    webhook.respostas = [503, 500]
    webhook.chamadas.clear()
    enviar(cliente, temperatura_silo(65, AGORA(), silo="silo-03"))  # sem histórico: a regra de janela não vale
    alarme = next(a for a in abertos(cliente, "silo-03") if a["regra"] == "silo-webhook")

    esperas = []
    for _ in range(2):
        with banco.begin() as conexao:
            esperas.append(
                conexao.execute(
                    text(
                        "select extract(epoch from proxima_tentativa_em - now()) "
                        "from execucao_acao where alarme_id = :a"
                    ),
                    {"a": alarme["id"]},
                ).scalar_one()
            )
            conexao.execute(
                text("update execucao_acao set proxima_tentativa_em = now() - interval '1 s' " "where alarme_id = :a"),
                {"a": alarme["id"]},
            )
        cliente.app.state.motor.executor.reprocessar_pendentes(cliente.app.state.motor.carregar_alarme)

    [execucao] = detalhe(cliente, alarme["id"])["acoes"]
    assert (execucao["status"], execucao["tentativas"]) == ("sucesso", 3)
    assert len(webhook.chamadas) == 3 and webhook.chamadas[0][1]["id"] == alarme["id"]
    assert esperas[0] < esperas[1]  # 2 s, depois 4 s


def test_acao_publicar_em_topico(cliente, silo, publicadas):
    cliente.post(
        "/api/v1/regras",
        headers=BRIGADA,
        json={
            "dominio": "armazenagem",
            "nome": "silo-publicar",
            "severidade": "medio",
            "condicao": {"todas": [{"grandeza": "temperatura", "op": "<", "valor": 0}]},
            "acoes": [{"tipo": "alarme"}, {"tipo": "publicar", "topico": "integracoes/silos/alertas"}],
        },
    )
    publicadas.clear()
    enviar(cliente, temperatura_silo(-2, AGORA()))
    [carga] = publicadas.em("integracoes/silos/alertas")
    assert carga["regra"] == "silo-publicar" and carga["dispositivo"] == "silo-02"


def test_regra_invalida_e_recusada_e_desativar_funciona(cliente, silo):
    invalida = {
        "dominio": "armazenagem",
        "nome": "x",
        "severidade": "alto",
        "escopo": "borda",
        "janela_s": 60,
        "condicao": {"todas": [{"grandeza": "temperatura", "op": ">", "valor": 1}]},
    }
    resposta = cliente.post("/api/v1/regras", json=invalida, headers=BRIGADA)
    assert resposta.status_code == 422 and "escopo borda" in str(resposta.json())
    sem_destino = invalida | {"escopo": "central", "janela_s": 0, "acoes": [{"tipo": "notificar", "canal": "sms"}]}
    assert cliente.post("/api/v1/regras", json=sem_destino, headers=BRIGADA).status_code == 422

    assert (
        cliente.patch("/api/v1/regras/silo-publicar", json={"ativa": False}, headers=BRIGADA).json()["ativa"] is False
    )
    assert "silo-publicar" not in [r.nome for r in cliente.app.state.motor.regras("brigada-cerrado", "armazenagem")]


def test_borda_recebe_so_regras_de_escopo_borda(cliente):
    """RF-EDG-04: regra alterada na central passa a valer no nó na próxima conexão."""
    regras = cliente.get("/api/v1/bordas/borda-goias/regras", headers=BORDA_GOIAS).json()
    assert {r["nome"] for r in regras} == {"risco-critico", "risco-alto", "risco-medio"}
    assert all(a["tipo"] == "comando" for r in regras for a in r["acoes"])

    novo = {"todas": [{"grandeza": "fumaca", "op": ">=", "valor": 80}, {"grandeza": "umidade", "op": "<", "valor": 15}]}
    cliente.patch("/api/v1/regras/risco-critico", json={"condicao": novo}, headers=BRIGADA)
    critica = next(
        r
        for r in cliente.get("/api/v1/bordas/borda-goias/regras", headers=BORDA_GOIAS).json()
        if r["nome"] == "risco-critico"
    )
    assert critica["condicao"] == novo
    original = {
        "todas": [{"grandeza": "fumaca", "op": ">=", "valor": 85}, {"grandeza": "umidade", "op": "<", "valor": 15}]
    }
    cliente.patch("/api/v1/regras/risco-critico", json={"condicao": original}, headers=BRIGADA)
    assert cliente.get("/api/v1/bordas/nao-existe/regras", headers=BORDA_GOIAS).status_code == 403


# ── Assinaturas: da brigada para a prefeitura ────────────────────────────────


@pytest.fixture(scope="module")
def prefeitura(cliente):
    """Anápolis acompanha incêndios num raio de 80 km e tem um terminal na Defesa Civil."""
    cliente.post(
        "/api/v1/catalogo/dominios", json={"slug": "alerta_publico", "nome": "Alerta público"}, headers=ANAPOLIS
    )
    cliente.post(
        "/api/v1/catalogo/perfis",
        headers=ANAPOLIS,
        json={
            "dominio": "alerta_publico",
            "slug": "terminal-alerta",
            "nome": "Terminal",
            "intervalo_esperado_s": 30,
        },
    )
    cliente.post(
        "/api/v1/dispositivos",
        headers=ANAPOLIS,
        json={
            "slug": "terminal-dc-anapolis",
            "nome": "Terminal DC",
            "tipo": "terminal",
            "dominio": "alerta_publico",
            "perfil": "terminal-alerta",
            "site": "defesa-civil",
        },
    )
    resposta = cliente.post(
        "/api/v1/assinaturas",
        headers=ANAPOLIS,
        json={
            "nome": "Entorno de Anápolis",
            "dominio": "incendio",
            "lat": -16.3267,
            "lon": -48.9530,
            "raio_km": 80,
            "severidade_min": "alto",
        },
    )
    assert resposta.status_code == 201
    return resposta.json()


def test_alarme_da_brigada_chega_ao_terminal_da_prefeitura(cliente, prefeitura, publicadas):
    publicadas.clear()
    enviar(cliente, alarme_local("est-pirenopolis-01", via="borda-goias"))  # ~55 km de Anápolis
    [origem] = abertos(cliente, "est-pirenopolis-01")

    [entrega] = cliente.get("/api/v1/alarmes", params={"abertos": True}, headers=ANAPOLIS).json()["itens"]
    assert entrega["origem_alarme_id"] == origem["id"] and entrega["severidade"] == "critico"
    assert "pirenopolis" in entrega["dados"]["resumo"] and entrega["dados"]["distancia_km"] < 80
    assert entrega["dispositivo"] is None  # a prefeitura não vê o dispositivo da brigada…
    assert cliente.get(f"/api/v1/alarmes/{origem['id']}", headers=ANAPOLIS).status_code == 404  # …nem o alarme dela

    [comando] = publicadas.em("pref-anapolis/defesa-civil/alerta_publico/terminal-dc-anapolis/comando")
    assert comando["dados"]["acao"] == "exibir_alerta" and comando["dados"]["alarme_id"] == entrega["id"]
    assert [e["inquilino"] for e in detalhe(cliente, origem["id"])["entregas"]] == ["pref-anapolis"]

    # o terminal confirma e o operador reconhece pelo próprio terminal
    for evento in ("recebido", "reconhecido"):
        enviar(
            cliente,
            nova_mensagem(
                "terminal-dc-anapolis",
                "evento",
                dados={
                    "evento": evento,
                    "comando_id": comando["id_mensagem"],
                    "alarme_id": entrega["id"],
                    "por": "operador-1",
                },
            ),
        )
    entrega = cliente.get(f"/api/v1/alarmes/{entrega['id']}", headers=ANAPOLIS).json()
    assert (entrega["estado"], entrega["reconhecido_por"]) == ("reconhecido", "terminal-dc-anapolis:operador-1")

    # a brigada resolve: a entrega é encerrada e o terminal avisado
    publicadas.clear()
    cliente.post(f"/api/v1/alarmes/{origem['id']}/resolver", headers=BRIGADA)
    assert cliente.get(f"/api/v1/alarmes/{entrega['id']}", headers=ANAPOLIS).json()["estado"] == "resolvido"
    [encerrar] = publicadas.em("pref-anapolis/defesa-civil/alerta_publico/terminal-dc-anapolis/comando")
    assert encerrar["dados"]["acao"] == "encerrar_alerta"


def test_alarme_fora_do_raio_ou_abaixo_da_severidade_nao_e_entregue(cliente, prefeitura):
    antes = cliente.get("/api/v1/alarmes", headers=ANAPOLIS).json()["total"]
    enviar(cliente, alarme_local("est-mateiros-01"))  # ~700 km de Anápolis
    enviar(cliente, alarme_local("est-jaragua-01", via="borda-goias", severidade="medio", regra="risco-medio"))
    assert cliente.get("/api/v1/alarmes", headers=ANAPOLIS).json()["total"] == antes


# ── Comandos e gêmeo pela API ────────────────────────────────────────────────


def test_comando_pelo_painel_e_resposta(cliente, publicadas):
    publicadas.clear()
    resposta = cliente.post(
        "/api/v1/dispositivos/atuador-mirador-01/comandos", json={"acao": "silenciar_alerta"}, headers=BRIGADA
    )
    assert resposta.status_code == 202 and resposta.json()["estado"] == "enviado"
    [enviado] = publicadas.em("brigada-cerrado/mirador/incendio/atuador-mirador-01/comando")
    assert enviado["id_mensagem"] == resposta.json()["id"]
    assert (
        cliente.post(
            "/api/v1/dispositivos/atuador-mirador-01/comandos", json={"acao": "x"}, headers=ANAPOLIS
        ).status_code
        == 404
    )


def test_gemeo_desejado_relatado_e_diferenca(cliente, publicadas):
    """RF-DIS-04: alterar o desejado com o aparelho offline gera a diferença, entregue na reconexão."""
    publicadas.clear()
    gemeo = cliente.put(
        "/api/v1/dispositivos/atuador-formosa-01/gemeo/desejado", json={"modo": "sirene", "volume": 80}, headers=BRIGADA
    ).json()
    assert gemeo["diferenca"] == {"modo": "sirene", "volume": 80}
    [comando] = publicadas.em("brigada-cerrado/formosa/incendio/atuador-formosa-01/comando")
    assert comando["dados"] == {"acao": "aplicar_desejado", "desejado": {"modo": "sirene", "volume": 80}}

    enviar(
        cliente,
        nova_mensagem("atuador-formosa-01", "estado", via="borda-goias", dados={"modo": "sirene", "volume": 80}),
    )
    gemeo = cliente.get("/api/v1/dispositivos/atuador-formosa-01/gemeo", headers=BRIGADA).json()
    assert gemeo["relatado"] == {"modo": "sirene", "volume": 80} and gemeo["diferenca"] == {}

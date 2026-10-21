"""Identidade e acesso: tokens, papéis, isolamento, credenciais de máquina e ACL dos brokers (RF-SEG, RF-DIS-02)."""

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from central.acesso_mqtt import arquivos_central, servicos_da_central
from nucleo import Medida, nova_mensagem
from nucleo.credenciais import derivar_senha, verificar_senha
from tests.central.conftest import (
    ADMIN,
    ANAPOLIS,
    AUDIENCIA,
    BORDA_GOIAS,
    BRIGADA,
    CREDENCIAIS_BORDA,
    EMISSOR,
    SEGREDO_PROVISIONAMENTO,
    basic,
    bearer,
    token,
)

LEITOR = bearer("leitor@brigada", "brigada-cerrado", "leitor")
OPERADOR = bearer("operador@brigada", "brigada-cerrado", "operador")
INTEGRADOR = bearer("integrador@brigada", "brigada-cerrado", "integrador")
BORDA_TOCANTINS = basic("borda-tocantins", CREDENCIAIS_BORDA["borda-tocantins"])


def leitura(dispositivo="est-anapolis-01"):
    return nova_mensagem(dispositivo, "telemetria", medidas=[Medida("temperatura", 25, "Cel")])


def credencial_simulada(slug):
    return basic(slug, derivar_senha(SEGREDO_PROVISIONAMENTO, slug))


# ── Tokens ───────────────────────────────────────────────────────────────────


def _token_com(**alteracoes):
    return {"Authorization": f"Bearer {token('x', 'brigada-cerrado', 'gestor', **alteracoes)}"}


@pytest.mark.parametrize(
    "cabecalho",
    [
        {},
        {"Authorization": "Bearer nao-e-jwt"},
        bearer("x", "brigada-cerrado", "gestor", expira_em=-10),
        _token_com(iss="http://outro-emissor/realms/x"),
        _token_com(aud="outra-api"),
        {
            "Authorization": "Bearer "
            + jwt.encode(
                {"iss": EMISSOR, "aud": AUDIENCIA, "sub": "x", "exp": 9999999999, "realm_access": {"roles": ["admin"]}},
                rsa.generate_private_key(public_exponent=65537, key_size=2048),
                algorithm="RS256",
            )
        },  # assinado por outra chave
    ],
    ids=["sem-token", "malformado", "expirado", "outro-emissor", "outra-audiencia", "outra-chave"],
)
def test_token_invalido_e_recusado_com_401(cliente, cabecalho):
    assert cliente.get("/api/v1/dispositivos", headers=cabecalho).status_code == 401


def test_usuario_sem_papel_da_plataforma_e_recusado(cliente):
    assert (
        cliente.get("/api/v1/dispositivos", headers=bearer("x", "brigada-cerrado", "offline_access")).status_code == 403
    )


# ── Papéis (RF-SEG-02) ───────────────────────────────────────────────────────


def test_leitor_consulta_mas_nao_altera(cliente):
    assert cliente.get("/api/v1/dispositivos", headers=LEITOR).status_code == 200
    assert cliente.post("/api/v1/catalogo/dominios", json={"slug": "x", "nome": "x"}, headers=LEITOR).status_code == 403
    assert (
        cliente.post("/api/v1/dispositivos/atuador-mirador-01/comandos", json={"acao": "a"}, headers=LEITOR).status_code
        == 403
    )


def test_operador_nao_altera_regras_nem_dispositivos(cliente):
    """Critério do RF-SEG-02."""
    assert cliente.patch("/api/v1/regras/risco-alto", json={"ativa": False}, headers=OPERADOR).status_code == 403
    assert cliente.patch("/api/v1/dispositivos/est-mirador-01", json={"nome": "x"}, headers=OPERADOR).status_code == 403
    assert cliente.post("/api/v1/dispositivos/est-mirador-01/revogar", headers=OPERADOR).status_code == 403
    # mas opera: comanda dispositivos
    resposta = cliente.post(
        "/api/v1/dispositivos/atuador-mirador-01/comandos", json={"acao": "silenciar_alerta"}, headers=OPERADOR
    )
    assert resposta.status_code == 202


def test_integrador_cadastra_dispositivo_mas_nao_regra(cliente):
    regra = {
        "dominio": "incendio",
        "nome": "x",
        "severidade": "alto",
        "condicao": {"todas": [{"grandeza": "a", "op": ">", "valor": 1}]},
    }
    assert cliente.post("/api/v1/regras", json=regra, headers=INTEGRADOR).status_code == 403
    dispositivo = {
        "slug": "est-integrador-01", "nome": "x", "tipo": "estacao", "dominio": "incendio",
        "perfil": "estacao-incendio", "site": "mirador",
    }  # fmt: skip
    assert cliente.post("/api/v1/dispositivos", json=dispositivo, headers=INTEGRADOR).status_code == 201


def test_destinos_de_notificacao_mascarados_para_quem_nao_e_gestor(cliente):
    """RF-DAD-05: telefone/e-mail completos só para o gestor."""
    acoes_gestor = cliente.get("/api/v1/regras/risco-critico", headers=BRIGADA).json()["acoes"]
    acoes_leitor = cliente.get("/api/v1/regras/risco-critico", headers=LEITOR).json()["acoes"]
    sms_gestor = next(a for a in acoes_gestor if a["tipo"] == "notificar")
    sms_leitor = next(a for a in acoes_leitor if a["tipo"] == "notificar")
    assert sms_gestor["destino"] == "+5562999990001" and sms_leitor["destino"] == "***0001"


def test_rotas_de_administracao_so_para_admin(cliente):
    assert cliente.get("/api/v1/inquilinos", headers=BRIGADA).status_code == 403
    assert cliente.get("/api/v1/dlq", headers=BRIGADA).status_code == 403
    assert cliente.get("/api/v1/inquilinos", headers=ADMIN).status_code == 200
    novo = {"slug": "coop-teste", "nome": "Cooperativa", "cenario": "rural"}
    assert cliente.post("/api/v1/inquilinos", json=novo, headers=ADMIN).status_code == 201


# ── Isolamento entre inquilinos (RF-SEG-03) ──────────────────────────────────


def test_usuario_nao_age_sobre_outro_inquilino(cliente):
    assert cliente.get("/api/v1/dispositivos", headers=BRIGADA | {"X-Inquilino": "pref-anapolis"}).status_code == 403
    assert cliente.get("/api/v1/dispositivos", headers=bearer("x", None, "gestor")).status_code == 403


def test_admin_escolhe_o_inquilino(cliente):
    assert cliente.get("/api/v1/dispositivos", headers=ADMIN).status_code == 400
    total = cliente.get("/api/v1/dispositivos", headers=ADMIN | {"X-Inquilino": "brigada-cerrado"}).json()["total"]
    assert total == cliente.get("/api/v1/dispositivos", headers=BRIGADA).json()["total"] > 0


# ── Ingestão HTTP por máquinas ───────────────────────────────────────────────


def test_ingestao_sem_credencial_e_recusada(cliente):
    assert cliente.post("/api/v1/ingestao", json=leitura()).status_code == 401
    assert (
        cliente.post("/api/v1/ingestao", json=leitura(), headers=basic("est-anapolis-01", "errada")).status_code == 401
    )
    assert cliente.post("/api/v1/ingestao", json=leitura(), headers=LEITOR).status_code == 403


def test_dispositivo_so_envia_os_proprios_dados(cliente):
    cabecalho = credencial_simulada("est-anapolis-01")
    assert cliente.post("/api/v1/ingestao", json=leitura("est-anapolis-01"), headers=cabecalho).status_code == 202
    resposta = cliente.post("/api/v1/ingestao", json=leitura("est-formosa-01"), headers=cabecalho)
    assert resposta.status_code == 400 and "credencial" in resposta.json()["erros"][0]["motivo"]


def test_borda_so_envia_dados_dos_seus_sites(cliente):
    lote = {"mensagens": [leitura("est-anapolis-01"), leitura("est-mateiros-01")]}
    resultados = cliente.post("/api/v1/ingestao/lote", json=lote, headers=BORDA_GOIAS).json()["resultados"]
    assert [r["status"] for r in resultados] == ["aceita", "recusada"]


def test_usuario_so_envia_dados_do_proprio_inquilino(cliente):
    assert cliente.post("/api/v1/ingestao", json=leitura(), headers=ANAPOLIS).status_code == 400
    assert cliente.post("/api/v1/ingestao", json=leitura(), headers=BRIGADA).status_code == 202


def test_rotas_da_borda_exigem_a_credencial_dela(cliente):
    assert cliente.get("/api/v1/bordas/borda-goias/regras").status_code == 401
    assert cliente.get("/api/v1/bordas/borda-goias/regras", headers=basic("borda-goias", "errada")).status_code == 401
    assert cliente.get("/api/v1/bordas/borda-goias/regras", headers=BORDA_TOCANTINS).status_code == 403
    assert (
        cliente.get("/api/v1/bordas/borda-goias/regras", headers=credencial_simulada("est-anapolis-01")).status_code
        == 403
    )


# ── Credenciais de dispositivo (RF-DIS-02) ───────────────────────────────────


def test_credencial_emitida_uma_vez_renovada_e_revogada(cliente):
    novo = {
        "slug": "est-credencial-01", "nome": "x", "tipo": "estacao", "dominio": "incendio",
        "perfil": "estacao-incendio", "site": "mirador",
    }  # fmt: skip
    criado = cliente.post("/api/v1/dispositivos", json=novo, headers=BRIGADA).json()
    primeira = criado["credencial"]
    assert "credencial" not in cliente.get("/api/v1/dispositivos/est-credencial-01", headers=BRIGADA).json()
    msg = lambda: leitura("est-credencial-01")  # noqa: E731
    assert cliente.post("/api/v1/ingestao", json=msg(), headers=basic("est-credencial-01", primeira)).status_code == 202

    segunda = cliente.post("/api/v1/dispositivos/est-credencial-01/credencial", headers=BRIGADA).json()["credencial"]
    assert cliente.post("/api/v1/ingestao", json=msg(), headers=basic("est-credencial-01", primeira)).status_code == 401
    assert cliente.post("/api/v1/ingestao", json=msg(), headers=basic("est-credencial-01", segunda)).status_code == 202

    assert cliente.post("/api/v1/dispositivos/est-credencial-01/revogar", headers=BRIGADA).json()["revogado"] is True
    assert cliente.post("/api/v1/ingestao", json=msg(), headers=basic("est-credencial-01", segunda)).status_code == 401
    # mensagem do revogado que chegue por outro caminho (ex.: MQTT) também é recusada pelo pipeline
    assert cliente.post("/api/v1/ingestao", json=msg(), headers=ADMIN).status_code == 400


def test_credencial_provisionada_pelo_integrador(cliente):
    novo = {
        "slug": "est-provisionada-01", "nome": "x", "tipo": "estacao", "dominio": "incendio",
        "perfil": "estacao-incendio", "site": "mirador", "credencial": "gravada-na-fabrica-123456",
    }  # fmt: skip
    assert cliente.post("/api/v1/dispositivos", json=novo, headers=BRIGADA).json()["credencial"] == novo["credencial"]
    cabecalho = basic("est-provisionada-01", novo["credencial"])
    assert cliente.post("/api/v1/ingestao", json=leitura("est-provisionada-01"), headers=cabecalho).status_code == 202


# ── Senhas e ACL dos brokers (RF-SEG-04) ─────────────────────────────────────


def _senhas(texto):
    return dict(linha.split(":", 1) for linha in texto.splitlines() if linha)


def test_acesso_do_broker_da_borda(cliente):
    acesso = cliente.get("/api/v1/bordas/borda-goias/acesso", headers=BORDA_GOIAS).json()
    senhas = _senhas(acesso["senhas"])
    assert "est-anapolis-01" in senhas and "est-mateiros-01" not in senhas  # só os sites dela
    assert verificar_senha(derivar_senha(SEGREDO_PROVISIONAMENTO, "est-anapolis-01"), senhas["est-anapolis-01"])
    assert verificar_senha(CREDENCIAIS_BORDA["borda-goias"], senhas["borda-goias"])
    assert "senha" not in acesso["senhas"].lower()

    acl = acesso["acl"]
    assert "user est-anapolis-01\ntopic write brigada-cerrado/anapolis/incendio/est-anapolis-01/telemetria\n" in acl
    assert "topic read brigada-cerrado/anapolis/incendio/est-anapolis-01/comando" in acl
    # dispositivo não lê o comando de outro nem publica no tópico de outro
    bloco = acl.split("user est-anapolis-01\n")[1].split("\n\n")[0]
    assert "atuador-anapolis-01" not in bloco and "#" not in bloco
    assert cliente.get("/api/v1/bordas/borda-goias/acesso", headers=BORDA_TOCANTINS).status_code == 403


def test_revogado_sai_do_arquivo_do_broker(cliente, banco):
    novo = {
        "slug": "est-revogada-02", "nome": "x", "tipo": "estacao", "dominio": "incendio",
        "perfil": "estacao-incendio", "site": "mirador",
    }  # fmt: skip
    cliente.post("/api/v1/dispositivos", json=novo, headers=BRIGADA)
    assert "est-revogada-02" in _senhas(arquivos_central(banco, [])[0])
    cliente.post("/api/v1/dispositivos/est-revogada-02/revogar", headers=BRIGADA)
    assert "est-revogada-02" not in _senhas(arquivos_central(banco, [])[0])


def test_acesso_do_broker_central(banco):
    servicos = servicos_da_central({"central": "s1", "ponte": "s2", "painel": "", "simulacao": "s4"}, "legado/base")
    senhas_txt, acl = arquivos_central(banco, servicos)
    senhas = _senhas(senhas_txt)
    assert {"central", "ponte", "simulacao", "borda-goias", "borda-tocantins"} <= senhas.keys()
    assert "painel" not in senhas  # sem senha configurada = sem usuário
    assert verificar_senha("s1", senhas["central"])
    # a bridge de cada borda só recebe comandos dos sites dela
    bridge = acl.split("user borda-goias\n")[1].split("\n\n")[0]
    assert "topic read brigada-cerrado/anapolis/+/+/comando" in bridge and "mateiros" not in bridge


def test_reprovisionar_com_a_credencial_do_aparelho(cliente):
    gravada = "credencial-do-aparelho-0001"
    resposta = cliente.post(
        "/api/v1/dispositivos/est-anapolis-01/credencial", json={"credencial": gravada}, headers=BRIGADA
    )
    assert resposta.json()["credencial"] == gravada
    assert (
        cliente.post("/api/v1/ingestao", json=leitura(), headers=basic("est-anapolis-01", gravada)).status_code == 202
    )
    curta = cliente.post(
        "/api/v1/dispositivos/est-anapolis-01/credencial", json={"credencial": "curta"}, headers=BRIGADA
    )
    assert curta.status_code == 422
    # volta à credencial derivada, usada por outros testes
    derivada = derivar_senha(SEGREDO_PROVISIONAMENTO, "est-anapolis-01")
    cliente.post("/api/v1/dispositivos/est-anapolis-01/credencial", json={"credencial": derivada}, headers=BRIGADA)

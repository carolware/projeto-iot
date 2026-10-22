"""Painel web: visão consolidada, eventos por local e tempo real por WebSocket (RF-APP-02/04, RF-SEG-03)."""

import pytest
from starlette.websockets import WebSocketDisconnect

from nucleo import Medida, nova_mensagem
from tests.central.conftest import ADMIN, ANAPOLIS, BRIGADA, token


def leitura(dispositivo, fumaca=5.0, umidade=60.0, temperatura=28.0):
    return nova_mensagem(
        dispositivo,
        "telemetria",
        medidas=[
            Medida("temperatura", temperatura, "Cel"),
            Medida("umidade", umidade, "%"),
            Medida("fumaca", fumaca, "1"),
        ],
    )


def enviar(cliente, *mensagens):
    corpo = cliente.post("/api/v1/ingestao/lote", json={"mensagens": list(mensagens)}, headers=ADMIN).json()
    assert corpo["recusadas"] == 0, corpo


def site(painel, slug):
    return next(s for s in painel["sites"] if s["slug"] == slug)


def test_eu(cliente):
    eu = cliente.get("/api/v1/eu", headers=BRIGADA).json()
    assert eu == {
        "usuario": "teste@sentinela",
        "papeis": ["gestor"],
        "inquilino": "brigada-cerrado",
        "cenario": "rural",
    }
    assert cliente.get("/api/v1/eu", headers=ANAPOLIS).json()["cenario"] == "urbano"
    assert cliente.get("/api/v1/eu").status_code == 401


def test_painel_classifica_o_risco_pelas_regras_do_inquilino(cliente):
    enviar(cliente, leitura("est-novo-progresso-01", fumaca=45, umidade=60))  # só o "risco-medio" (sem alarme)
    painel = cliente.get("/api/v1/painel", headers=BRIGADA).json()
    assert painel["cenario"] == "rural" and len(painel["sites"]) >= 9
    novo_progresso = site(painel, "novo-progresso")
    assert novo_progresso["risco"] == "medio" and novo_progresso["lat"] == pytest.approx(-7.1261)
    estacao = next(d for d in novo_progresso["dispositivos"] if d["tipo"] == "estacao")
    assert estacao["online"] and estacao["leituras"]["fumaca"]["valor"] == 45
    assert estacao["historico_fumaca"][-1] == 45

    enviar(cliente, leitura("est-novo-progresso-01", fumaca=95, umidade=5))
    novo_progresso = site(cliente.get("/api/v1/painel", headers=BRIGADA).json(), "novo-progresso")
    assert novo_progresso["risco"] == "critico" and novo_progresso["alarmes_abertos"] >= 1
    enviar(cliente, leitura("est-novo-progresso-01"))


def test_painel_da_prefeitura_so_mostra_o_que_e_dela(cliente):
    painel = cliente.get("/api/v1/painel", headers=ANAPOLIS).json()
    assert painel["cenario"] == "urbano"
    assert {s["slug"] for s in painel["sites"]} >= {"prefeitura", "defesa-civil", "bombeiros"}
    assert all(not d["slug"].startswith("est-") for s in painel["sites"] for d in s["dispositivos"])


def test_eventos_por_local(cliente):
    evento = nova_mensagem("atuador-mirador-01", "evento", dados={"evento": "executado", "acao": "teste-eventos"})
    enviar(cliente, evento)
    itens = cliente.get("/api/v1/eventos", params={"site": "mirador"}, headers=BRIGADA).json()["itens"]
    assert any(e["dados"].get("acao") == "teste-eventos" and e["dispositivo"] == "atuador-mirador-01" for e in itens)
    assert cliente.get("/api/v1/eventos", params={"site": "mirador"}, headers=ANAPOLIS).json()["total"] == 0


def _receber_ate(ws, *tipos, limite=30):
    """Lê do WebSocket até ter visto uma mensagem de cada tipo pedido (a ordem não importa)."""
    vistas: dict[str, dict] = {}
    for _ in range(limite):
        mensagem = ws.receive_json()
        if mensagem["tipo"] in tipos:
            vistas.setdefault(mensagem["tipo"], mensagem)
        if len(vistas) == len(tipos):
            return vistas[tipos[0]] if len(tipos) == 1 else vistas
    raise AssertionError(f"faltou: {set(tipos) - set(vistas)}")


def test_tempo_real_entrega_telemetria_e_alarmes_do_proprio_inquilino(cliente):
    """RF-APP-02: aplicação assinante recebe a telemetria logo após a ingestão."""
    url = f"/api/v1/tempo-real?token={token('t', 'brigada-cerrado', 'leitor')}"
    with cliente.websocket_connect(url) as ws:
        assert ws.receive_json() == {"tipo": "conectado", "inquilino": "brigada-cerrado", "usuario": "t"}
        msg = leitura("est-mirador-01", fumaca=93, umidade=8)
        enviar(cliente, msg)
        vistas = _receber_ate(ws, "telemetria", "alarme")
        telemetria, alarme = vistas["telemetria"], vistas["alarme"]
        assert telemetria["mensagem"]["id_mensagem"] == msg["id_mensagem"]
        assert telemetria["mensagem"]["contexto"]["site"] == "mirador"
        assert alarme["evento"] == "aberto" and alarme["alarme"]["site"] == "mirador"
    enviar(cliente, leitura("est-mirador-01"))


def test_tempo_real_nao_vaza_entre_inquilinos(cliente):
    cliente.post("/api/v1/catalogo/dominios", json={"slug": "alerta_publico", "nome": "Alerta"}, headers=ANAPOLIS)
    cliente.post(
        "/api/v1/catalogo/perfis",
        json={"dominio": "alerta_publico", "slug": "terminal-alerta", "nome": "Terminal", "intervalo_esperado_s": 30},
        headers=ANAPOLIS,
    )
    cliente.post(
        "/api/v1/dispositivos",
        json={
            "slug": "terminal-ws-anapolis",
            "nome": "Terminal WS",
            "tipo": "terminal",
            "dominio": "alerta_publico",
            "perfil": "terminal-alerta",
            "site": "defesa-civil",
        },
        headers=ANAPOLIS,
    )
    url = f"/api/v1/tempo-real?token={token('p', 'pref-anapolis', 'operador')}"
    with cliente.websocket_connect(url) as ws:
        ws.receive_json()
        enviar(cliente, leitura("est-mirador-01"))
        # a prefeitura recebe só o que é dela: o estado do próprio terminal chega, a telemetria da brigada não
        enviar(cliente, nova_mensagem("terminal-ws-anapolis", "estado", dados={"online": True}))
        recebida = ws.receive_json()  # a telemetria da brigada, enviada antes, não pode aparecer
        assert recebida["tipo"] == "estado" and recebida["mensagem"]["dispositivo"] == "terminal-ws-anapolis"


@pytest.mark.parametrize(
    "consulta",
    ["token=nao-e-jwt", f"token={token('x', 'brigada-cerrado', 'gestor')}&inquilino=pref-anapolis"],
    ids=["token-invalido", "outro-inquilino"],
)
def test_tempo_real_recusa_sem_acesso(cliente, consulta):
    with pytest.raises(WebSocketDisconnect), cliente.websocket_connect(f"/api/v1/tempo-real?{consulta}") as ws:
        ws.receive_json()


def test_cors_libera_so_o_painel(cliente):
    permitido = cliente.options(
        "/api/v1/painel", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"}
    )
    assert permitido.headers.get("access-control-allow-origin") == "http://localhost:3000"
    negado = cliente.options(
        "/api/v1/painel", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in negado.headers


def test_metricas_prometheus(cliente):
    """RF-OBS-01: métricas em formato Prometheus, por módulo."""
    enviar(cliente, leitura("est-mirador-01"))
    texto = cliente.get("/metrics/").text
    for metrica in (
        'sentinela_mensagens_total{canal="lote",status="aceita"}',
        "sentinela_ingestao_latencia_segundos_bucket",
        "sentinela_pipeline_segundos_count",
        "sentinela_motor_fila",
        "sentinela_tempo_real_conexoes",
        'sentinela_http_duracao_segundos_count{metodo="POST",rota="/api/v1/ingestao/lote",status="200"}',
    ):
        assert metrica in texto, metrica


def test_logs_json_com_correlacao(caplog):
    """RF-OBS-02: cada linha é JSON e carrega o id_mensagem de correlação."""
    import json
    import logging

    from nucleo.logs import FormatoJson

    registro = logging.LogRecord("nucleo.ingestao", logging.INFO, "", 0, "mensagem %s", ("aceita",), None)
    registro.id_mensagem = "b6d1e0c4-5f7a-4c2e-9d13-8a2f61c0e9b7"
    linha = json.loads(FormatoJson("central").format(registro))
    assert linha["msg"] == "mensagem aceita" and linha["servico"] == "central"
    assert linha["id_mensagem"] == "b6d1e0c4-5f7a-4c2e-9d13-8a2f61c0e9b7" and linha["ts"].endswith("Z")

"""
Integração da central (FastAPI + TimescaleDB). Cada teste cita o critério de
aceitação do documento de requisitos que ele demonstra.
"""

from datetime import UTC, datetime, timedelta
from statistics import mean

import pytest
from sqlalchemy import text

from nucleo import Medida, nova_mensagem
from tests.central.conftest import ADMIN, ANAPOLIS, BORDA_GOIAS, BRIGADA


def leitura(dispositivo="est-mateiros-01", quando=None, temp=30.0, umid=40.0, fumaca=5.0):
    return nova_mensagem(
        dispositivo,
        "telemetria",
        medidas=[Medida("temperatura", temp, "Cel"), Medida("umidade", umid, "%"), Medida("fumaca", fumaca, "1")],
        timestamp=quando or datetime.now(UTC),
    )


def contar(banco, sql: str, **params) -> int:
    with banco.connect() as conexao:
        return conexao.execute(text(sql), params).scalar_one()


# ── Ingestão ─────────────────────────────────────────────────────────────────


def test_ingestao_http_valida_202_invalida_400_com_erros(cliente, banco):
    """RF-ING-02: válida → 202; inválida → 400 com a lista de erros (e vai para a DLQ, RF-ING-06)."""
    msg = leitura()
    resposta = cliente.post("/api/v1/ingestao", json=msg, headers=ADMIN)
    assert resposta.status_code == 202 and resposta.json()["status"] == "aceita"

    invalida = leitura() | {"timestamp": "ontem"}
    resposta = cliente.post("/api/v1/ingestao", json=invalida, headers=ADMIN)
    assert resposta.status_code == 400
    assert resposta.json()["erros"][0]["campo"] == "timestamp"
    assert contar(banco, "select count(*) from dlq where payload like :p", p=f"%{invalida['id_mensagem']}%") == 1


def test_reenvio_e_armazenado_uma_unica_vez(cliente, banco):
    """RF-ING-08: mensagem reenviada (ex.: pela borda) com o mesmo id é armazenada uma vez."""
    msg = leitura()
    assert cliente.post("/api/v1/ingestao", json=msg, headers=ADMIN).json()["status"] == "aceita"
    assert cliente.post("/api/v1/ingestao", json=msg, headers=ADMIN).json()["status"] == "duplicada"
    assert contar(banco, "select count(*) from telemetria where id_mensagem = :i", i=msg["id_mensagem"]) == 3


def test_lote_de_5000_com_3_invalidas(cliente, banco):
    """RF-ING-03: lote de 5.000 com 3 inválidas grava 4.997 e informa as 3 recusadas."""
    base = datetime.now(UTC) - timedelta(days=1)
    lote = [leitura("est-jaragua-01", base + timedelta(seconds=i)) for i in range(5000)]
    lote[7]["dispositivo"] = "nao-existe"
    lote[2500]["medidas"] = []
    lote[4321]["versao"] = "9.9"
    resposta = cliente.post("/api/v1/ingestao/lote?via=borda-goias", json={"mensagens": lote}, headers=BORDA_GOIAS)
    corpo = resposta.json()
    assert resposta.status_code == 200
    assert (corpo["aceitas"], corpo["recusadas"], corpo["duplicadas"]) == (4997, 3, 0)
    assert [r["indice"] for r in corpo["resultados"] if r["status"] == "recusada"] == [7, 2500, 4321]
    ids = [m["id_mensagem"] for m in lote]
    assert contar(banco, "select count(*) from mensagem where id_mensagem = any(cast(:ids as uuid[]))", ids=ids) == 4997

    # sincronização repetida (link caiu no meio do envio): nada duplica
    repetido = cliente.post(
        "/api/v1/ingestao/lote?via=borda-goias", json={"mensagens": lote}, headers=BORDA_GOIAS
    ).json()
    assert (repetido["aceitas"], repetido["duplicadas"]) == (0, 4997)


def test_dado_atrasado_entra_no_lugar_certo_sem_virar_ultimo_estado(cliente):
    """RF-ING-04 / RF-DAD-02: medição de 48 h atrás entra na posição correta e não vira "atual"."""
    agora = datetime.now(UTC).replace(microsecond=0)
    cliente.post("/api/v1/ingestao", json=leitura("est-formosa-01", agora, temp=25.0), headers=ADMIN)
    atrasada = leitura("est-formosa-01", agora - timedelta(hours=48), temp=41.0)
    assert cliente.post("/api/v1/ingestao", json=atrasada, headers=ADMIN).json()["status"] == "aceita"

    estado = cliente.get("/api/v1/dispositivos/est-formosa-01/estado", headers=BRIGADA).json()
    assert {lt["grandeza"]: lt["valor"] for lt in estado["leituras"]}["temperatura"] == 25.0

    historico = cliente.get(
        "/api/v1/dispositivos/est-formosa-01/telemetria",
        params={"grandeza": "temperatura", "ordenar": "tempo"},
        headers=BRIGADA,
    ).json()["itens"]
    assert [p["valor"] for p in historico] == [41.0, 25.0]


def test_mqtt_estado_e_evento_alimentam_gemeo_e_historico(cliente, banco):
    estado = nova_mensagem("est-mirador-01", "estado", dados={"bateria": 87})
    evento = nova_mensagem("est-mirador-01", "evento", dados={"evento": "reinicio"})
    corpo = cliente.post("/api/v1/ingestao/lote", json={"mensagens": [estado, evento]}, headers=ADMIN).json()
    assert corpo["aceitas"] == 2
    assert cliente.get("/api/v1/dispositivos/est-mirador-01/estado", headers=BRIGADA).json()["relatado"] == {
        "bateria": 87
    }
    assert contar(banco, "select count(*) from evento where id_mensagem = :i", i=evento["id_mensagem"]) == 1


# ── Dados e agregações ───────────────────────────────────────────────────────


def test_agregacao_horaria_bate_com_os_dados_brutos(cliente):
    """RF-PRO-04: consulta agregada retorna os mesmos valores calculados sobre os dados brutos."""
    hora = (datetime.now(UTC) - timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    temperaturas = [20.0, 22.5, 31.0, 27.25]
    lote = [
        leitura("est-novo-progresso-01", hora + timedelta(minutes=10 * i), temp=t) for i, t in enumerate(temperaturas)
    ]
    cliente.post("/api/v1/ingestao/lote", json={"mensagens": lote}, headers=ADMIN)

    [ponto] = cliente.get(
        "/api/v1/dispositivos/est-novo-progresso-01/telemetria",
        params={
            "grandeza": "temperatura",
            "agregacao": "1h",
            "inicio": hora.isoformat(),
            "fim": (hora + timedelta(hours=1)).isoformat(),
        },
        headers=BRIGADA,
    ).json()["itens"]
    assert ponto["valor"] == pytest.approx(mean(temperaturas))
    assert (ponto["minimo"], ponto["maximo"], ponto["amostras"]) == (20.0, 31.0, 4)


def test_consulta_24h_de_um_dispositivo_responde_rapido(cliente):
    """RF-DAD-01: consulta de 24 h de um dispositivo responde em até 1 s."""
    inicio = (datetime.now(UTC) - timedelta(hours=24)).replace(microsecond=0)
    lote = [leitura("est-sandolandia-01", inicio + timedelta(seconds=30 * i)) for i in range(2880)]
    cliente.post("/api/v1/ingestao/lote", json={"mensagens": lote}, headers=ADMIN)
    t0 = datetime.now()
    resposta = cliente.get(
        "/api/v1/dispositivos/est-sandolandia-01/telemetria",
        params={"inicio": inicio.isoformat(), "tamanho": 500},
        headers=BRIGADA,
    )
    assert resposta.status_code == 200 and resposta.json()["total"] == 2880 * 3
    assert (datetime.now() - t0).total_seconds() < 1


# ── Catálogo, dispositivos e isolamento ──────────────────────────────────────


def test_dominio_novo_so_por_configuracao(cliente):
    """RF-CEN-01 / RNF-08: gestor cria "pecuaria" pela API e o domínio já recebe dados, sem código novo."""
    assert (
        cliente.post(
            "/api/v1/catalogo/dominios", json={"slug": "pecuaria", "nome": "Pecuária"}, headers=BRIGADA
        ).status_code
        == 201
    )
    perfil = {
        "dominio": "pecuaria",
        "slug": "bebedouro",
        "nome": "Bebedouro com sensor de nível",
        "intervalo_esperado_s": 1800,
        "grandezas": [{"grandeza": "nivel", "unidade": "%", "valor_min": 0, "valor_max": 100}],
    }
    assert cliente.post("/api/v1/catalogo/perfis", json=perfil, headers=BRIGADA).status_code == 201
    dispositivo = {
        "slug": "bebedouro-mateiros-01",
        "nome": "Bebedouro 1",
        "tipo": "estacao",
        "dominio": "pecuaria",
        "perfil": "bebedouro",
        "site": "mateiros",
    }
    assert cliente.post("/api/v1/dispositivos", json=dispositivo, headers=BRIGADA).status_code == 201
    msg = nova_mensagem("bebedouro-mateiros-01", "telemetria", medidas=[Medida("nivel", 64, "%")])
    assert cliente.post("/api/v1/ingestao", json=msg, headers=ADMIN).status_code == 202
    perfis = cliente.get("/api/v1/catalogo/perfis", params={"dominio": "pecuaria"}, headers=BRIGADA).json()
    assert perfis["itens"][0]["grandezas"][0]["grandeza"] == "nivel"


def test_mesma_hierarquia_serve_cidade_e_campo(cliente):
    """RF-CEN-03: inquilino → site → área → dispositivo representa fazenda com talhões e bairro com praças."""
    assert (
        cliente.post(
            "/api/v1/sites/mateiros/areas",
            json={"slug": "talhao-07", "nome": "Talhão 07", "lat": -10.55, "lon": -46.42, "raio_km": 1.2},
            headers=BRIGADA,
        ).status_code
        == 201
    )
    assert (
        cliente.post(
            "/api/v1/sites/defesa-civil/areas",
            json={"slug": "praca-bom-jesus", "nome": "Praça Bom Jesus", "lat": -16.33, "lon": -48.96},
            headers=ANAPOLIS,
        ).status_code
        == 201
    )
    areas = cliente.get("/api/v1/sites/mateiros/areas", headers=BRIGADA).json()["itens"]
    assert [a["slug"] for a in areas] == ["talhao-07"]


def test_online_offline_respeita_o_intervalo_do_perfil(cliente):
    """RF-DIS-06: sensor que envia de hora em hora não aparece offline após 5 min sem dados."""
    cliente.post(
        "/api/v1/catalogo/perfis",
        headers=BRIGADA,
        json={
            "dominio": "incendio",
            "slug": "estacao-horaria",
            "nome": "Estação horária",
            "intervalo_esperado_s": 3600,
            "grandezas": [{"grandeza": "temperatura", "unidade": "Cel"}],
        },
    )
    for slug, perfil in (("est-horaria-01", "estacao-horaria"), ("est-rapida-01", "estacao-incendio")):
        cliente.post(
            "/api/v1/dispositivos",
            headers=BRIGADA,
            json={
                "slug": slug,
                "nome": slug,
                "tipo": "estacao",
                "dominio": "incendio",
                "perfil": perfil,
                "site": "mateiros",
            },
        )
        cinco_min = datetime.now(UTC) - timedelta(minutes=5)
        msg = nova_mensagem(slug, "telemetria", medidas=[Medida("temperatura", 30, "Cel")], timestamp=cinco_min)
        assert cliente.post("/api/v1/ingestao", json=msg, headers=ADMIN).status_code == 202
    assert cliente.get("/api/v1/dispositivos/est-horaria-01", headers=BRIGADA).json()["online"] is True
    assert cliente.get("/api/v1/dispositivos/est-rapida-01", headers=BRIGADA).json()["online"] is False


def test_usuario_de_um_inquilino_nao_ve_dados_de_outro(cliente):
    """RF-SEG-03 (parte de dados): a prefeitura não enxerga as estações da brigada, nem pela API."""
    assert cliente.get("/api/v1/dispositivos/est-mateiros-01", headers=ANAPOLIS).status_code == 404
    assert cliente.get("/api/v1/dispositivos/est-mateiros-01/telemetria", headers=ANAPOLIS).status_code == 404
    assert cliente.get("/api/v1/dispositivos", headers=ANAPOLIS).json()["total"] == 0
    assert cliente.get("/api/v1/sites/mateiros", headers=ANAPOLIS).status_code == 404


def test_alteracao_de_dispositivo_fica_na_auditoria(cliente):
    """RF-DAD-06: toda alteração tem autor, data e valor anterior."""
    resposta = cliente.patch(
        "/api/v1/dispositivos/est-pirenopolis-01", json={"firmware": "sensor.py 2.0"}, headers=BRIGADA
    )
    assert resposta.status_code == 200 and resposta.json()["firmware"] == "sensor.py 2.0"
    [registro] = cliente.get(
        "/api/v1/auditoria", params={"entidade": "dispositivo", "tamanho": 1}, headers=BRIGADA
    ).json()["itens"]
    assert registro["autor"] == "teste@sentinela" and registro["acao"] == "atualizar"
    assert (registro["antes"]["firmware"], registro["depois"]["firmware"]) == (None, "sensor.py 2.0")


def test_cadastro_duplicado_e_referencia_invalida(cliente):
    dominio = {"slug": "incendio", "nome": "Duplicado"}
    assert cliente.post("/api/v1/catalogo/dominios", json=dominio, headers=BRIGADA).status_code == 409
    sem_perfil = {
        "slug": "x-01",
        "nome": "x",
        "tipo": "estacao",
        "dominio": "incendio",
        "perfil": "nao-existe",
        "site": "mateiros",
    }
    assert cliente.post("/api/v1/dispositivos", json=sem_perfil, headers=BRIGADA).status_code == 422
    assert cliente.get("/api/v1/dispositivos", headers=ADMIN | {"X-Inquilino": "nao-existe"}).status_code == 404


def test_tags_e_grupos_filtram_a_frota(cliente):
    """RF-DIS-07: filtrar por tag/grupo devolve só os dispositivos que têm todas as tags pedidas."""
    for slug, tags in (
        ("est-mateiros-01", ["grupo:jalapao", "bioma:cerrado", "grupo:jalapao"]),
        ("est-pirenopolis-01", ["bioma:cerrado"]),
    ):
        resposta = cliente.patch(f"/api/v1/dispositivos/{slug}", json={"tags": tags}, headers=BRIGADA)
        assert resposta.status_code == 200 and resposta.json()["tags"] == sorted(set(tags))

    def filtrar(*tags):
        corpo = cliente.get("/api/v1/dispositivos", params={"tag": list(tags)}, headers=BRIGADA).json()
        return {d["slug"] for d in corpo["itens"]}

    assert filtrar("bioma:cerrado") == {"est-mateiros-01", "est-pirenopolis-01"}
    assert filtrar("bioma:cerrado", "grupo:jalapao") == {"est-mateiros-01"}
    assert filtrar("grupo:inexistente") == set()
    assert cliente.get("/api/v1/dispositivos", params={"tag": "bioma:cerrado"}, headers=ANAPOLIS).json()["total"] == 0
    invalida = cliente.patch("/api/v1/dispositivos/est-mateiros-01", json={"tags": ["Com Espaço"]}, headers=BRIGADA)
    assert invalida.status_code == 422


def test_cadastro_em_lote_por_csv_com_relatorio_por_linha(cliente, banco, monkeypatch):
    """RF-DIS-03: 1.000 dispositivos por CSV; as linhas inválidas voltam no relatório sem barrar as demais."""
    chamadas = []
    monkeypatch.setattr(cliente.app.state.acesso, "atualizar", lambda: chamadas.append(1))
    cabecalho = "slug,nome,tipo,dominio,perfil,site,area,fabricante,firmware,tags"
    validas = [
        f"lote-{i:04d},Estação {i},estacao,incendio,estacao-incendio,mateiros,,Equipe X,1.0,grupo:lote;bioma:cerrado"
        for i in range(1000)
    ]
    invalidas = [
        "lote-0000,Repetida,estacao,incendio,estacao-incendio,mateiros,,,,",  # slug já usado na linha 2
        "lote-sem-perfil,x,estacao,incendio,nao-existe,mateiros,,,,",
        "lote-tipo-ruim,x,satelite,incendio,estacao-incendio,mateiros,,,,",
    ]
    csv = "\n".join([cabecalho, *validas, *invalidas])

    resposta = cliente.post("/api/v1/dispositivos/lote", content=csv, headers=BRIGADA | {"content-type": "text/csv"})

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert (corpo["total"], corpo["criados"], corpo["falhas"]) == (1003, 1000, 3)
    falhas = {item["linha"]: item for item in corpo["linhas"] if not item["criado"]}
    assert set(falhas) == {1002, 1003, 1004}
    assert "já existe" in falhas[1002]["erro"] and falhas[1002]["credencial"] is None
    assert "nao-existe" in falhas[1003]["erro"]
    assert "tipo" in falhas[1004]["erro"]
    credenciais = [item["credencial"] for item in corpo["linhas"] if item["criado"]]
    assert len(set(credenciais)) == 1000 and all(credenciais)
    assert chamadas == [1], "a ACL do broker é regerada uma vez por lote, não por linha"
    assert contar(banco, "SELECT count(*) FROM dispositivo WHERE slug LIKE 'lote-%'") == 1000
    filtrados = cliente.get("/api/v1/dispositivos", params={"tag": "grupo:lote"}, headers=BRIGADA).json()
    assert filtrados["total"] == 1000


def test_lote_com_cabecalho_errado_e_recusado_inteiro(cliente):
    headers = BRIGADA | {"content-type": "text/csv"}
    sem_site = "slug,nome,tipo,dominio,perfil\nx-01,x,estacao,incendio,estacao-incendio"
    assert cliente.post("/api/v1/dispositivos/lote", content=sem_site, headers=headers).status_code == 400
    coluna_extra = "slug,nome,tipo,dominio,perfil,site,senha\nx-01,x,estacao,incendio,estacao-incendio,mateiros,1"
    assert cliente.post("/api/v1/dispositivos/lote", content=coluna_extra, headers=headers).status_code == 400


@pytest.mark.parametrize(
    "rota",
    [
        "/api/v1/dispositivos",
        "/api/v1/sites",
        "/api/v1/catalogo/dominios",
        "/api/v1/catalogo/perfis",
        "/api/v1/auditoria",
        "/api/v1/bordas",
    ],
)
def test_listagens_aceitam_a_mesma_paginacao(cliente, rota):
    """RF-APP-07: todas as listagens aceitam os mesmos parâmetros de paginação."""
    corpo = cliente.get(rota, params={"pagina": 1, "tamanho": 2}, headers=BRIGADA).json()
    assert set(corpo) == {"itens", "pagina", "tamanho", "total"}
    assert len(corpo["itens"]) <= 2 and corpo["tamanho"] == 2
    assert cliente.get(rota, params={"tamanho": 0}, headers=BRIGADA).status_code == 422


def test_ordenacao_so_aceita_campos_permitidos(cliente):
    assert cliente.get("/api/v1/dispositivos", params={"ordenar": "-slug"}, headers=BRIGADA).status_code == 200
    assert cliente.get("/api/v1/dispositivos", params={"ordenar": "senha"}, headers=BRIGADA).status_code == 422


# ── Contratos e documentação ─────────────────────────────────────────────────


def test_contratos_publicados_por_url(cliente):
    """RF-INT-01 e RF-APP-01: contratos e OpenAPI acessíveis por URL."""
    assert cliente.get("/contratos/asyncapi.yaml").status_code == 200
    assert "Histórico" in cliente.get("/contratos/CHANGELOG.md").text
    assert cliente.get("/contratos/mensagem-canonica.v1.schema.json").json()["title"].startswith("Mensagem canônica")
    assert cliente.get("/contratos/exemplos/telemetria-estacao.json").status_code == 200
    assert cliente.get("/contratos/../config.py").status_code == 404
    rotas = cliente.get("/openapi.json").json()["paths"]
    assert {"/api/v1/ingestao", "/api/v1/ingestao/lote", "/api/v1/dispositivos/{slug}/telemetria"} <= set(rotas)


def test_borda_informa_estado_e_painel_mostra_tempo_sem_sincronizar(cliente):
    """RF-EDG-06: o painel mostra há quanto tempo cada nó está sem sincronizar."""
    assert (
        cliente.put(
            "/api/v1/bordas/borda-goias/estado", json={"fila_pendente": 42, "versao": "0.2.0"}, headers=BORDA_GOIAS
        ).status_code
        == 204
    )
    assert (
        cliente.put("/api/v1/bordas/nao-existe/estado", json={"fila_pendente": 0}, headers=BORDA_GOIAS).status_code
        == 403
    )
    bordas = {b["slug"]: b for b in cliente.get("/api/v1/bordas", headers=BRIGADA).json()["itens"]}
    assert bordas["borda-goias"]["fila_pendente"] == 42 and bordas["borda-goias"]["segundos_sem_sincronizar"] < 5
    assert bordas["borda-tocantins"]["segundos_sem_sincronizar"] is None

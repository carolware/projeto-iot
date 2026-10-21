"""Pipeline de ingestão com adaptadores em memória (RF-ING-03/04/06/08, RF-PRO-01, RF-CEN-02)."""

from datetime import UTC, datetime, timedelta

import pytest

from nucleo import Medida, nova_mensagem, parsear_topico
from nucleo.ingestao import (
    DispositivoCadastrado,
    GrandezaPerfil,
    MensagemEnriquecida,
    PipelineIngestao,
    Recusa,
)

AGORA = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

ESTACAO = DispositivoCadastrado(
    id="11111111-1111-1111-1111-111111111111",
    slug="est-mateiros-01",
    inquilino="brigada-cerrado",
    site="mateiros",
    dominio="incendio",
    perfil="estacao-incendio",
    grandezas={
        "temperatura": GrandezaPerfil("temperatura", "Cel", -10, 60),
        "umidade": GrandezaPerfil("umidade", "%", 0, 100),
        "fumaca": GrandezaPerfil("fumaca", "1", 0, 100),
    },
)
REVOGADA = DispositivoCadastrado(
    id="22222222-2222-2222-2222-222222222222",
    slug="est-revogada-01",
    inquilino="brigada-cerrado",
    site="mateiros",
    dominio="incendio",
    perfil="estacao-incendio",
    revogado=True,
)


class CatalogoFake:
    def __init__(self, *dispositivos: DispositivoCadastrado):
        self.dispositivos = {d.slug: d for d in dispositivos}

    def buscar_dispositivos(self, slugs):
        return {s: self.dispositivos[s] for s in slugs if s in self.dispositivos}


class RepositorioFake:
    def __init__(self):
        self.gravadas: dict[str, MensagemEnriquecida] = {}
        self.chamadas: list[list[MensagemEnriquecida]] = []

    def gravar(self, mensagens):
        self.chamadas.append(mensagens)
        novas = {m.id_mensagem for m in mensagens if m.id_mensagem not in self.gravadas}
        for m in mensagens:
            self.gravadas.setdefault(m.id_mensagem, m)
        return novas


class FilaFake:
    def __init__(self):
        self.recusas: list[Recusa] = []

    def registrar(self, recusas):
        self.recusas.extend(recusas)


class BarramentoFake:
    def __init__(self):
        self.aceitas: list[MensagemEnriquecida] = []
        self.recusadas: list[Recusa] = []

    def publicar_aceita(self, mensagem):
        self.aceitas.append(mensagem)

    def publicar_recusada(self, recusa):
        self.recusadas.append(recusa)


@pytest.fixture
def ambiente():
    repo, fila, barramento = RepositorioFake(), FilaFake(), BarramentoFake()
    pipeline = PipelineIngestao(CatalogoFake(ESTACAO, REVOGADA), repo, fila, barramento, relogio=lambda: AGORA)
    return pipeline, repo, fila, barramento


def leitura(temp=34.2, umid=18.0, fumaca=62.5, *, dispositivo="est-mateiros-01", quando=AGORA, unidade_temp="Cel"):
    return nova_mensagem(
        dispositivo,
        "telemetria",
        medidas=[
            Medida("temperatura", temp, unidade_temp),
            Medida("umidade", umid, "%"),
            Medida("fumaca", fumaca, "1"),
        ],
        timestamp=quando,
    )


def test_mensagem_valida_e_aceita_enriquecida_e_publicada(ambiente):
    pipeline, repo, fila, barramento = ambiente
    msg = leitura()
    [resultado] = pipeline.processar([msg], origem="http")
    assert resultado.status == "aceita" and resultado.id_mensagem == msg["id_mensagem"]
    gravada = repo.gravadas[msg["id_mensagem"]]
    assert gravada.dispositivo.inquilino == "brigada-cerrado"
    assert gravada.recebido_em == AGORA
    contexto = barramento.aceitas[0].para_dict()["contexto"]
    assert contexto == {
        "inquilino": "brigada-cerrado",
        "site": "mateiros",
        "area": None,
        "dominio": "incendio",
        "perfil": "estacao-incendio",
    }
    assert fila.recusas == []


def test_reenvio_com_mesmo_id_e_gravado_uma_vez(ambiente):
    pipeline, repo, _, barramento = ambiente
    msg = leitura()
    pipeline.processar([msg], origem="borda")
    [resultado] = pipeline.processar([msg], origem="borda")
    assert resultado.status == "duplicada"
    assert len(repo.gravadas) == 1 and len(barramento.aceitas) == 1


def test_duplicata_dentro_do_mesmo_lote(ambiente):
    pipeline, repo, _, _ = ambiente
    msg = leitura()
    resultados = pipeline.processar([msg, dict(msg)], origem="lote")
    assert [r.status for r in resultados] == ["aceita", "duplicada"]
    assert len(repo.chamadas[0]) == 1


def test_lote_e_gravado_em_ordem_de_medicao(ambiente):
    pipeline, repo, _, _ = ambiente
    tarde, cedo, meio = (leitura(quando=AGORA - timedelta(hours=h)) for h in (1, 48, 24))
    resultados = pipeline.processar([tarde, cedo, meio], origem="lote")
    assert all(r.status == "aceita" for r in resultados)
    assert [m.mensagem.timestamp for m in repo.chamadas[0]] == sorted(m.mensagem.timestamp for m in repo.chamadas[0])
    # resultados voltam na ordem em que foram enviados
    assert [r.id_mensagem for r in resultados] == [tarde["id_mensagem"], cedo["id_mensagem"], meio["id_mensagem"]]


def test_lote_de_5000_com_3_invalidas_grava_4997(ambiente):
    """Critério de aceitação do RF-ING-03."""
    pipeline, repo, fila, _ = ambiente
    lote = [leitura(quando=AGORA - timedelta(seconds=i)) for i in range(5000)]
    lote[10]["tipo"] = "sensor"
    del lote[2000]["medidas"]
    lote[4999]["dispositivo"] = "est-desconhecida-99"
    resultados = pipeline.processar(lote, origem="lote")
    recusadas = [r.indice for r in resultados if r.status == "recusada"]
    assert recusadas == [10, 2000, 4999]
    assert len(repo.gravadas) == 4997
    assert len(fila.recusas) == 3


def test_invalida_vai_para_a_fila_com_motivo(ambiente):
    pipeline, _, fila, barramento = ambiente
    msg = leitura()
    msg["timestamp"] = "ontem"
    [resultado] = pipeline.processar([msg], origem="mqtt:x")
    assert resultado.status == "recusada" and resultado.erros
    assert fila.recusas[0].origem == "mqtt:x" and fila.recusas[0].payload is msg
    assert barramento.recusadas == fila.recusas


def test_payload_que_nao_e_json_objeto_e_recusado(ambiente):
    pipeline, _, fila, _ = ambiente
    [resultado] = pipeline.processar(["texto qualquer"], origem="mqtt:x")
    assert resultado.status == "recusada" and resultado.id_mensagem is None
    assert len(fila.recusas) == 1


@pytest.mark.parametrize(
    ("ajuste", "campo"),
    [
        ({"dispositivo": "est-desconhecida-99"}, "dispositivo"),
        ({"dispositivo": "est-revogada-01"}, "dispositivo"),
    ],
)
def test_dispositivo_nao_autenticado_e_recusado(ambiente, ajuste, campo):
    pipeline, repo, _, _ = ambiente
    msg = leitura() | ajuste
    [resultado] = pipeline.processar([msg], origem="http")
    assert resultado.status == "recusada" and resultado.erros[0].campo == campo
    assert repo.gravadas == {}


def test_comando_nao_e_aceito_na_ingestao(ambiente):
    pipeline, _, _, _ = ambiente
    msg = nova_mensagem("est-mateiros-01", "comando", dados={"acao": "acionar_brigada"})
    [resultado] = pipeline.processar([msg], origem="http")
    assert resultado.status == "recusada" and resultado.erros[0].campo == "tipo"


def test_topico_precisa_bater_com_o_cadastro(ambiente):
    pipeline, _, _, _ = ambiente
    certo = parsear_topico("brigada-cerrado/mateiros/incendio/est-mateiros-01/telemetria")
    outro_inquilino = parsear_topico("pref-anapolis/mateiros/incendio/est-mateiros-01/telemetria")
    assert pipeline.processar([leitura()], "mqtt", certo)[0].status == "aceita"
    [resultado] = pipeline.processar([leitura()], "mqtt", outro_inquilino)
    assert resultado.status == "recusada" and resultado.erros[0].campo == "topico"


def test_unidade_e_normalizada_para_a_do_perfil(ambiente):
    pipeline, repo, _, _ = ambiente
    msg = leitura(temp=307.35, unidade_temp="K")
    pipeline.processar([msg], origem="http")
    temperatura = repo.gravadas[msg["id_mensagem"]].medidas[0]
    assert temperatura.unidade == "Cel" and temperatura.valor == pytest.approx(34.2)


def test_unidade_sem_conversao_e_recusada(ambiente):
    pipeline, _, _, _ = ambiente
    [resultado] = pipeline.processar([leitura(unidade_temp="dB")], origem="http")
    assert resultado.status == "recusada" and resultado.erros[0].campo == "medidas/0/unidade"


def test_grandeza_fora_do_perfil_e_recusada(ambiente):
    pipeline, _, _, _ = ambiente
    msg = leitura()
    msg["medidas"].append({"grandeza": "pm25", "valor": 12, "unidade": "ug/m3"})
    [resultado] = pipeline.processar([msg], origem="http")
    assert resultado.status == "recusada" and resultado.erros[0].campo == "medidas/3/grandeza"


def test_medida_fora_da_faixa_e_marcada_suspeita(ambiente):
    """Critério do RF-CEN-02: fora da faixa do perfil → suspeita (mas gravada)."""
    pipeline, repo, _, _ = ambiente
    msg = leitura(umid=130)
    [resultado] = pipeline.processar([msg], origem="http")
    assert resultado.status == "aceita"
    suspeitas = {m.grandeza: m.suspeita for m in repo.gravadas[msg["id_mensagem"]].medidas}
    assert suspeitas == {"temperatura": False, "umidade": True, "fumaca": False}


def test_estado_e_evento_passam_sem_medidas(ambiente):
    pipeline, repo, _, _ = ambiente
    estado = nova_mensagem("est-mateiros-01", "estado", dados={"online": True})
    evento = nova_mensagem("est-mateiros-01", "evento", dados={"evento": "reinicio"})
    assert [r.status for r in pipeline.processar([estado, evento], origem="http")] == ["aceita", "aceita"]
    assert all(m.medidas == () for m in repo.gravadas.values())

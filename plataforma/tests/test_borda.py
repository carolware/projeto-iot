"""Nó de borda: fila persistente, decisão local e sincronização (RF-EDG-01/02/03/05, RF-SEG-05)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

from borda.decisao_local import DecisorLocal
from borda.fila import PRIORIDADE_ALARME, PRIORIDADE_ESTADO, FilaLocal
from borda.sincronizador import LinkIndisponivel, Sincronizador
from nucleo import Medida, nova_mensagem, parsear_topico, validar_mensagem
from nucleo.regras import Regra

CHAVE = Fernet.generate_key()
REGRAS = [
    Regra.de_dict(r) for r in json.loads((Path(__file__).parents[1] / "borda" / "regras-padrao.json").read_text())
]
TOPICO = parsear_topico("brigada-cerrado/mateiros/incendio/est-mateiros-01/telemetria")


def leitura(fumaca=10.0, umidade=50.0, temperatura=30.0, quando=None):
    return nova_mensagem(
        "est-mateiros-01",
        "telemetria",
        medidas=[
            Medida("temperatura", temperatura, "Cel"),
            Medida("umidade", umidade, "%"),
            Medida("fumaca", fumaca, "1"),
        ],
        timestamp=quando or datetime.now(UTC),
    )


@pytest.fixture
def fila(tmp_path):
    f = FilaLocal(tmp_path / "fila.db", CHAVE)
    yield f
    f.fechar()


# ── Fila ─────────────────────────────────────────────────────────────────────


def test_fila_e_idempotente_por_id(fila):
    msg = leitura()
    assert fila.enfileirar(msg) is True
    assert fila.enfileirar(msg) is False
    assert fila.contagem()["pendente"] == 1


def test_fila_sobrevive_a_reinicio(tmp_path):
    """RF-EDG-01: o que foi aceito pelo nó não se perde se ele reiniciar (ou faltar energia)."""
    caminho = tmp_path / "fila.db"
    primeira = FilaLocal(caminho, CHAVE)
    mensagens = [leitura() for _ in range(3)]
    for m in mensagens:
        primeira.enfileirar(m)
    primeira.fechar()

    reaberta = FilaLocal(caminho, CHAVE)
    assert [i.mensagem for i in reaberta.proximas(10)] == sorted(mensagens, key=lambda m: m["timestamp"])
    reaberta.fechar()


def test_disco_nao_revela_os_dados(tmp_path):
    """RF-SEG-05: disco retirado de um nó de borda não revela os dados guardados."""
    caminho = tmp_path / "fila.db"
    f = FilaLocal(caminho, CHAVE)
    f.enfileirar(leitura(fumaca=12.345))
    f.registrar_invalida("mqtt:x", b'{"segredo": "fazenda boa vista"}', "teste")
    f.fechar()
    bruto = b"".join(p.read_bytes() for p in tmp_path.iterdir())
    assert b"est-mateiros-01" not in bruto and b"12.345" not in bruto and b"fazenda boa vista" not in bruto

    with pytest.raises(InvalidToken):
        FilaLocal(caminho, Fernet.generate_key()).proximas(1)


def test_alarme_sai_antes_da_telemetria_atrasada(fila):
    """RF-EDG-05: num link lento, o alarme gerado agora chega antes do lote atrasado de telemetria."""
    agora = datetime.now(UTC)
    atrasadas = [leitura(quando=agora - timedelta(hours=h)) for h in (10, 9, 8)]
    for m in atrasadas:
        fila.enfileirar(m)
    estado = nova_mensagem("est-mateiros-01", "estado", dados={"bateria": 80}, timestamp=agora - timedelta(hours=11))
    alarme = nova_mensagem("est-mateiros-01", "evento", dados={"evento": "alarme_local"}, timestamp=agora)
    fila.enfileirar(estado, PRIORIDADE_ESTADO)
    fila.enfileirar(alarme, PRIORIDADE_ALARME)
    ordem = [i.id_mensagem for i in fila.proximas(10)]
    assert ordem == [alarme["id_mensagem"], estado["id_mensagem"], *(m["id_mensagem"] for m in atrasadas)]


def test_limpeza_so_remove_o_que_a_central_confirmou(fila):
    enviada, pendente = leitura(), leitura()
    fila.enfileirar(enviada)
    fila.enfileirar(pendente)
    fila.marcar_enviadas([enviada["id_mensagem"]])
    assert fila.limpar_enviadas(mais_antigas_que_s=-1) == 1
    assert fila.contagem() == {"pendente": 1, "enviada": 0, "recusada": 0}


# ── Decisão local ────────────────────────────────────────────────────────────


class Relogio:
    def __init__(self):
        self.agora = datetime.now(UTC).timestamp()

    def __call__(self):
        return self.agora


def test_risco_critico_aciona_a_brigada_localmente():
    """RF-EDG-03: a regra dispara no nó, sem depender da central."""
    decisor = DecisorLocal(REGRAS, "borda-tocantins")
    msg = leitura(fumaca=93, umidade=10)
    decisao = decisor.avaliar(msg, TOPICO)
    assert decisao.regra.severidade == "critico"
    [(topico, comando)] = decisao.comandos
    assert topico == "brigada-cerrado/mateiros/incendio/atuador-mateiros-01/comando"
    assert comando["dados"]["acao"] == "acionar_brigada" and validar_mensagem(comando) == []
    # o alarme é um evento canônico da própria estação, com o horário da medição
    assert validar_mensagem(decisao.alarme) == []
    assert decisao.alarme["dispositivo"] == "est-mateiros-01" and decisao.alarme["timestamp"] == msg["timestamp"]
    assert decisao.alarme["via"] == "borda-tocantins"
    assert decisao.alarme["dados"]["comandos"] == [
        {"id": comando["id_mensagem"], "dispositivo": "atuador-mateiros-01", "acao": "acionar_brigada"}
    ]


def test_alerta_repete_so_apos_cooldown_mas_escalada_para_critico_e_imediata():
    relogio = Relogio()
    decisor = DecisorLocal(REGRAS, "borda-tocantins", cooldown_s=30, relogio=relogio)
    alto = lambda: leitura(fumaca=65, umidade=20, quando=datetime.fromtimestamp(relogio.agora, UTC))  # noqa: E731
    critico = lambda: leitura(fumaca=90, umidade=10, quando=datetime.fromtimestamp(relogio.agora, UTC))  # noqa: E731

    assert decisor.avaliar(alto(), TOPICO).regra.severidade == "alto"
    relogio.agora += 5
    assert decisor.avaliar(alto(), TOPICO) is None  # dentro do cooldown
    relogio.agora += 1
    assert decisor.avaliar(critico(), TOPICO).regra.severidade == "critico"  # escalou: não espera
    relogio.agora += 3
    assert decisor.avaliar(critico(), TOPICO) is None
    relogio.agora += 30
    assert decisor.avaliar(critico(), TOPICO).regra.severidade == "critico"


def test_risco_medio_nao_gera_alarme():
    assert DecisorLocal(REGRAS, "borda-goias").avaliar(leitura(fumaca=45, umidade=60), TOPICO) is None


def test_dado_atrasado_nao_dispara_alarme_como_se_fosse_atual():
    """RF-ING-04: medição de horas atrás (ex.: reenviada pelo sensor) não gera alarme agora."""
    decisor = DecisorLocal(REGRAS, "borda-tocantins")
    antiga = leitura(fumaca=93, umidade=10, quando=datetime.now(UTC) - timedelta(hours=2))
    assert decisor.avaliar(antiga, TOPICO) is None


def test_estado_por_site_e_independente():
    decisor = DecisorLocal(REGRAS, "borda-tocantins")
    outro = parsear_topico("brigada-cerrado/sandolandia/incendio/est-sandolandia-01/telemetria")
    assert decisor.avaliar(leitura(fumaca=93, umidade=10), TOPICO)
    assert decisor.avaliar(leitura(fumaca=93, umidade=10), outro)


# ── Sincronização ────────────────────────────────────────────────────────────


class CentralFake:
    """Simula POST /ingestao/lote: deduplica por id e pode estar fora do ar."""

    def __init__(self):
        self.recebidas: dict[str, dict] = {}
        self.online = True
        self.lotes: list[list[dict]] = []
        self.recusar: set[str] = set()

    def __call__(self, mensagens):
        if not self.online:
            raise LinkIndisponivel("sem rota para a central")
        self.lotes.append(mensagens)
        resultados = []
        for i, m in enumerate(mensagens):
            if m["id_mensagem"] in self.recusar:
                resultados.append({"indice": i, "status": "recusada", "erros": [{"campo": "x", "motivo": "teste"}]})
            elif m["id_mensagem"] in self.recebidas:
                resultados.append({"indice": i, "status": "duplicada", "erros": []})
            else:
                self.recebidas[m["id_mensagem"]] = m
                resultados.append({"indice": i, "status": "aceita", "erros": []})
        return {"resultados": resultados}


def test_desconexao_nao_perde_nem_duplica(fila):
    """RF-EDG-02 / critério do MVP: link cortado, reconexão, cada mensagem chega uma única vez e em ordem."""
    central = CentralFake()
    sync = Sincronizador(fila, central, tamanho_lote=100)
    inicio = datetime.now(UTC) - timedelta(hours=72)
    mensagens = [leitura(quando=inicio + timedelta(minutes=10 * i)) for i in range(432)]  # 72 h de leituras

    central.online = False
    for m in mensagens:
        fila.enfileirar(m)
    with pytest.raises(LinkIndisponivel):
        sync.esvaziar()
    assert fila.contagem()["pendente"] == 432 and central.recebidas == {}

    central.online = True
    assert sync.esvaziar() == 432
    assert len(central.recebidas) == 432
    chegada = [m["timestamp"] for lote in central.lotes for m in lote]
    assert chegada == sorted(chegada)
    assert fila.contagem()["pendente"] == 0


def test_resposta_perdida_reenvia_e_a_central_deduplica(fila):
    """O lote chegou mas a confirmação se perdeu: o reenvio volta como 'duplicada' e sai da fila."""
    central = CentralFake()
    msg = leitura()
    fila.enfileirar(msg)
    central([msg])  # a central gravou...
    sync = Sincronizador(fila, central)  # ...mas a borda não soube
    assert sync.esvaziar() == 1
    assert len(central.recebidas) == 1 and fila.contagem() == {"pendente": 0, "enviada": 1, "recusada": 0}


def test_recusada_pela_central_nao_trava_a_fila(fila):
    central = CentralFake()
    ruim, boa = leitura(), leitura()
    central.recusar.add(ruim["id_mensagem"])
    fila.enfileirar(ruim)
    fila.enfileirar(boa)
    assert Sincronizador(fila, central).esvaziar() == 2
    assert fila.contagem() == {"pendente": 0, "enviada": 1, "recusada": 1}


def test_resposta_inconsistente_mantem_tudo_pendente(fila):
    fila.enfileirar(leitura())
    sync = Sincronizador(fila, lambda mensagens: {"resultados": []})
    with pytest.raises(LinkIndisponivel):
        sync.ciclo()
    assert fila.contagem()["pendente"] == 1

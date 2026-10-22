"""HTTP local do nó de borda: ingestão sem MQTT (RF-EDG-01) e painel do técnico de campo (RF-EDG-07)."""

import threading

import pytest
import requests
from cryptography.fernet import Fernet

from borda import app
from borda.decisao_local import DecisorLocal
from borda.fila import FilaLocal
from borda.painel_local import AcessoLocal, criar_servidor
from nucleo import Medida, nova_mensagem
from nucleo.credenciais import hash_senha
from tests.test_borda import REGRAS
from tests.test_borda_app import ClienteFake, leitura

SENHA_DISPOSITIVO = "credencial-est-mateiros-01"
SENHA_PAINEL = "senha-do-tecnico"


def gravar_acesso(diretorio, dispositivos: dict[str, tuple[str, str]]) -> None:
    """dispositivos: slug → (senha, base do tópico). Mesmo formato que a central gera."""
    diretorio.mkdir(exist_ok=True)
    (diretorio / "senhas").write_text(
        "".join(f"{slug}:{hash_senha(senha)}\n" for slug, (senha, _) in dispositivos.items())
    )
    (diretorio / "acl").write_text(
        "".join(
            f"user {slug}\n" + "".join(f"topic write {base}/{tipo}\n" for tipo in ("telemetria", "evento", "estado"))
            for slug, (_, base) in dispositivos.items()
        )
    )


@pytest.fixture
def borda(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "BORDA_ID", "borda-tocantins")
    monkeypatch.setattr(app, "SITES", {"mateiros"})
    acesso = tmp_path / "acesso"
    gravar_acesso(
        acesso,
        {
            "est-mateiros-01": (SENHA_DISPOSITIVO, "brigada-cerrado/mateiros/incendio/est-mateiros-01"),
            "est-formosa-01": ("outra", "brigada-cerrado/formosa/incendio/est-formosa-01"),
        },
    )
    fila = FilaLocal(tmp_path / "fila.db", Fernet.generate_key())
    no = app.NoBorda(fila, DecisorLocal(REGRAS, "borda-tocantins"))
    no.cliente = ClienteFake()
    identificacao = {"borda": "borda-tocantins", "versao": "teste", "sites": ["mateiros"]}
    servidor = criar_servidor(no, AcessoLocal(acesso), SENHA_PAINEL, 0, identificacao)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{servidor.server_address[1]}"
    yield url, no, fila, acesso
    servidor.shutdown()
    fila.fechar()


def enviar(url, mensagem, usuario="est-mateiros-01", senha=SENHA_DISPOSITIVO):
    return requests.post(f"{url}/api/v1/ingestao", json=mensagem, auth=(usuario, senha), timeout=5)


def test_ingestao_http_local_entra_na_mesma_fila_que_o_mqtt(borda):
    """RF-EDG-01: aparelho sem MQTT envia à borda; reenvio não duplica."""
    url, _, fila, _ = borda
    msg = leitura()
    resposta = enviar(url, msg)
    assert resposta.status_code == 202 and resposta.json()["status"] == "aceita"
    assert enviar(url, msg).json()["status"] == "duplicada"
    [item] = fila.proximas(10)
    assert item.mensagem["id_mensagem"] == msg["id_mensagem"] and item.mensagem["via"] == "borda-tocantins"


def test_ingestao_http_local_aplica_a_regra_local_sem_link(borda):
    url, no, fila, _ = borda
    assert enviar(url, leitura(fumaca=93, umidade=8)).status_code == 202
    [(topico, comando)] = no.cliente.publicadas
    assert topico == "brigada-cerrado/mateiros/incendio/atuador-mateiros-01/comando"
    assert fila.contagem()["pendente"] == 2  # leitura + alarme local


@pytest.mark.parametrize(
    ("usuario", "senha", "mensagem", "status"),
    [
        ("est-mateiros-01", "errada", "valida", 401),
        ("nao-cadastrado", "x", "valida", 401),
        ("est-formosa-01", "outra", "formosa", 403),  # site atendido por outra borda
        ("est-mateiros-01", SENHA_DISPOSITIVO, "outro-dispositivo", 400),  # falar em nome de outro
        ("est-mateiros-01", SENHA_DISPOSITIVO, "fora-do-contrato", 400),
    ],
)
def test_ingestao_http_local_recusa(borda, usuario, senha, mensagem, status):
    url, _, fila, _ = borda
    corpos = {
        "valida": leitura(),
        "formosa": nova_mensagem("est-formosa-01", "telemetria", medidas=[Medida("temperatura", 30, "Cel")]),
        "fora-do-contrato": {"versao": "1.0"},
        "outro-dispositivo": nova_mensagem("est-outra-01", "telemetria", medidas=[Medida("temperatura", 30, "Cel")]),
    }
    assert enviar(url, corpos[mensagem], usuario, senha).status_code == status
    assert fila.contagem()["pendente"] == 0


def test_credencial_revogada_na_central_deixa_de_valer_na_borda(borda):
    """A borda relê senhas/ACL quando a sincronização troca os arquivos (RF-DIS-02)."""
    url, _, _, acesso = borda
    assert enviar(url, leitura()).status_code == 202
    gravar_acesso(acesso, {})
    assert enviar(url, leitura()).status_code == 401


def test_painel_local_mostra_leituras_alarmes_fila_e_link(borda):
    """RF-EDG-07: técnico de campo vê o estado do nó sem depender da central."""
    url, no, _, _ = borda
    enviar(url, leitura(fumaca=93, umidade=8))
    no.link.online = False
    assert requests.get(url, timeout=5).status_code == 401
    assert requests.get(url, auth=("tecnico", "errada"), timeout=5).status_code == 401

    estado = requests.get(f"{url}/estado", auth=("tecnico", SENHA_PAINEL), timeout=5).json()
    assert estado["link"]["online"] is False
    assert estado["fila"]["pendente"] == 2
    assert "est-mateiros-01" in estado["ultimas_leituras"]
    assert estado["alarmes_recentes"][0]["comandos"] == ["acionar_brigada"]

    pagina = requests.get(url, auth=("tecnico", SENHA_PAINEL), timeout=5)
    assert pagina.status_code == 200 and "text/html" in pagina.headers["Content-Type"]
    assert "SEM LINK" in pagina.text and "est-mateiros-01" in pagina.text


def test_painel_local_fica_desligado_sem_senha(tmp_path, borda):
    _, no, _, acesso = borda
    servidor = criar_servidor(no, AcessoLocal(acesso), "", 0, {"borda": "b", "versao": "v", "sites": []})
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{servidor.server_address[1]}"
        assert requests.get(url, auth=("tecnico", ""), timeout=5).status_code == 403
    finally:
        servidor.shutdown()

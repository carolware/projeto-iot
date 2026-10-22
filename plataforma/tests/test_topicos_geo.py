import pytest

from nucleo import Topico, dentro_do_raio, distancia_km, montar_topico, parsear_topico
from nucleo.topicos import padrao_assinatura


def test_montar_e_parsear_topico():
    texto = montar_topico("pref-anapolis", "defesa-civil", "alerta_publico", "terminal-dc-01", "comando")
    assert texto == "pref-anapolis/defesa-civil/alerta_publico/terminal-dc-01/comando"
    esperado = Topico("pref-anapolis", "defesa-civil", "alerta_publico", "terminal-dc-01", "comando")
    assert parsear_topico(texto) == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "sentinela-iot-2026-joao-carol/monitoramento-br/mateiros/sensor/fumaca",  # formato antigo
        "brigada-cerrado/mateiros/incendio/est-01",  # nível faltando
        "brigada-cerrado/mateiros/incendio/est-01/alarme",  # tipo desconhecido
        "Brigada/mateiros/incendio/est-01/telemetria",  # maiúscula
        "brigada-cerrado/+/incendio/est-01/telemetria",  # curinga não é tópico de publicação
    ],
)
def test_topicos_fora_da_convencao(texto: str):
    assert parsear_topico(texto) is None


def test_montar_topico_recusa_nivel_invalido():
    with pytest.raises(ValueError):
        montar_topico("brigada cerrado", "mateiros", "incendio", "est-01", "telemetria")
    with pytest.raises(ValueError):
        montar_topico("brigada-cerrado", "mateiros", "incendio", "est-01", "sensor")


def test_padrao_assinatura():
    assert padrao_assinatura("telemetria") == "+/+/+/+/telemetria"
    assert padrao_assinatura("comando", "pref-anapolis") == "pref-anapolis/+/+/+/comando"
    with pytest.raises(ValueError):
        padrao_assinatura("sensor")


def test_distancia_anapolis_goiania():
    # Anápolis → Goiânia ≈ 52 km em linha reta
    assert distancia_km(-16.3267, -48.9530, -16.6869, -49.2648) == pytest.approx(52, abs=5)


def test_distancia_de_um_ponto_para_ele_mesmo():
    assert distancia_km(-10.5464, -46.4168, -10.5464, -46.4168) == 0


def test_dentro_do_raio():
    # raio FIRMS padrão do gateway: 0.15° ≈ 16,7 km
    assert dentro_do_raio(-10.5464, -46.4168, 16.7, -10.55, -46.30)
    assert not dentro_do_raio(-10.5464, -46.4168, 16.7, -10.80, -46.41)

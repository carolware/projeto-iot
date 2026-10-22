"""
carga.py — teste de carga da instância central (RNF-01 e RNF-02).

    1. cadastra pela API (como gestor.brigada) o domínio `carga`, o perfil
       `sensor-carga`, N dispositivos no site `mirador` e a regra `pico-carga`
       (temperatura > 90 °C → alarme alto); idempotente;
    2. abre uma conexão MQTT/TLS por dispositivo, com a credencial própria,
       e publica telemetria canônica na taxa pedida (padrão 1.000 msg/s);
    3. a cada intervalo, um dispositivo manda um pico (95 °C): a regra abre um
       alarme, e a leitura normal seguinte o resolve;
    4. confere pela métrica da central que todas foram aceitas (gravadas) e
       escuta o WebSocket /tempo-real, o mesmo do painel, para medir:
         publicação → telemetria no painel     (meta p95 ≤ 2 s)
         publicação do pico → alarme no painel (meta p95 ≤ 3 s)

Os tempos são medidos no mesmo relógio (o desta máquina), do publish até a
chegada no WebSocket — o caminho inteiro: broker, ingestão, banco, motor de
regras e tempo real.

Uso (com o compose no ar, a partir da raiz do repo):
    python plataforma/simuladores/carga/carga.py --taxa 1000 --dispositivos 50 --duracao 60
"""

import argparse
import asyncio
import json
import os
import ssl
import statistics
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import requests
import websockets
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))  # permite rodar como script

from nucleo.credenciais import derivar_senha  # noqa: E402
from nucleo.mensagem import Medida, nova_mensagem  # noqa: E402
from nucleo.topicos import montar_topico  # noqa: E402

load_dotenv()
KEYCLOAK = os.getenv("KEYCLOAK_URL", "http://localhost:8080/realms/sentinela")
SENHA_DEMO = os.getenv("SENHA_USUARIOS_DEMO", "sentinela")
CA = os.getenv("MQTT_CA_LOCAL", str(RAIZ / "infra/pki/gerado/ca.crt"))

INQUILINO, SITE, DOMINIO, PERFIL, REGRA = "brigada-cerrado", "mirador", "carga", "sensor-carga", "pico-carga"
NORMAL, PICO = 25.0, 95.0
META_TELEMETRIA_S, META_ALARME_S = 2.0, 3.0


# ── 1. Cadastro pela API ────────────────────────────────────────────────────


def entrar(usuario: str) -> str:
    resposta = requests.post(
        f"{KEYCLOAK}/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "cli", "username": usuario, "password": SENHA_DEMO},
        timeout=10,
    )
    resposta.raise_for_status()
    return resposta.json()["access_token"]


def preparar(central: str, token: str, slugs: list[str], segredo: str) -> None:
    cabecalho = {"Authorization": f"Bearer {token}"}

    def postar(rota: str, corpo: dict) -> bool:
        resposta = requests.post(f"{central}{rota}", json=corpo, headers=cabecalho, timeout=10)
        if resposta.status_code == 409:
            return False
        resposta.raise_for_status()
        return True

    postar("/api/v1/catalogo/dominios", {"slug": DOMINIO, "nome": "Teste de carga"})
    postar(
        "/api/v1/catalogo/perfis",
        {
            "dominio": DOMINIO,
            "slug": PERFIL,
            "nome": "Sensor do teste de carga",
            "intervalo_esperado_s": 60,
            "grandezas": [{"grandeza": "temperatura", "unidade": "Cel", "valor_min": -50, "valor_max": 150}],
        },
    )
    postar(
        "/api/v1/regras",
        {
            "dominio": DOMINIO,
            "nome": REGRA,
            "severidade": "alto",
            "condicao": {"todas": [{"grandeza": "temperatura", "op": ">", "valor": 90}]},
            "acoes": [{"tipo": "alarme"}],
        },
    )
    novos = 0
    for slug in slugs:
        credencial = derivar_senha(segredo, slug)
        criado = postar(
            "/api/v1/dispositivos",
            {
                "slug": slug,
                "nome": f"Carga {slug[-3:]}",
                "tipo": "estacao",
                "dominio": DOMINIO,
                "perfil": PERFIL,
                "site": SITE,
                "fabricante": "simulador",
                "firmware": "carga",
                "credencial": credencial,
            },
        )
        if criado:
            novos += 1
        else:  # já existia: garante a credencial derivada
            requests.post(
                f"{central}/api/v1/dispositivos/{slug}/credencial",
                json={"credencial": credencial},
                headers=cabecalho,
                timeout=10,
            ).raise_for_status()
    print(f"cadastro: domínio {DOMINIO}, perfil {PERFIL}, regra {REGRA}, {len(slugs)} dispositivos ({novos} novos)")


# ── 2. Publicação MQTT ──────────────────────────────────────────────────────


def conectar(slugs: list[str], segredo: str, broker: str, porta: int) -> dict[str, mqtt.Client]:
    """Uma conexão por dispositivo; espera o broker recarregar a ACL dos recém-criados."""
    clientes: dict[str, mqtt.Client] = {}
    prazo = time.monotonic() + 30
    for slug in slugs:
        while True:
            conectado = threading.Event()
            cliente = mqtt.Client(client_id=f"{slug}-{os.getpid()}", clean_session=True)
            cliente.username_pw_set(slug, derivar_senha(segredo, slug))
            cliente.tls_set(ca_certs=CA, tls_version=ssl.PROTOCOL_TLS_CLIENT)
            cliente.max_inflight_messages_set(1000)
            cliente.on_connect = lambda c, u, f, rc, ev=conectado: rc == 0 and ev.set()
            cliente.connect(broker, porta, keepalive=30)
            cliente.loop_start()
            if conectado.wait(3):
                clientes[slug] = cliente
                break
            cliente.loop_stop()
            if time.monotonic() > prazo:
                raise SystemExit(f"{slug}: broker recusou a conexão (credencial/ACL ainda não recarregada?)")
            time.sleep(1)
    print(f"mqtt: {len(clientes)} conexões TLS abertas em {broker}:{porta}")
    return clientes


class Medicao:
    def __init__(self):
        self.publicadas: dict[str, float] = {}  # id_mensagem → instante do publish
        self.picos: dict[str, float] = {}
        self.telemetria: list[float] = []
        self.alarmes: list[float] = []
        self.trava = threading.Lock()


def publicar(clientes: dict[str, mqtt.Client], medicao: Medicao, taxa: int, duracao: float, picos_s: float) -> float:
    slugs = list(clientes)
    intervalo = 1 / taxa
    proximo_pico = time.monotonic() + picos_s
    pico_da_vez = 0
    inicio = time.monotonic()
    enviadas = 0
    while (agora := time.monotonic()) - inicio < duracao:
        # agenda absoluta: atrasos de um envio são compensados nos seguintes
        alvo = inicio + enviadas * intervalo
        if alvo > agora:
            time.sleep(alvo - agora)
        slug = slugs[enviadas % len(slugs)]
        valor = NORMAL
        if time.monotonic() >= proximo_pico and slug == slugs[pico_da_vez % len(slugs)]:
            valor, proximo_pico, pico_da_vez = PICO, proximo_pico + picos_s, pico_da_vez + 1
        mensagem = nova_mensagem(slug, "telemetria", medidas=[Medida("temperatura", valor, "Cel")])
        with medicao.trava:
            medicao.publicadas[mensagem["id_mensagem"]] = time.time()
            if valor == PICO:
                medicao.picos[mensagem["id_mensagem"]] = time.time()
        clientes[slug].publish(montar_topico(INQUILINO, SITE, DOMINIO, slug, "telemetria"), json.dumps(mensagem), qos=1)
        enviadas += 1
    return time.monotonic() - inicio


# ── 3. Medição pelo WebSocket ───────────────────────────────────────────────


async def escutar(url: str, medicao: Medicao, pronto: asyncio.Event, parar: asyncio.Event) -> None:
    async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
        pronto.set()
        while not parar.is_set():
            try:
                bruto = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except TimeoutError:
                continue
            chegada = time.time()
            carga = json.loads(bruto)
            if carga.get("tipo") == "telemetria":
                id_mensagem = carga["mensagem"]["id_mensagem"]
                with medicao.trava:
                    publicada = medicao.publicadas.get(id_mensagem)
                if publicada is not None:
                    medicao.telemetria.append(chegada - publicada)
            elif carga.get("tipo") == "alarme" and carga.get("evento") == "aberto":
                origem = (carga["alarme"].get("dados") or {}).get("mensagem_origem")
                with medicao.trava:
                    publicada = medicao.picos.get(origem)
                if publicada is not None:
                    medicao.alarmes.append(chegada - publicada)


def percentil(valores: list[float], p: float) -> float:
    if len(valores) < 2:
        return valores[0] if valores else float("nan")
    return statistics.quantiles(valores, n=100, method="inclusive")[int(p) - 1]


def aceitas_mqtt(metricas: str) -> float:
    """Contador da própria central: mensagens MQTT aceitas (gravadas no banco), somado entre as réplicas."""
    total = 0.0
    for url in metricas.split(","):
        for linha in requests.get(url, timeout=10).text.splitlines():
            if linha.startswith('sentinela_mensagens_total{canal="mqtt",status="aceita"}'):
                total += float(linha.rsplit(" ", 1)[1])
    return total


def relatar(medicao: Medicao, tempo_envio: float, aceitas: float) -> bool:
    enviadas, recebidas = len(medicao.publicadas), len(medicao.telemetria)
    print(f"\nenviadas: {enviadas} em {tempo_envio:.1f} s ({enviadas / tempo_envio:.0f} msg/s)")
    print(f"aceitas pela central (métrica): {aceitas:.0f} ({aceitas / max(enviadas, 1):.1%})")
    print(f"recebidas no painel (WebSocket): {recebidas} ({recebidas / max(enviadas, 1):.1%})")
    ok = aceitas >= enviadas
    for nome, valores, meta in (
        ("publicação → telemetria", medicao.telemetria, META_TELEMETRIA_S),
        ("pico → alarme aberto   ", medicao.alarmes, META_ALARME_S),
    ):
        if not valores:
            print(f"{nome}: nenhuma amostra")
            ok = False
            continue
        p95 = percentil(valores, 95)
        passou = p95 <= meta
        ok &= passou
        print(
            f"{nome}: n={len(valores):>6}  p50={percentil(valores, 50) * 1000:7.0f} ms  "
            f"p95={p95 * 1000:7.0f} ms  p99={percentil(valores, 99) * 1000:7.0f} ms  "
            f"máx={max(valores) * 1000:7.0f} ms  meta p95 ≤ {meta:.0f} s: {'OK' if passou else 'FALHOU'}"
        )
    print(f"alarmes medidos: {len(medicao.alarmes)} de {len(medicao.picos)} picos")
    return ok


async def principal(args: argparse.Namespace) -> bool:
    segredo = os.getenv("SEGREDO_PROVISIONAMENTO")
    if not segredo:
        raise SystemExit("SEGREDO_PROVISIONAMENTO não definido (ver .env)")
    central = args.central.rstrip("/")
    slugs = [f"carga-{i:03d}" for i in range(1, args.dispositivos + 1)]
    token = entrar("gestor.brigada")
    preparar(central, token, slugs, segredo)
    clientes = await asyncio.to_thread(conectar, slugs, segredo, args.broker, args.porta)

    medicao = Medicao()
    pronto, parar = asyncio.Event(), asyncio.Event()
    url = central.replace("http", "ws", 1) + f"/api/v1/tempo-real?token={token}"
    if args.sem_painel:  # só vazão: ninguém conectado ao tempo real
        pronto.set()
        escuta = asyncio.create_task(parar.wait())
    else:
        escuta = asyncio.create_task(escutar(url, medicao, pronto, parar))
    await asyncio.wait_for(pronto.wait(), 10)

    antes = aceitas_mqtt(args.metricas)
    print(f"publicando {args.taxa} msg/s por {args.duracao:.0f} s (pico a cada {args.picos:g} s)…")
    tempo_envio = await asyncio.to_thread(publicar, clientes, medicao, args.taxa, args.duracao, args.picos)
    await asyncio.sleep(args.espera)  # deixa o fim da fila chegar ao painel
    parar.set()
    await escuta
    for cliente in clientes.values():
        cliente.loop_stop()
        cliente.disconnect()
    return relatar(medicao, tempo_envio, aceitas_mqtt(args.metricas) - antes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--central", default="http://localhost:8000")
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--porta", type=int, default=8883)
    parser.add_argument("--taxa", type=int, default=1000, help="mensagens por segundo (total)")
    parser.add_argument("--dispositivos", type=int, default=50)
    parser.add_argument("--duracao", type=float, default=60, help="segundos publicando")
    parser.add_argument("--picos", type=float, default=0.5, help="segundos entre picos de temperatura")
    parser.add_argument("--espera", type=float, default=5, help="segundos de escuta após o fim do envio")
    parser.add_argument(
        "--metricas",
        default="http://localhost:9103/metrics",
        help="métricas de quem ingere o MQTT, separadas por vírgula (réplicas do central-ingestao)",
    )
    parser.add_argument("--sem-painel", action="store_true", help="não escuta o WebSocket (mede só a vazão)")
    sys.exit(0 if asyncio.run(principal(parser.parse_args())) else 1)


if __name__ == "__main__":
    main()

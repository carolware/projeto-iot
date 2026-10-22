"""
cadastrar_alerta_publico.py — demonstração do critério "domínio novo só por
configuração" (RNF-08, RF-CEN-01): cria pela API, sem nenhuma alteração de
código, o domínio `alerta_publico` nas prefeituras, o perfil do terminal de
alerta, um terminal na Defesa Civil e outro nos bombeiros de cada cidade e a
assinatura de alerta: incêndios de severidade alta ou crítica num raio de
80 km da cidade passam a chegar aos terminais da prefeitura.

Cada prefeitura é cadastrada pelo próprio gestor (gestor.<cidade>), que entra
pelo Keycloak; os terminais simulados recebem a credencial derivada de
SEGREDO_PROVISIONAMENTO, a mesma que o simulador em Go calcula.

Idempotente: rodar de novo não duplica nada (409 = já existe).

Uso:  python plataforma/simuladores/demo/cadastrar_alerta_publico.py [--central http://localhost:8000]
"""

import argparse
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # permite rodar como script

from nucleo.credenciais import derivar_senha  # noqa: E402

load_dotenv()
KEYCLOAK = os.getenv("KEYCLOAK_URL", "http://localhost:8080/realms/sentinela")
SENHA_DEMO = os.getenv("SENHA_USUARIOS_DEMO", "sentinela")  # usuários de demonstração do realm

# cidade → (lat, lon) do centro
PREFEITURAS = {
    "anapolis": (-16.3267, -48.9530),
    "formosa": (-15.5372, -47.3372),
    "pirenopolis": (-15.8558, -48.9597),
    "jaragua": (-15.7529, -49.3344),
}
NOMES = {"anapolis": "Anápolis", "formosa": "Formosa", "pirenopolis": "Pirenópolis", "jaragua": "Jaraguá"}
RAIO_KM = 80
TERMINAIS = {"defesa-civil": "dc", "bombeiros": "bm"}


def entrar(usuario: str) -> dict:
    """Token OIDC do usuário (cliente "cli", só para scripts de desenvolvimento)."""
    resposta = requests.post(
        f"{KEYCLOAK}/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "cli", "username": usuario, "password": SENHA_DEMO},
        timeout=10,
    )
    resposta.raise_for_status()
    return {"Authorization": f"Bearer {resposta.json()['access_token']}"}


def postar(central: str, rota: str, cabecalho: dict, corpo: dict) -> str:
    resposta = requests.post(
        f"{central}{rota}",
        json=corpo,
        headers=cabecalho,
        timeout=10,
    )
    if resposta.status_code == 409:
        return "já existia"
    resposta.raise_for_status()
    return "criado"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--central", default="http://localhost:8000")
    central = parser.parse_args().central.rstrip("/")

    segredo = os.getenv("SEGREDO_PROVISIONAMENTO")
    if not segredo:
        raise SystemExit("SEGREDO_PROVISIONAMENTO não definido (ver .env)")
    topicos = []
    for cidade, (lat, lon) in PREFEITURAS.items():
        inquilino = f"pref-{cidade}"
        token = entrar(f"gestor.{cidade}")
        print(f"── {inquilino} (como gestor.{cidade})")
        print("   domínio alerta_publico:", postar(central, "/api/v1/catalogo/dominios", token, {
            "slug": "alerta_publico",
            "nome": "Alerta público",
            "descricao": "Alertas de risco recebidos de outros inquilinos (ex.: incêndio na zona rural)",
        }))
        print("   perfil terminal-alerta:", postar(central, "/api/v1/catalogo/perfis", token, {
            "dominio": "alerta_publico",
            "slug": "terminal-alerta",
            "nome": "Terminal de alerta",
            "intervalo_esperado_s": 30,
            "grandezas": [],
        }))
        for site, sigla in TERMINAIS.items():
            slug = f"terminal-{sigla}-{cidade}"
            credencial = derivar_senha(segredo, slug)
            situacao = postar(central, "/api/v1/dispositivos", token, {
                "slug": slug,
                "nome": f"Terminal — {site.replace('-', ' ').title()}",
                "tipo": "terminal",
                "dominio": "alerta_publico",
                "perfil": "terminal-alerta",
                "site": site,
                "fabricante": "simulador",
                "firmware": "terminal_go",
                "credencial": credencial,
            })
            if situacao == "já existia":  # garante que o cadastro tem a credencial que o terminal usa
                requests.post(f"{central}/api/v1/dispositivos/{slug}/credencial", json={"credencial": credencial},
                              headers=token, timeout=10).raise_for_status()
                situacao = "já existia (credencial reprovisionada)"
            print(f"   {slug}:", situacao)
            topicos.append(f"{inquilino}/{site}/alerta_publico/{slug}")
        existentes = requests.get(
            f"{central}/api/v1/assinaturas", headers=token, timeout=10
        ).json()["itens"]
        if any(a["dominio"] == "incendio" for a in existentes):
            print("   assinatura incendio: já existia")
        else:
            print(f"   assinatura incendio ({RAIO_KM} km):", postar(central, "/api/v1/assinaturas", token, {
                "nome": f"Incêndios no entorno de {NOMES[cidade]}",
                "dominio": "incendio",
                "lat": lat,
                "lon": lon,
                "raio_km": RAIO_KM,
                "severidade_min": "alto",
            }))

    print("\nTERMINAIS=" + ",".join(topicos))


if __name__ == "__main__":
    main()

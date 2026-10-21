"""
cadastrar_alerta_publico.py — demonstração do critério "domínio novo só por
configuração" (RNF-08, RF-CEN-01): cria pela API, sem nenhuma alteração de
código, o domínio `alerta_publico` nas prefeituras, o perfil do terminal de
alerta, um terminal na Defesa Civil e outro nos bombeiros de cada cidade e a
assinatura de alerta: incêndios de severidade alta ou crítica num raio de
80 km da cidade passam a chegar aos terminais da prefeitura.

Idempotente: rodar de novo não duplica nada (409 = já existe).

Uso:  python plataforma/simuladores/demo/cadastrar_alerta_publico.py [--central http://localhost:8000]
"""

import argparse

import requests

# cidade → (lat, lon) do centro
PREFEITURAS = {
    "anapolis": (-16.3267, -48.9530),
    "formosa": (-15.5372, -47.3372),
    "pirenopolis": (-15.8558, -48.9597),
    "jaragua": (-15.7529, -49.3344),
}
RAIO_KM = 80
TERMINAIS = {"defesa-civil": "dc", "bombeiros": "bm"}


def postar(central: str, rota: str, inquilino: str, corpo: dict) -> str:
    resposta = requests.post(
        f"{central}{rota}",
        json=corpo,
        headers={"X-Inquilino": inquilino, "X-Usuario": "demo@sentinela"},
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

    topicos = []
    for cidade, (lat, lon) in PREFEITURAS.items():
        inquilino = f"pref-{cidade}"
        print(f"── {inquilino}")
        print("   domínio alerta_publico:", postar(central, "/api/v1/catalogo/dominios", inquilino, {
            "slug": "alerta_publico",
            "nome": "Alerta público",
            "descricao": "Alertas de risco recebidos de outros inquilinos (ex.: incêndio na zona rural)",
        }))
        print("   perfil terminal-alerta:", postar(central, "/api/v1/catalogo/perfis", inquilino, {
            "dominio": "alerta_publico",
            "slug": "terminal-alerta",
            "nome": "Terminal de alerta",
            "intervalo_esperado_s": 30,
            "grandezas": [],
        }))
        for site, sigla in TERMINAIS.items():
            slug = f"terminal-{sigla}-{cidade}"
            print(f"   {slug}:", postar(central, "/api/v1/dispositivos", inquilino, {
                "slug": slug,
                "nome": f"Terminal — {site.replace('-', ' ').title()}",
                "tipo": "terminal",
                "dominio": "alerta_publico",
                "perfil": "terminal-alerta",
                "site": site,
                "fabricante": "simulador",
                "firmware": "terminal_go",
            }))
            topicos.append(f"{inquilino}/{site}/alerta_publico/{slug}")
        existentes = requests.get(
            f"{central}/api/v1/assinaturas", headers={"X-Inquilino": inquilino}, timeout=10
        ).json()["itens"]
        if any(a["dominio"] == "incendio" for a in existentes):
            print("   assinatura incendio: já existia")
        else:
            print(f"   assinatura incendio ({RAIO_KM} km):", postar(central, "/api/v1/assinaturas", inquilino, {
                "nome": f"Incêndios no entorno de {cidade.title()}",
                "dominio": "incendio",
                "lat": lat,
                "lon": lon,
                "raio_km": RAIO_KM,
                "severidade_min": "alto",
            }))

    print("\nTERMINAIS=" + ",".join(topicos))


if __name__ == "__main__":
    main()

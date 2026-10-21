"""
cadastrar_alerta_publico.py — demonstração do critério "domínio novo só por
configuração" (RNF-08, RF-CEN-01): cria pela API, sem nenhuma alteração de
código, o domínio `alerta_publico` nas prefeituras, o perfil do terminal de
alerta e um terminal na Defesa Civil e outro nos bombeiros de cada cidade.

Idempotente: rodar de novo não duplica nada (409 = já existe).

Uso:  python plataforma/simuladores/demo/cadastrar_alerta_publico.py [--central http://localhost:8000]
"""

import argparse

import requests

PREFEITURAS = ("anapolis", "formosa", "pirenopolis", "jaragua")
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
    for cidade in PREFEITURAS:
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

    print("\nTERMINAIS=" + ",".join(topicos))


if __name__ == "__main__":
    main()

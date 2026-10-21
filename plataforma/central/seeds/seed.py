"""
seed.py — dados iniciais da demonstração. Idempotente: rodar de novo não
duplica nada (é executado a cada subida do contêiner da central).

- Rural: inquilino `brigada-cerrado`, domínio `incendio`, as 9 zonas como
  sites; por zona uma estação, um atuador da brigada e um conector FIRMS
  (todos simulados); e os nós de borda (fog).
- Urbano: as prefeituras e seus locais de resgate (prefeitura, Defesa Civil,
  bombeiros). O domínio `alerta_publico` e os terminais NÃO entram aqui: são
  criados pela API na demonstração, para provar que um domínio novo nasce só
  por configuração (RNF-08).

Uso:  python -m central.seeds.seed
"""

import logging

from sqlalchemy import Connection, Table, select
from sqlalchemy.dialects.postgresql import insert

from central import db
from central.config import configuracao
from central.db import criar_engine

log = logging.getLogger("central.seed")

# zona → (nome, lat, lon, borda)   — mesmas coordenadas da 1ª entrega
ZONAS = {
    "anapolis": ("Anápolis (GO)", -16.3267, -48.9530, "borda-goias"),
    "formosa": ("Formosa (GO)", -15.5372, -47.3372, "borda-goias"),
    "pirenopolis": ("Pirenópolis (GO)", -15.8558, -48.9597, "borda-goias"),
    "jaragua": ("Jaraguá (GO)", -15.7529, -49.3344, "borda-goias"),
    "sandolandia": ("Sandolândia (TO)", -12.5408, -49.9192, "borda-tocantins"),
    "mateiros": ("Mateiros (TO)", -10.5464, -46.4168, "borda-tocantins"),
    "lagoa-da-confusao": ("Lagoa da Confusão (TO)", -10.7906, -49.6199, "borda-tocantins"),
    "novo-progresso": ("Novo Progresso (PA)", -7.1261, -55.3853, None),
    "mirador": ("Mirador (MA)", -6.3745, -44.3683, None),
}

BORDAS = {
    "borda-tocantins": "Nó de borda — Tocantins (Jalapão)",
    "borda-goias": "Nó de borda — Goiás",
}

# prefeitura → (nome, lat, lon) do centro; os locais de resgate ficam próximos
PREFEITURAS = {
    "pref-anapolis": ("Prefeitura de Anápolis", -16.3267, -48.9530),
    "pref-formosa": ("Prefeitura de Formosa", -15.5372, -47.3372),
    "pref-pirenopolis": ("Prefeitura de Pirenópolis", -15.8558, -48.9597),
    "pref-jaragua": ("Prefeitura de Jaraguá", -15.7529, -49.3344),
}
LOCAIS_RESGATE = {
    "prefeitura": ("Sede da prefeitura", 0.0, 0.0),
    "defesa-civil": ("Defesa Civil", 0.008, -0.006),
    "bombeiros": ("Corpo de Bombeiros", -0.010, 0.007),
}

# perfil → (nome, intervalo esperado em s, tipo de dispositivo, prefixo do slug, grandezas)
PERFIS_RURAIS = {
    "estacao-incendio": (
        "Estação de detecção de incêndio",
        10,
        "estacao",
        "est-{zona}-01",
        [("temperatura", "Cel", -10, 60), ("umidade", "%", 0, 100), ("fumaca", "1", 0, 100)],
    ),
    "atuador-brigada": ("Atuador da brigada local", 30, "atuador", "atuador-{zona}-01", []),
    "conector-firms": (
        "Conector NASA FIRMS (focos de calor por satélite)",
        600,
        "conector",
        "firms-{zona}",
        [("focos", "1", 0, None), ("frp_max", "MW", 0, None), ("distancia_km", "km", 0, None)],
    ),
}


def _garantir(conexao: Connection, tabela: Table, chave: dict, valores: dict | None = None):
    """INSERT ... ON CONFLICT DO NOTHING e devolve o id (novo ou existente)."""
    conexao.execute(insert(tabela).values(**chave, **(valores or {})).on_conflict_do_nothing())
    filtro = [tabela.c[k] == v for k, v in chave.items()]
    return conexao.execute(select(tabela.c.id).where(*filtro)).scalar_one()


def semear(conexao: Connection) -> None:
    # ── Rural ──
    brigada = _garantir(
        conexao, db.inquilino, {"slug": "brigada-cerrado"}, {"nome": "Brigada do Cerrado", "cenario": "rural"}
    )
    incendio = _garantir(
        conexao,
        db.dominio,
        {"inquilino_id": brigada, "slug": "incendio"},
        {"nome": "Incêndio florestal", "descricao": "Detecção de risco de incêndio por estações e satélite"},
    )
    perfis = {}
    for slug, (nome, intervalo, _tipo, _padrao, grandezas) in PERFIS_RURAIS.items():
        perfis[slug] = _garantir(
            conexao,
            db.perfil_dispositivo,
            {"dominio_id": incendio, "slug": slug},
            {"nome": nome, "intervalo_esperado_s": intervalo},
        )
        for grandeza, unidade, minimo, maximo in grandezas:
            conexao.execute(
                insert(db.perfil_grandeza)
                .values(perfil_id=perfis[slug], grandeza=grandeza, unidade=unidade, valor_min=minimo, valor_max=maximo)
                .on_conflict_do_nothing()
            )
    for slug, nome in BORDAS.items():
        _garantir(conexao, db.no_borda, {"slug": slug}, {"inquilino_id": brigada, "nome": nome, "versao": "0.1.0"})
    for zona, (nome, lat, lon, _borda) in ZONAS.items():
        site = _garantir(
            conexao,
            db.site,
            {"inquilino_id": brigada, "slug": zona},
            {"nome": nome, "lat": lat, "lon": lon, "raio_km": 16.7},
        )
        for perfil, (nome_perfil, _intervalo, tipo, padrao, _grandezas) in PERFIS_RURAIS.items():
            _garantir(
                conexao,
                db.dispositivo,
                {"slug": padrao.format(zona=zona)},
                {
                    "inquilino_id": brigada,
                    "perfil_id": perfis[perfil],
                    "site_id": site,
                    "nome": f"{nome_perfil.split(' (')[0]} — {nome}",
                    "tipo": tipo,
                    "fabricante": "simulador",
                },
            )

    # ── Urbano ──
    for slug, (nome, lat, lon) in PREFEITURAS.items():
        prefeitura = _garantir(conexao, db.inquilino, {"slug": slug}, {"nome": nome, "cenario": "urbano"})
        for local, (nome_local, dlat, dlon) in LOCAIS_RESGATE.items():
            _garantir(
                conexao,
                db.site,
                {"inquilino_id": prefeitura, "slug": local},
                {"nome": nome_local, "lat": round(lat + dlat, 4), "lon": round(lon + dlon, 4)},
            )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [seed] %(message)s", datefmt="%H:%M:%S")
    engine = criar_engine(configuracao().database_url)
    with engine.begin() as conexao:
        semear(conexao)
    engine.dispose()
    log.info("Seed aplicado: 1 inquilino rural (%d zonas), %d prefeituras", len(ZONAS), len(PREFEITURAS))


if __name__ == "__main__":
    main()

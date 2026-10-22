"""
zonas.py — tabela única das zonas simuladas (antes duplicada em sensor.py,
gateway.py, mock_incendio.py, iot.ts e sim.ts).

Cada zona é um site do inquilino rural `brigada-cerrado`. A região diz por
onde os dispositivos dela falam com a plataforma:
- "tocantins" / "goias": atrás de um nó de borda (fog), via broker local;
- None: conexão direta com a central (sem fog).
"""

import os
from dataclasses import dataclass

INQUILINO = "brigada-cerrado"
DOMINIO = "incendio"


@dataclass(frozen=True)
class Zona:
    slug: str
    nome: str
    lat: float
    lon: float
    regiao: str | None

    @property
    def estacao(self) -> str:
        return f"est-{self.slug}-01"

    @property
    def atuador(self) -> str:
        return f"atuador-{self.slug}-01"

    @property
    def conector_firms(self) -> str:
        return f"firms-{self.slug}"


ZONAS: dict[str, Zona] = {
    z.slug: z
    for z in (
        Zona("anapolis", "Anápolis (GO)", -16.3267, -48.9530, "goias"),
        Zona("formosa", "Formosa (GO)", -15.5372, -47.3372, "goias"),
        Zona("pirenopolis", "Pirenópolis (GO)", -15.8558, -48.9597, "goias"),
        Zona("jaragua", "Jaraguá (GO)", -15.7529, -49.3344, "goias"),
        Zona("sandolandia", "Sandolândia (TO)", -12.5408, -49.9192, "tocantins"),
        Zona("mateiros", "Mateiros (TO)", -10.5464, -46.4168, "tocantins"),
        Zona("lagoa-da-confusao", "Lagoa da Confusão (TO)", -10.7906, -49.6199, "tocantins"),
        Zona("novo-progresso", "Novo Progresso (PA)", -7.1261, -55.3853, None),
        Zona("mirador", "Mirador (MA)", -6.3745, -44.3683, None),
    )
}

# Broker de cada região visto da máquina do desenvolvedor (portas TLS publicadas pelo compose)
BROKER_NA_MAQUINA = {
    "tocantins": ("localhost", int(os.getenv("MQTT_PORTA_BORDA_TOCANTINS", "8884"))),
    "goias": ("localhost", int(os.getenv("MQTT_PORTA_BORDA_GOIAS", "8885"))),
    None: ("localhost", int(os.getenv("MQTT_PORTA_CENTRAL", "8883"))),
}

# Canal de controle da simulação (não faz parte da plataforma)
TOPICO_MOCK = "simulacao/mock"


def zonas_do_ambiente() -> list[Zona]:
    """Zonas atendidas por este processo (variável ZONAS, separadas por vírgula; vazio = todas)."""
    nomes = [n.strip() for n in os.getenv("ZONAS", "").split(",") if n.strip()]
    desconhecidas = [n for n in nomes if n not in ZONAS]
    if desconhecidas:
        raise SystemExit(f"ZONAS desconhecidas: {desconhecidas} (opções: {', '.join(ZONAS)})")
    return [ZONAS[n] for n in nomes] if nomes else list(ZONAS.values())

"""
nucleo — domínio compartilhado entre a instância central (cloud) e os nós de
borda (fog). Não conhece protocolo, banco nem cenário (arquitetura hexagonal,
§7.1 do documento de requisitos): só a mensagem canônica, a convenção de
tópicos, a validação contra o contrato e utilidades geográficas.
"""

from nucleo.geo import dentro_do_raio, distancia_km
from nucleo.mensagem import Medida, Mensagem, nova_mensagem
from nucleo.topicos import Topico, montar_topico, parsear_topico
from nucleo.validacao import ErroValidacao, validar_mensagem

__all__ = [
    "ErroValidacao",
    "Medida",
    "Mensagem",
    "Topico",
    "dentro_do_raio",
    "distancia_km",
    "montar_topico",
    "nova_mensagem",
    "parsear_topico",
    "validar_mensagem",
]

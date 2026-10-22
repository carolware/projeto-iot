"""
validacao.py — valida mensagens contra o contrato canônico (RF-ING-06).

O schema é lido de `contratos/` (contrato primeiro, §7.3); o diretório pode
ser sobrescrito por CONTRATOS_DIR quando o pacote roda fora do repositório.

Dois validadores do mesmo schema: o jsonschema-rs (compilado, ~20x mais
rápido) responde "é válida?" para cada mensagem; só as inválidas passam pelo
jsonschema, que monta os erros detalhados por campo (RNF-01).
"""

import json
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import jsonschema_rs
from jsonschema import Draft202012Validator

from nucleo.mensagem import parsear_timestamp
from nucleo.topicos import Topico

SCHEMA_V1 = "mensagem-canonica.v1.schema.json"


@dataclass(frozen=True)
class ErroValidacao:
    campo: str
    motivo: str

    def __str__(self) -> str:
        return f"{self.campo}: {self.motivo}"


def diretorio_contratos() -> Path:
    return Path(os.getenv("CONTRATOS_DIR", Path(__file__).resolve().parents[1] / "contratos"))


@cache
def _schema() -> dict:
    schema = json.loads((diretorio_contratos() / SCHEMA_V1).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


@cache
def _validador() -> Draft202012Validator:
    return Draft202012Validator(_schema())


@cache
def _validador_rapido():
    return jsonschema_rs.Draft202012Validator(_schema())


def validar_mensagem(bruto: object, topico: Topico | None = None) -> list[ErroValidacao]:
    """
    Retorna a lista de erros (vazia = válida). Além do schema, confere que o
    timestamp é uma data real e, se o tópico for informado, que o dispositivo
    e o tipo do tópico batem com os da mensagem.
    """
    if _validador_rapido().is_valid(bruto):
        erros = []
    else:
        erros = [
            ErroValidacao("/".join(str(p) for p in erro.absolute_path) or "(raiz)", erro.message)
            for erro in sorted(_validador().iter_errors(bruto), key=lambda e: list(e.absolute_path))
        ]
    if erros or not isinstance(bruto, dict):
        return erros

    try:
        parsear_timestamp(bruto["timestamp"])
    except ValueError as exc:
        erros.append(ErroValidacao("timestamp", str(exc)))

    if topico is not None:
        if topico.dispositivo != bruto["dispositivo"]:
            erros.append(ErroValidacao("dispositivo", f"difere do tópico ({topico.dispositivo})"))
        if topico.tipo != bruto["tipo"]:
            erros.append(ErroValidacao("tipo", f"difere do tópico ({topico.tipo})"))
    return erros

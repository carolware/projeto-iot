"""
logs.py — logs estruturados (RF-OBS-02): uma linha JSON por evento, com o
`id_mensagem` como identificador de correlação. Uma leitura pode ser seguida
da estação à fila da borda, à sincronização, à ingestão, à regra e ao alarme
na central:

    docker compose logs borda-goias central | grep <id_mensagem>

Campos extras entram com `log.info("...", extra={"id_mensagem": ..., "dispositivo": ...})`.

LOG_FORMATO=json (padrão nos contêineres) ou texto; LOG_NIVEL=INFO (DEBUG
registra cada mensagem individualmente).
"""

import json
import logging
import os
from datetime import UTC, datetime

# atributos padrão de LogRecord — o resto vem de extra={...}
_PADRAO = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}


class FormatoJson(logging.Formatter):
    def __init__(self, servico: str):
        super().__init__()
        self.servico = servico

    def format(self, record: logging.LogRecord) -> str:
        linha = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "nivel": record.levelname,
            "servico": self.servico,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        linha |= {k: v for k, v in vars(record).items() if k not in _PADRAO and not k.startswith("_")}
        if record.exc_info:
            linha["erro"] = self.formatException(record.exc_info)
        return json.dumps(linha, ensure_ascii=False, default=str)


class FormatoTexto(logging.Formatter):
    """Legível no terminal; os campos de correlação aparecem no fim da linha."""

    def __init__(self, servico: str):
        super().__init__(f"%(asctime)s [{servico}] %(levelname)-8s %(message)s", datefmt="%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        texto = super().format(record)
        extras = {k: v for k, v in vars(record).items() if k not in _PADRAO and not k.startswith("_")}
        return f"{texto}  {extras}" if extras else texto


def configurar_logs(servico: str) -> None:
    formato = os.getenv("LOG_FORMATO", "texto").lower()
    manipulador = logging.StreamHandler()
    manipulador.setFormatter(FormatoJson(servico) if formato == "json" else FormatoTexto(servico))
    raiz = logging.getLogger()
    raiz.handlers[:] = [manipulador]
    raiz.setLevel(os.getenv("LOG_NIVEL", "INFO").upper())
    for proprio in ("uvicorn", "uvicorn.error", "uvicorn.access"):  # o uvicorn traz handlers de texto próprios
        logging.getLogger(proprio).handlers.clear()
        logging.getLogger(proprio).propagate = True
    for ruidoso in ("httpx", "httpcore", "urllib3", "multipart"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)

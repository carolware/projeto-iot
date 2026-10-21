"""
fila.py — fila persistente do nó de borda (store-and-forward, RF-EDG-01).

- SQLite em modo WAL com synchronous=FULL: uma queda de energia não corrompe
  a fila nem perde o que já foi confirmado ao dispositivo (RNF-07, RNF-10).
- Payload cifrado com Fernet (AES-128-CBC + HMAC): o disco retirado do nó
  não revela as leituras guardadas (RF-SEG-05).
- Idempotente por id_mensagem: o mesmo envio repetido pelo dispositivo não
  entra duas vezes.
- Prioridade: alarmes saem antes da telemetria rotineira (RF-EDG-05).
"""

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

PRIORIDADE_ALARME = 10
PRIORIDADE_ESTADO = 1
PRIORIDADE_TELEMETRIA = 0

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS fila (
    id_mensagem  TEXT PRIMARY KEY,
    prioridade   INTEGER NOT NULL,
    medido_em    TEXT NOT NULL,
    criado_em    REAL NOT NULL,
    payload      BLOB NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente', 'enviada', 'recusada')),
    enviado_em   REAL,
    motivo       TEXT
);
CREATE INDEX IF NOT EXISTS fila_envio ON fila (status, prioridade DESC, medido_em);

CREATE TABLE IF NOT EXISTS invalidas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    recebido_em REAL NOT NULL,
    origem      TEXT NOT NULL,
    payload     BLOB NOT NULL,
    motivo      TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ItemFila:
    id_mensagem: str
    prioridade: int
    mensagem: dict


class FilaLocal:
    def __init__(self, caminho: str | Path, chave: bytes):
        self.cifra = Fernet(chave)
        self._lock = threading.Lock()
        self._conexao = sqlite3.connect(str(caminho), check_same_thread=False, isolation_level=None)
        self._conexao.execute("PRAGMA journal_mode=WAL")
        self._conexao.execute("PRAGMA synchronous=FULL")
        self._conexao.executescript(_ESQUEMA)

    # ── escrita ────────────────────────────────────────────────────────────

    def enfileirar(self, mensagem: dict, prioridade: int = PRIORIDADE_TELEMETRIA) -> bool:
        """Guarda a mensagem; devolve False se o id já estava na fila (reenvio)."""
        with self._lock, self._transacao():
            cursor = self._conexao.execute(
                "INSERT OR IGNORE INTO fila (id_mensagem, prioridade, medido_em, criado_em, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (mensagem["id_mensagem"], prioridade, mensagem["timestamp"], time.time(), self._cifrar(mensagem)),
            )
        return cursor.rowcount == 1

    def registrar_invalida(self, origem: str, payload: bytes | str, motivo: str) -> None:
        dados = payload.encode() if isinstance(payload, str) else payload
        with self._lock, self._transacao():
            self._conexao.execute(
                "INSERT INTO invalidas (recebido_em, origem, payload, motivo) VALUES (?, ?, ?, ?)",
                (time.time(), origem, self.cifra.encrypt(dados), motivo),
            )

    def marcar_enviadas(self, ids: list[str]) -> None:
        self._marcar(ids, "enviada", None)

    def marcar_recusadas(self, motivos: dict[str, str]) -> None:
        with self._lock, self._transacao():
            self._conexao.executemany(
                "UPDATE fila SET status = 'recusada', enviado_em = ?, motivo = ? WHERE id_mensagem = ?",
                [(time.time(), motivo, id_) for id_, motivo in motivos.items()],
            )

    def limpar_enviadas(self, mais_antigas_que_s: float) -> int:
        """Remove o que a central já confirmou há mais de N segundos (mantém o disco do nó pequeno)."""
        with self._lock, self._transacao():
            cursor = self._conexao.execute(
                "DELETE FROM fila WHERE status = 'enviada' AND enviado_em < ?", (time.time() - mais_antigas_que_s,)
            )
        return cursor.rowcount

    # ── leitura ────────────────────────────────────────────────────────────

    def proximas(self, limite: int) -> list[ItemFila]:
        """Pendentes em ordem de envio: prioridade maior primeiro e, dentro dela, pelo horário da medição."""
        with self._lock:
            linhas = self._conexao.execute(
                "SELECT id_mensagem, prioridade, payload FROM fila WHERE status = 'pendente' "
                "ORDER BY prioridade DESC, medido_em LIMIT ?",
                (limite,),
            ).fetchall()
        return [ItemFila(id_, prioridade, self._decifrar(payload)) for id_, prioridade, payload in linhas]

    def contagem(self) -> dict[str, int]:
        with self._lock:
            linhas = self._conexao.execute("SELECT status, count(*) FROM fila GROUP BY status").fetchall()
        return {"pendente": 0, "enviada": 0, "recusada": 0} | dict(linhas)

    def fechar(self) -> None:
        self._conexao.close()

    # ── internos ───────────────────────────────────────────────────────────

    def _marcar(self, ids: list[str], status: str, motivo: str | None) -> None:
        if not ids:
            return
        with self._lock, self._transacao():
            self._conexao.executemany(
                "UPDATE fila SET status = ?, enviado_em = ?, motivo = ? WHERE id_mensagem = ?",
                [(status, time.time(), motivo, id_) for id_ in ids],
            )

    def _transacao(self):
        return _Transacao(self._conexao)

    def _cifrar(self, mensagem: dict) -> bytes:
        return self.cifra.encrypt(json.dumps(mensagem, ensure_ascii=False).encode())

    def _decifrar(self, payload: bytes) -> dict:
        return json.loads(self.cifra.decrypt(payload))


class _Transacao:
    """BEGIN IMMEDIATE … COMMIT (ou ROLLBACK em erro): escrita atômica mesmo com queda de energia."""

    def __init__(self, conexao: sqlite3.Connection):
        self.conexao = conexao

    def __enter__(self):
        self.conexao.execute("BEGIN IMMEDIATE")

    def __exit__(self, tipo, valor, rastro):
        self.conexao.execute("ROLLBACK" if tipo else "COMMIT")
        return False

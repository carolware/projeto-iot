"""
credenciais.py — credenciais de dispositivos e serviços (RF-DIS-02, RF-SEG-04/05).

- Hash no formato do Mosquitto (`$7$` = PBKDF2-HMAC-SHA512, 101 iterações,
  salt de 12 bytes): a plataforma guarda só o hash, e a mesma linha serve
  para o banco, para a verificação HTTP e para o password_file dos brokers.
- Credencial nova: token aleatório, mostrado uma única vez.
- Provisionamento dos dispositivos SIMULADOS: sem fábrica para gravar a senha
  no aparelho, ela é derivada de um segredo de provisionamento (HMAC) que só a
  central e os simuladores conhecem. Dispositivos cadastrados pela API recebem
  credencial aleatória.
"""

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

ITERACOES_MOSQUITTO = 101


def gerar_credencial() -> str:
    return secrets.token_urlsafe(24)


def derivar_senha(segredo: str, identidade: str) -> str:
    """Senha de um dispositivo simulado, a partir do segredo de provisionamento."""
    digest = hmac.new(segredo.encode(), f"dispositivo:{identidade}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode()[:32]


def hash_senha(senha: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(12)
    digest = hashlib.pbkdf2_hmac("sha512", senha.encode(), salt, ITERACOES_MOSQUITTO)
    return f"$7${ITERACOES_MOSQUITTO}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verificar_senha(senha: str, codificado: str | None) -> bool:
    """Compara em tempo constante; hash ausente ou malformado nunca confere."""
    if not codificado:
        return False
    try:
        _, tipo, iteracoes, salt, digest = codificado.split("$")
        if tipo != "7":
            return False
        calculado = hashlib.pbkdf2_hmac("sha512", senha.encode(), base64.b64decode(salt), int(iteracoes))
        return hmac.compare_digest(calculado, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def gravar_se_mudou(diretorio: Path, senhas: str, acl: str) -> bool:
    """Troca atômica dos arquivos; só escreve se mudou (o broker recarrega ao detectar a mudança)."""
    diretorio.mkdir(parents=True, exist_ok=True)
    mudou = False
    for nome, conteudo in (("senhas", senhas), ("acl", acl)):
        destino = diretorio / nome
        if destino.exists() and destino.read_text() == conteudo:
            continue
        temporario = destino.with_suffix(".tmp")
        temporario.write_text(conteudo)
        os.chmod(temporario, 0o600)
        temporario.replace(destino)
        mudou = True
    return mudou

"""
gerar.py — CA própria da plataforma e certificados de servidor dos brokers
(TLS 1.2+ em toda interface MQTT, RNF-11). Idempotente: só cria o que falta.

Roda como serviço de inicialização do compose (`pki`), antes dos brokers:

    python gerar.py --saida /pki mosquitto-central mosquitto-borda-tocantins ...

Cada certificado vale para o nome do serviço na rede do Docker e para
`localhost` (scripts e testes na máquina). Os clientes verificam o broker com
`ca.crt`; a chave da CA nunca sai do diretório gerado (fora do git).
"""

import argparse
import datetime as dt
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

UID_MOSQUITTO = 1883  # usuário do processo mosquitto nas imagens eclipse-mosquitto


def _nome(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Sentinela"), x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _gravar_chave(caminho: Path, chave) -> None:
    caminho.write_bytes(
        chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    os.chmod(caminho, 0o640)
    try:
        os.chown(caminho, UID_MOSQUITTO, UID_MOSQUITTO)  # o broker relê a chave como usuário mosquitto
    except PermissionError:
        pass


def autoridade(saida: Path):
    crt, key = saida / "ca.crt", saida / "ca.key"
    if crt.exists() and key.exists():
        return x509.load_pem_x509_certificate(crt.read_bytes()), serialization.load_pem_private_key(
            key.read_bytes(), None
        )
    chave = ec.generate_private_key(ec.SECP256R1())
    agora = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_nome("Sentinela CA"))
        .issuer_name(_nome("Sentinela CA"))
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - dt.timedelta(minutes=5))
        .not_valid_after(agora + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True
        )  # key_cert_sign + crl_sign
        .sign(chave, hashes.SHA256())
    )
    _gravar_chave(key, chave)
    os.chmod(key, 0o600)
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    os.chmod(crt, 0o644)
    print("CA criada")
    return cert, chave


def servidor(saida: Path, nome: str, ca_cert, ca_chave) -> None:
    crt, key = saida / f"{nome}.crt", saida / f"{nome}.key"
    if crt.exists() and key.exists():
        atual = x509.load_pem_x509_certificate(crt.read_bytes())
        if atual.issuer == ca_cert.subject and atual.not_valid_after_utc > dt.datetime.now(dt.UTC) + dt.timedelta(days=30):
            return
    chave = ec.generate_private_key(ec.SECP256R1())
    agora = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_nome(nome))
        .issuer_name(ca_cert.subject)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - dt.timedelta(minutes=5))
        .not_valid_after(agora + dt.timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(nome), x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(ca_chave, hashes.SHA256())
    )
    _gravar_chave(key, chave)
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    os.chmod(crt, 0o644)
    print(f"certificado de {nome} criado")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera a CA e os certificados dos brokers")
    parser.add_argument("--saida", default="/pki")
    parser.add_argument("nomes", nargs="+", help="nomes dos brokers (DNS na rede do Docker)")
    args = parser.parse_args()
    saida = Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)
    ca_cert, ca_chave = autoridade(saida)
    for nome in args.nomes:
        servidor(saida, nome, ca_cert, ca_chave)
    print("PKI pronta em", saida)


if __name__ == "__main__":
    main()

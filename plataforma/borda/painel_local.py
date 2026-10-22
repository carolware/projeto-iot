"""
painel_local.py — servidor HTTP do nó de borda, acessível na rede do campo.

- POST /api/v1/ingestao (RF-EDG-01): mesma mensagem canônica da central, para
  aparelhos que não falam MQTT. Autenticação HTTP Basic com a credencial do
  próprio dispositivo, conferida contra as senhas/ACL que a borda já baixa da
  central para o broker local — funciona sem link. O tópico (inquilino, site,
  domínio) sai da ACL do dispositivo, então ninguém publica em nome de outro.
- GET / e GET /estado (RF-EDG-07): painel do técnico de campo com últimas
  leituras, alarmes locais, fila e link. Senha local (BORDA_PAINEL_SENHA);
  sem senha definida, o painel fica desligado.

Servidor da biblioteca padrão (uma thread por requisição): o volume é o de um
campo, não o da central.
"""

import base64
import binascii
import html
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from nucleo.credenciais import verificar_senha
from nucleo.topicos import parsear_topico

log = logging.getLogger("borda.http")

TIPOS_INGESTAO = ("telemetria", "evento", "estado")
LIMITE_CORPO = 256 * 1024


class AcessoLocal:
    """Senhas e ACL do broker local, relidas quando a sincronização troca os arquivos."""

    def __init__(self, diretorio: Path):
        self.diretorio = Path(diretorio)
        self._versao: tuple | None = None
        self._senhas: dict[str, str] = {}
        self._bases: dict[str, str] = {}

    def _recarregar(self) -> None:
        arquivos = (self.diretorio / "senhas", self.diretorio / "acl")
        try:
            versao = tuple(a.stat().st_mtime_ns for a in arquivos)
        except FileNotFoundError:
            self._senhas, self._bases, self._versao = {}, {}, None
            return
        if versao == self._versao:
            return
        senhas = dict(linha.split(":", 1) for linha in arquivos[0].read_text().splitlines() if ":" in linha)
        bases, usuario = {}, None
        for linha in arquivos[1].read_text().splitlines():
            if linha.startswith("user "):
                usuario = linha[5:].strip()
            elif linha.startswith("topic write ") and linha.endswith("/telemetria") and usuario:
                bases[usuario] = linha[len("topic write ") : -len("/telemetria")]
        self._senhas, self._bases, self._versao = senhas, bases, versao

    def autenticar(self, usuario: str, senha: str) -> str | None:
        """Devolve a base do tópico do dispositivo ({inquilino}/{site}/{dominio}/{slug}) ou None."""
        self._recarregar()
        base = self._bases.get(usuario)
        if base and verificar_senha(senha, self._senhas.get(usuario)):
            return base
        return None


def _credenciais(cabecalho: str | None) -> tuple[str, str] | None:
    if not cabecalho or not cabecalho.startswith("Basic "):
        return None
    try:
        usuario, _, senha = base64.b64decode(cabecalho[6:]).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return usuario, senha


def estado_do_no(no, identificacao: dict) -> dict:
    agora = time.time()
    link = no.link
    return {
        **identificacao,
        "link": {
            "online": link.online,
            "ultimo_sync_ha_s": round(agora - link.ultimo_sync) if link.ultimo_sync else None,
            "ultimo_erro": link.ultimo_erro,
        },
        "fila": no.fila.contagem(),
        "regras_locais": [r.nome for r in no.decisor.regras],
        "ultimas_leituras": dict(sorted(no.ultimas_leituras.items())),
        "alarmes_recentes": list(no.alarmes_recentes),
    }


def _pagina(estado: dict) -> str:
    e = html.escape
    link = estado["link"]
    situacao = {True: "online", False: "SEM LINK", None: "aguardando"}[link["online"]]
    sync = f"há {link['ultimo_sync_ha_s']} s" if link["ultimo_sync_ha_s"] is not None else "nunca"
    leituras = (
        "".join(
            f"<tr><td>{e(slug)}</td><td>{e(leitura['site'])}</td><td>{e(leitura['timestamp'])}</td><td>"
            + ", ".join(f"{e(m['grandeza'])} {m['valor']} {e(m.get('unidade', ''))}" for m in leitura["medidas"])
            + "</td></tr>"
            for slug, leitura in estado["ultimas_leituras"].items()
        )
        or '<tr><td colspan="4">nenhuma leitura desde que o nó iniciou</td></tr>'
    )
    alarmes = (
        "".join(
            f"<tr><td>{e(a['quando'])}</td><td>{e(a['site'])}</td><td>{e(a['severidade'])}</td>"
            f"<td>{e(a['regra'])}</td><td>{e(', '.join(a['comandos']) or '—')}</td></tr>"
            for a in estado["alarmes_recentes"]
        )
        or '<tr><td colspan="5">nenhum alarme local</td></tr>'
    )
    fila = estado["fila"]
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="5">
<title>{e(estado["borda"])} — painel local</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 16px; background: #fff; color: #222; }}
 table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; font-size: 14px; }}
 th, td {{ border-bottom: 1px solid #ddd; padding: 4px 6px; text-align: left; }}
 .online {{ color: #1a7f37; }} .offline {{ color: #c62828; font-weight: bold; }}
 .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }}
 .cards div {{ border: 1px solid #ddd; border-radius: 6px; padding: 8px 12px; }}
</style></head><body>
<h1>{e(estado["borda"])} <small>v{e(estado["versao"])}</small></h1>
<p>Sites: {e(", ".join(estado["sites"]))} · atualiza a cada 5 s</p>
<div class="cards">
 <div>Link com a central: <b class="{"online" if link["online"] else "offline"}">{situacao}</b>
  <br>última sincronização {sync}</div>
 <div>Fila: <b>{fila["pendente"]}</b> pendentes<br>{fila["enviada"]} enviadas · {fila["recusada"]} recusadas</div>
 <div>Regras locais:<br>{e(", ".join(estado["regras_locais"]) or "nenhuma")}</div>
</div>
<h2>Alarmes locais recentes</h2>
<table><tr><th>Quando</th><th>Site</th><th>Severidade</th><th>Regra</th><th>Comandos</th></tr>{alarmes}</table>
<h2>Últimas leituras</h2>
<table><tr><th>Dispositivo</th><th>Site</th><th>Medido em</th><th>Medidas</th></tr>{leituras}</table>
</body></html>"""


def criar_servidor(no, acesso: AcessoLocal, senha_painel: str, porta: int, identificacao: dict) -> ThreadingHTTPServer:
    sites = set(identificacao["sites"])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, formato, *args):  # o log estruturado do nó já registra o que importa
            log.debug(formato, *args)

        def _responder(self, status: int, corpo: dict | str, extra: dict | None = None) -> None:
            dados = corpo.encode() if isinstance(corpo, str) else json.dumps(corpo, ensure_ascii=False).encode()
            self.send_response(status)
            tipo = "text/html; charset=utf-8" if isinstance(corpo, str) else "application/json"
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(dados)))
            for chave, valor in (extra or {}).items():
                self.send_header(chave, valor)
            self.end_headers()
            self.wfile.write(dados)

        def _negar(self, motivo: str) -> None:
            self._responder(401, {"erro": motivo}, {"WWW-Authenticate": 'Basic realm="borda"'})

        def do_GET(self):
            if self.path not in ("/", "/estado"):
                return self._responder(404, {"erro": "não encontrado"})
            if not senha_painel:
                return self._responder(403, {"erro": "painel local desligado (defina BORDA_PAINEL_SENHA)"})
            credenciais = _credenciais(self.headers.get("Authorization"))
            if credenciais is None or credenciais[1] != senha_painel:
                return self._negar("senha do painel local")
            estado = estado_do_no(no, identificacao)
            self._responder(200, _pagina(estado) if self.path == "/" else estado)

        def do_POST(self):
            if self.path != "/api/v1/ingestao":
                return self._responder(404, {"erro": "não encontrado"})
            credenciais = _credenciais(self.headers.get("Authorization"))
            base = acesso.autenticar(*credenciais) if credenciais else None
            if base is None:
                return self._negar("credencial do dispositivo")
            tamanho = int(self.headers.get("Content-Length") or 0)
            if tamanho > LIMITE_CORPO:
                return self._responder(413, {"erro": f"corpo acima de {LIMITE_CORPO} bytes"})
            payload = self.rfile.read(tamanho)
            origem = f"http:{credenciais[0]}"
            try:
                bruto = json.loads(payload)
            except (json.JSONDecodeError, UnicodeDecodeError):
                no._recusar(origem, payload, "payload não é JSON")
                return self._responder(400, {"status": "recusada", "erros": ["payload não é JSON"]})
            tipo = bruto.get("tipo") if isinstance(bruto, dict) else None
            topico = parsear_topico(f"{base}/{tipo if tipo in TIPOS_INGESTAO else 'telemetria'}")
            if topico is None or topico.site not in sites:
                return self._responder(403, {"erro": "dispositivo de site não atendido por este nó"})
            status, erros = no.processar(bruto, topico, no.cliente, origem, payload)
            corpo = {"status": status, "id_mensagem": bruto.get("id_mensagem"), "erros": erros}
            self._responder(400 if status == "recusada" else 202, corpo)

    return ThreadingHTTPServer(("0.0.0.0", porta), Handler)


def iniciar_servidor_local(
    no, acesso: AcessoLocal, senha_painel: str, porta: int, identificacao: dict
) -> ThreadingHTTPServer:
    servidor = criar_servidor(no, acesso, senha_painel, porta, identificacao)
    threading.Thread(target=servidor.serve_forever, name="http-local", daemon=True).start()
    log.info("HTTP local na porta %d (ingestão; painel %s)", porta, "ativo" if senha_painel else "desligado")
    return servidor

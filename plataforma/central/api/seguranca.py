"""
seguranca.py — identidade e acesso na API (RF-SEG-01/02/03, RF-SEG-05).

Pessoas: token OIDC emitido pelo Keycloak (Authorization: Bearer). O token
traz os papéis (realm_access.roles) e o inquilino (claim `inquilino`). Só o
administrador da plataforma pode agir sobre qualquer inquilino, escolhendo-o
no cabeçalho X-Inquilino; os demais ficam presos ao inquilino do token.

    papel       pode
    leitor      consultar
    operador    + reconhecer/resolver alarmes, enviar comandos, gêmeo
    integrador  + cadastrar dispositivos e perfis, credenciais, ingestão HTTP
    gestor      tudo dentro do inquilino (catálogo, regras, assinaturas, auditoria)
    admin       tudo, em qualquer inquilino; inquilinos e fila de inválidas

Máquinas: nós de borda e dispositivos que usam HTTP se autenticam com a
própria credencial (HTTP Basic: slug + credencial), verificada contra o hash.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials, HTTPBearer
from sqlalchemy import select

from central import db
from central.api.comum import Banco, ContextoInquilino
from nucleo.credenciais import verificar_senha
from nucleo.ingestao import DispositivoCadastrado

PAPEIS = ("admin", "gestor", "operador", "integrador", "leitor")

_bearer = HTTPBearer(auto_error=False, description="Token OIDC do Keycloak")
_basic = HTTPBasic(auto_error=False, description="Nó de borda ou dispositivo: slug + credencial")


# ──────────────────────────────────────────────
# Pessoas (OIDC)
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class Usuario:
    nome: str
    inquilino: str | None
    papeis: frozenset[str] = field(default_factory=frozenset)

    @property
    def admin(self) -> bool:
        return "admin" in self.papeis


class TokenInvalido(Exception):
    pass


class Autenticador:
    """Valida tokens RS256 do emissor configurado (chaves do JWKS, ou uma chave fixa nos testes)."""

    def __init__(self, emissor: str, audiencia: str, jwks_url: str | None = None, chave_publica=None):
        self.emissor = emissor
        self.audiencia = audiencia
        self.chave_publica = chave_publica
        self.jwks = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=300) if jwks_url and not chave_publica else None

    def validar(self, token: str) -> Usuario:
        try:
            chave = self.chave_publica or self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                chave,
                algorithms=["RS256"],
                audience=self.audiencia,
                issuer=self.emissor,
                options={"require": ["exp", "iss", "aud"]},
            )
        except (jwt.PyJWTError, AttributeError) as exc:
            raise TokenInvalido(str(exc)) from exc
        papeis = frozenset(p for p in claims.get("realm_access", {}).get("roles", []) if p in PAPEIS)
        return Usuario(claims.get("preferred_username") or claims["sub"], claims.get("inquilino") or None, papeis)


def usuario_atual(
    request: Request, credenciais: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> Usuario:
    if credenciais is None:
        raise HTTPException(401, "token ausente", headers={"WWW-Authenticate": "Bearer"})
    try:
        usuario = request.app.state.autenticador.validar(credenciais.credentials)
    except TokenInvalido as exc:
        raise HTTPException(401, f"token inválido: {exc}", headers={"WWW-Authenticate": "Bearer"}) from exc
    if not usuario.papeis:
        raise HTTPException(403, "usuário sem papel na plataforma")
    return usuario


UsuarioAtual = Annotated[Usuario, Depends(usuario_atual)]


def exigir(*papeis: str) -> Callable[..., ContextoInquilino]:
    """Dependência: usuário com um dos papéis (admin sempre passa) agindo sobre um inquilino."""

    def dependencia(
        banco: Banco,
        usuario: UsuarioAtual,
        x_inquilino: Annotated[str | None, Header(description="Só para admin: inquilino sobre o qual agir")] = None,
    ) -> ContextoInquilino:
        if not usuario.admin and not usuario.papeis & set(papeis):
            raise HTTPException(403, f"requer papel: {', '.join(papeis)}")
        if usuario.admin:
            alvo = x_inquilino or usuario.inquilino
            if not alvo:
                raise HTTPException(400, "administrador: informe o inquilino no cabeçalho X-Inquilino")
        else:
            if not usuario.inquilino:
                raise HTTPException(403, "usuário sem inquilino associado")
            if x_inquilino and x_inquilino != usuario.inquilino:
                raise HTTPException(403, "usuário não pode agir sobre outro inquilino")
            alvo = usuario.inquilino
        with banco.connect() as conexao:
            inquilino_id = conexao.execute(select(db.inquilino.c.id).where(db.inquilino.c.slug == alvo)).scalar()
        if inquilino_id is None:
            raise HTTPException(404, f"inquilino {alvo!r} não encontrado")
        return ContextoInquilino(inquilino_id, alvo, usuario.nome, usuario.papeis)

    return dependencia


def _exigir_admin(usuario: UsuarioAtual) -> Usuario:
    if not usuario.admin:
        raise HTTPException(403, "requer papel: admin")
    return usuario


Leitura = Annotated[ContextoInquilino, Depends(exigir(*PAPEIS))]
Operacao = Annotated[ContextoInquilino, Depends(exigir("gestor", "operador"))]
Integracao = Annotated[ContextoInquilino, Depends(exigir("gestor", "integrador"))]
Gestao = Annotated[ContextoInquilino, Depends(exigir("gestor"))]
Admin = Annotated[Usuario, Depends(_exigir_admin)]


# ──────────────────────────────────────────────
# Máquinas (nós de borda e dispositivos via HTTP)
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class Maquina:
    tipo: str  # "borda" | "dispositivo"
    slug: str
    inquilino: str
    sites: frozenset[str] = frozenset()

    def pode_enviar_por(self, d: DispositivoCadastrado) -> bool:
        if self.tipo == "dispositivo":
            return d.slug == self.slug
        return d.inquilino == self.inquilino and d.site in self.sites


def autenticar_maquina(banco, credenciais: HTTPBasicCredentials | None) -> Maquina | None:
    if credenciais is None:
        return None
    b, d, i = db.no_borda, db.dispositivo, db.inquilino
    with banco.connect() as conexao:
        borda = conexao.execute(
            select(b.c.slug, b.c.credencial_hash, b.c.sites, i.c.slug.label("inquilino"))
            .join(i, i.c.id == b.c.inquilino_id)
            .where(b.c.slug == credenciais.username)
        ).first()
        if borda is not None:
            if verificar_senha(credenciais.password, borda.credencial_hash):
                return Maquina("borda", borda.slug, borda.inquilino, frozenset(borda.sites))
            return None
        disp = conexao.execute(
            select(d.c.slug, d.c.credencial_hash, i.c.slug.label("inquilino"))
            .join(i, i.c.id == d.c.inquilino_id)
            .where(d.c.slug == credenciais.username, d.c.revogado_em.is_(None))
        ).first()
    if disp is not None and verificar_senha(credenciais.password, disp.credencial_hash):
        return Maquina("dispositivo", disp.slug, disp.inquilino)
    return None


def borda_autenticada(
    banco: Banco, slug: str, credenciais: Annotated[HTTPBasicCredentials | None, Depends(_basic)]
) -> Maquina:
    """O próprio nó de borda, e só ele, acessa as rotas /bordas/{slug}/..."""
    maquina = autenticar_maquina(banco, credenciais)
    if maquina is None:
        raise HTTPException(401, "credencial do nó de borda inválida", headers={"WWW-Authenticate": "Basic"})
    if maquina.tipo != "borda" or maquina.slug != slug:
        raise HTTPException(403, "credencial não pertence a este nó de borda")
    return maquina


Borda = Annotated[Maquina, Depends(borda_autenticada)]


def autorizacao_ingestao(
    request: Request,
    banco: Banco,
    basic: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Callable[[DispositivoCadastrado], bool]:
    """
    Quem pode enviar mensagens por HTTP e de quais dispositivos:
    dispositivo → só as próprias; nó de borda → dispositivos dos sites dele;
    integrador/gestor → dispositivos do próprio inquilino; admin → qualquer um.
    """
    if bearer is not None:
        try:
            usuario = request.app.state.autenticador.validar(bearer.credentials)
        except TokenInvalido as exc:
            raise HTTPException(401, f"token inválido: {exc}") from exc
        if usuario.admin:
            return lambda d: True
        if not usuario.papeis & {"gestor", "integrador"}:
            raise HTTPException(403, "ingestão por usuário requer papel gestor ou integrador")
        return lambda d: d.inquilino == usuario.inquilino
    maquina = autenticar_maquina(banco, basic)
    if maquina is None:
        raise HTTPException(401, "credencial ausente ou inválida", headers={"WWW-Authenticate": "Basic"})
    return maquina.pode_enviar_por


AutorizacaoIngestao = Annotated[Callable[[DispositivoCadastrado], bool], Depends(autorizacao_ingestao)]

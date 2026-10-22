# ADR 0006 — Keycloak para identidade

## Contexto
Usuários entram por OpenID Connect, com segundo fator para administradores
(RF-SEG-01), cinco papéis (RF-SEG-02) e isolamento por inquilino (RF-SEG-03).

## Decisão
**Keycloak 26** no compose, com o realm versionado
(`infra/keycloak/sentinela-realm.json`): papéis no `realm_access`, claim
`inquilino`, audiência `sentinela-api` e TOTP obrigatório para o admin. A API
valida o JWT (RS256) pelo JWKS; o painel usa Authorization Code + PKCE
(`keycloak-js`).

## Alternativas
- **Autenticação própria na API** (usuário/senha + JWT): reinventaria 2FA,
  recuperação de senha e sessões.
- **Auth0 / Cognito**: serviço externo pago e fora do ambiente local.
- **Authentik / Zitadel**: viáveis, mas o Keycloak é a referência do §9.1.

## Consequências
- Usuários e senhas ficam fora do banco da plataforma; o banco guarda só o `sub`.
- Máquinas (dispositivos e bordas) não usam o Keycloak: autenticam por HTTP
  Basic com a credencial própria.
- Chaves de API com escopo e validade (RF-SEG-06) ficaram fora desta entrega.

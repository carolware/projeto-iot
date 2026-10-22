/**
 * auth.tsx — login pelo Keycloak (OIDC, código + PKCE) e chamadas autenticadas
 * à API da central (RF-SEG-01/02).
 *
 * O adaptador `keycloak-js` só existe no navegador: é carregado com import()
 * dentro de um efeito, e até lá a página mostra "entrando…" (o HTML do
 * servidor e o da hidratação são iguais).
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type Keycloak from "keycloak-js";
import { API_URL, KEYCLOAK_CLIENTE, KEYCLOAK_REALM, KEYCLOAK_URL } from "./config";
import type { Eu } from "./tipos";

export class ErroApi extends Error {
  constructor(
    public status: number,
    public detalhe: unknown,
  ) {
    super(typeof detalhe === "string" ? detalhe : JSON.stringify(detalhe));
  }
}

interface Sessao {
  eu: Eu;
  token: () => Promise<string>;
  api: <T = unknown>(caminho: string, opcoes?: { metodo?: string; corpo?: unknown }) => Promise<T>;
  pode: (...papeis: string[]) => boolean;
  sair: () => void;
}

const Contexto = createContext<Sessao | null>(null);

export function useSessao(): Sessao {
  const sessao = useContext(Contexto);
  if (!sessao) throw new Error("useSessao fora do AuthProvider");
  return sessao;
}

function Aviso({ texto, detalhe }: { texto: string; detalhe?: string }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-ink text-fg font-mono text-sm">
      <div className="text-center">
        <div className="font-display text-[22px] tracking-wide">SENTINELA</div>
        <div className="mt-2 text-faint">{texto}</div>
        {detalhe && <div className="mt-2 text-danger text-[11px] max-w-md">{detalhe}</div>}
      </div>
    </div>
  );
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [kc, setKc] = useState<Keycloak | null>(null);
  const [eu, setEu] = useState<Eu | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    let cancelado = false;
    (async () => {
      try {
        const { default: KeycloakJs } = await import("keycloak-js");
        const instancia = new KeycloakJs({ url: KEYCLOAK_URL, realm: KEYCLOAK_REALM, clientId: KEYCLOAK_CLIENTE });
        await instancia.init({ onLoad: "login-required", pkceMethod: "S256", checkLoginIframe: false });
        const resposta = await fetch(`${API_URL}/api/v1/eu`, { headers: { Authorization: `Bearer ${instancia.token}` } });
        if (!resposta.ok) throw new Error(`a API recusou o login (${resposta.status})`);
        if (!cancelado) {
          setKc(instancia);
          setEu(await resposta.json());
        }
      } catch (e) {
        if (!cancelado) setErro(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelado = true;
    };
  }, []);

  const token = useCallback(async () => {
    if (!kc) throw new Error("sem sessão");
    await kc.updateToken(30); // renova se faltar menos de 30 s
    return kc.token!;
  }, [kc]);

  const api = useCallback(
    async <T,>(caminho: string, opcoes: { metodo?: string; corpo?: unknown } = {}): Promise<T> => {
      const headers: Record<string, string> = { Authorization: `Bearer ${await token()}` };
      const init: RequestInit = { method: opcoes.metodo ?? "GET", headers };
      if (opcoes.corpo !== undefined) {
        headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(opcoes.corpo);
      }
      const resposta = await fetch(`${API_URL}${caminho}`, init);
      const corpo = resposta.status === 204 ? null : await resposta.json().catch(() => null);
      if (!resposta.ok) throw new ErroApi(resposta.status, corpo?.detail ?? corpo);
      return corpo as T;
    },
    [token],
  );

  const sessao = useMemo<Sessao | null>(
    () =>
      kc && eu
        ? {
            eu,
            token,
            api,
            pode: (...papeis) => eu.papeis.includes("admin") || papeis.some((p) => eu.papeis.includes(p)),
            sair: () => kc.logout({ redirectUri: window.location.origin }),
          }
        : null,
    [kc, eu, token, api],
  );

  if (erro) return <Aviso texto="Não foi possível entrar" detalhe={erro} />;
  if (!sessao) return <Aviso texto="entrando pelo Keycloak…" />;
  return <Contexto.Provider value={sessao}>{children}</Contexto.Provider>;
}

import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useSessao } from "@/lib/auth";
import { useClock } from "@/lib/iot";
import { RISK_LABEL, type Risk } from "@/lib/tipos";
import { StatusDot } from "@/components/dashboard/StatusDot";

function Relogio() {
  const { clock } = useClock();
  return <span className="hidden md:inline text-faint tabular-nums">{clock}</span>;
}

const LINKS: Array<{ para: string; rotulo: string; papeis?: string[] }> = [
  { para: "/", rotulo: "PAINEL" },
  { para: "/alarmes", rotulo: "ALARMES" },
  { para: "/eventos", rotulo: "EVENTOS" },
  { para: "/bordas", rotulo: "BORDAS" },
  { para: "/cadastro", rotulo: "CADASTRO", papeis: ["gestor", "integrador"] },
];

/** Cabeçalho comum (navegação, usuário, tempo real) + conteúdo da página. */
export function Casco({ titulo, conectado, children }: { titulo: string; conectado?: boolean; children: ReactNode }) {
  const { eu, pode, sair } = useSessao();
  const links = LINKS.filter((l) => !l.papeis || pode(...l.papeis)).filter(
    (l) => !(eu.cenario === "urbano" && l.para === "/bordas"),
  );
  return (
    <div className="min-h-screen flex flex-col bg-ink text-fg font-body text-sm antialiased">
      <header className="sticky top-0 z-20 border-b border-hair bg-ink/95 backdrop-blur-sm">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-5 py-2.5">
          <div className="flex items-baseline gap-2.5">
            <span className="font-display text-[19px] tracking-wide text-fg leading-none">SENTINELA</span>
            <span className="font-mono text-[10px] text-ember tracking-[0.15em]">PLATAFORMA IOT</span>
          </div>
          <nav aria-label="Navegação principal" className="flex items-center gap-1 font-mono text-[10px] tracking-[0.15em]">
            {links.map((l) => (
              <Link
                key={l.para}
                to={l.para}
                className="rounded px-2 py-1 text-faint hover:text-fg"
                activeProps={{ className: "rounded px-2 py-1 bg-surface-2 text-fg" }}
                activeOptions={{ exact: l.para === "/" }}
              >
                {l.rotulo}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-4 font-mono text-[11px]">
            {conectado !== undefined && (
              <span className="flex items-center gap-1.5" aria-live="polite">
                <StatusDot color={conectado ? "bg-ok" : "bg-danger"} duration={2} />
                <span className="text-fg">TEMPO REAL</span>
                <span className="text-faint hidden sm:inline">{conectado ? "conectado" : "reconectando…"}</span>
              </span>
            )}
            <span className="text-faint">
              <span className="text-fg">{eu.usuario}</span> · {eu.papeis.join(", ")} · {eu.inquilino ?? "todos os inquilinos"}
            </span>
            <Relogio />
            <button onClick={sair} className="rounded border border-hair px-2 py-1 text-faint hover:text-fg cursor-pointer">
              SAIR
            </button>
          </div>
        </div>
      </header>
      <h1 className="sr-only">{titulo}</h1>
      {children}
    </div>
  );
}

export function Secao({ titulo, acoes, children }: { titulo: string; acoes?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-md border border-hair bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h2 className="font-mono text-[10px] tracking-[0.2em] text-faint">{titulo}</h2>
        {acoes}
      </div>
      {children}
    </section>
  );
}

export const BADGE_SEVERIDADE: Record<string, string> = {
  baixo: "bg-ok/15 text-ok",
  medio: "bg-warn/20 text-warn",
  alto: "bg-warn text-ink",
  critico: "bg-danger text-ink",
  offline: "bg-hair text-dim",
};

export function Badge({ valor }: { valor: string }) {
  return (
    <span className={`rounded-[3px] px-1.5 py-0.5 font-mono text-[9px] font-bold tracking-widest ${BADGE_SEVERIDADE[valor] ?? "bg-hair text-dim"}`}>
      {RISK_LABEL[valor as Risk] ?? valor.toUpperCase()}
    </span>
  );
}

export function quando(iso: string | null | undefined) {
  return iso ? new Date(iso).toLocaleString("pt-BR", { hour12: false }) : "—";
}

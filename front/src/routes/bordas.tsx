import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useSessao } from "@/lib/auth";
import { useTempoReal } from "@/lib/iot";
import type { Pagina } from "@/lib/tipos";
import { StatusDot } from "@/components/dashboard/StatusDot";
import { Casco, Secao, quando } from "@/components/layout/Casco";

export const Route = createFileRoute("/bordas")({
  head: () => ({ meta: [{ title: "Nós de borda — SENTINELA" }] }),
  component: Bordas,
});

interface NoBorda {
  slug: string;
  nome: string;
  versao: string | null;
  fila_pendente: number;
  ultimo_sync_em: string | null;
  segundos_sem_sincronizar: number | null;
}

const LIMITE_ATENCAO_S = 60;

function tempo(segundos: number | null) {
  if (segundos === null) return "nunca sincronizou";
  if (segundos < 60) return `${Math.round(segundos)} s`;
  if (segundos < 3600) return `${Math.round(segundos / 60)} min`;
  return `${(segundos / 3600).toFixed(1)} h`;
}

/** Saúde dos nós de borda: fila pendente e há quanto tempo cada um está sem sincronizar (RF-EDG-06). */
function Bordas() {
  const { api } = useSessao();
  const conectado = useTempoReal();
  const { data, isLoading } = useQuery({
    queryKey: ["bordas"],
    queryFn: () => api<Pagina<NoBorda>>("/api/v1/bordas"),
    refetchInterval: 5000,
  });
  return (
    <Casco titulo="Nós de borda" conectado={conectado}>
      <main className="flex-1 px-5 py-4 max-w-5xl w-full mx-auto">
        <Secao titulo="NÓS DE BORDA (FOG) · SINCRONIZAÇÃO COM A CENTRAL">
          {isLoading ? (
            <div className="font-mono text-[11px] text-faint">carregando…</div>
          ) : (data?.itens.length ?? 0) === 0 ? (
            <div className="font-mono text-[11px] text-faint">Este inquilino não tem nós de borda.</div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {data!.itens.map((b) => {
                const atrasado = b.segundos_sem_sincronizar === null || b.segundos_sem_sincronizar > LIMITE_ATENCAO_S;
                return (
                  <article key={b.slug} className={`rounded border ${atrasado ? "border-danger/50" : "border-hair"} bg-surface-2 p-3 font-mono text-[11px]`}>
                    <div className="flex items-center gap-2">
                      <StatusDot color={atrasado ? "bg-danger" : "bg-ok"} duration={2} />
                      <h3 className="font-display text-[16px] tracking-wide">{b.nome}</h3>
                    </div>
                    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                      <dt className="text-faint">sem sincronizar há</dt>
                      <dd className={atrasado ? "text-danger" : "text-ok"}>{tempo(b.segundos_sem_sincronizar)}</dd>
                      <dt className="text-faint">fila pendente</dt>
                      <dd className={b.fila_pendente > 0 ? "text-warn" : "text-fg"}>{b.fila_pendente} mensagem(ns)</dd>
                      <dt className="text-faint">última sincronização</dt>
                      <dd>{quando(b.ultimo_sync_em)}</dd>
                      <dt className="text-faint">versão</dt>
                      <dd>{b.versao ?? "—"}</dd>
                    </dl>
                    {atrasado && (
                      <div className="mt-2 text-danger text-[10px]">
                        Link com a central fora: o nó continua decidindo localmente e guardando na fila cifrada.
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          )}
        </Secao>
      </main>
    </Casco>
  );
}

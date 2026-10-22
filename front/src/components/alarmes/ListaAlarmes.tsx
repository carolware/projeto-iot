import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSessao } from "@/lib/auth";
import { useTempoReal } from "@/lib/iot";
import type { Alarme, Json, Pagina } from "@/lib/tipos";
import { Badge, quando } from "@/components/layout/Casco";

interface Detalhe extends Alarme {
  entregas: Array<{ inquilino: string; estado: string; reconhecido_por: string | null }>;
  acoes: Array<{ tipo: string; status: string; tentativas: number; ultimo_erro: string | null; executado_em: string | null }>;
  comandos: Array<{ id: string; dispositivo: string; acao: string; origem: string; estado: string; respondido_em: string | null }>;
}

const ESTADO_COR: Record<string, string> = { ativo: "text-danger", reconhecido: "text-warn", resolvido: "text-ok" };

function DetalheAlarme({ id, onFechar }: { id: string; onFechar: () => void }) {
  const { api } = useSessao();
  const { data } = useQuery({ queryKey: ["alarme", id], queryFn: () => api<Detalhe>(`/api/v1/alarmes/${id}`) });
  return (
    <div className="fixed inset-0 z-50 bg-black/80 flex justify-end" onClick={onFechar} role="dialog" aria-modal="true" aria-label="Detalhe do alarme">
      <aside className="h-full w-full max-w-lg overflow-y-auto border-l border-hair bg-surface p-5 font-mono text-[11px]" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <span className="text-faint tracking-[0.2em] text-[10px]">DETALHE DO ALARME</span>
          <button onClick={onFechar} aria-label="Fechar" className="text-faint hover:text-fg cursor-pointer">✕</button>
        </div>
        {!data ? (
          <div className="mt-4 text-faint">carregando…</div>
        ) : (
          <>
            <div className="mt-3 flex items-center gap-2">
              <Badge valor={data.severidade} />
              <span className="text-fg text-[13px]">{data.regra ?? data.dados.resumo}</span>
            </div>
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
              <dt className="text-faint">estado</dt><dd className={ESTADO_COR[data.estado]}>{data.estado}</dd>
              <dt className="text-faint">onde</dt><dd>{data.site ?? data.dados.origem?.site} {data.dispositivo ? `· ${data.dispositivo}` : ""}</dd>
              <dt className="text-faint">ocorreu</dt><dd>{quando(data.ocorrido_em)}</dd>
              <dt className="text-faint">ocorrências</dt><dd>{data.ocorrencias}</dd>
              <dt className="text-faint">reconhecido</dt><dd>{data.reconhecido_por ? `${data.reconhecido_por} · ${quando(data.reconhecido_em)}` : "—"}</dd>
              <dt className="text-faint">resolvido</dt><dd>{data.resolvido_por ? `${data.resolvido_por} · ${quando(data.resolvido_em)}` : "—"}</dd>
              {data.dados.decidido_na_borda && (<><dt className="text-faint">decisão</dt><dd>na borda ({data.dados.via})</dd></>)}
              {data.dados.satelite && (<><dt className="text-faint">satélite</dt><dd>{data.dados.satelite.focos} foco(s) FIRMS</dd></>)}
            </dl>
            {data.dados.leituras && (
              <div className="mt-3 text-faint">leituras: <span className="text-fg">{JSON.stringify(data.dados.leituras)}</span></div>
            )}
            <h3 className="mt-4 text-faint tracking-[0.2em] text-[10px]">AÇÕES</h3>
            <ul className="mt-1 space-y-1">
              {data.acoes.length === 0 && <li className="text-faint">nenhuma</li>}
              {data.acoes.map((a, i) => (
                <li key={i}>{a.tipo} · <span className={a.status === "sucesso" ? "text-ok" : "text-warn"}>{a.status}</span> · {a.tentativas} tentativa(s){a.ultimo_erro ? ` · ${a.ultimo_erro}` : ""}</li>
              ))}
            </ul>
            <h3 className="mt-4 text-faint tracking-[0.2em] text-[10px]">COMANDOS</h3>
            <ul className="mt-1 space-y-1">
              {data.comandos.length === 0 && <li className="text-faint">nenhum</li>}
              {data.comandos.map((c) => (
                <li key={c.id}>{c.dispositivo} · {c.acao} · {c.origem} · <span className={c.estado === "confirmado" ? "text-ok" : "text-warn"}>{c.estado}</span></li>
              ))}
            </ul>
            {data.entregas.length > 0 && (
              <>
                <h3 className="mt-4 text-faint tracking-[0.2em] text-[10px]">ENTREGUE ÀS PREFEITURAS</h3>
                <ul className="mt-1 space-y-1">
                  {data.entregas.map((e) => (
                    <li key={e.inquilino}>{e.inquilino} · <span className={ESTADO_COR[e.estado]}>{e.estado}</span>{e.reconhecido_por ? ` · ${e.reconhecido_por}` : ""}</li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </aside>
    </div>
  );
}

/** Lista de alarmes do inquilino, ao vivo, com reconhecer/resolver (RF-PRO-05). */
export function ListaAlarmes({
  abertos,
  site,
  compacta,
}: {
  abertos?: boolean | undefined;
  site?: string | undefined;
  compacta?: boolean;
}) {
  const { api, pode } = useSessao();
  const queryClient = useQueryClient();
  const [aberto, setAberto] = useState<string | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const filtros = new URLSearchParams({ tamanho: "50", ...(abertos !== undefined ? { abertos: String(abertos) } : {}), ...(site ? { site } : {}) });
  const chave = ["alarmes", filtros.toString()];
  const { data, isLoading } = useQuery({ queryKey: chave, queryFn: () => api<Pagina<Alarme>>(`/api/v1/alarmes?${filtros}`) });
  useTempoReal((m: Json) => {
    if (m.tipo === "alarme") queryClient.invalidateQueries({ queryKey: ["alarmes"] });
  });

  const transicao = async (id: string, acao: "reconhecer" | "resolver") => {
    setErro(null);
    try {
      await api(`/api/v1/alarmes/${id}/${acao}`, { metodo: "POST", corpo: {} });
      queryClient.invalidateQueries({ queryKey: ["alarmes"] });
    } catch (e) {
      setErro(e instanceof Error ? e.message : String(e));
    }
  };
  const operar = pode("operador", "gestor");

  if (isLoading) return <div className="font-mono text-[11px] text-faint">carregando alarmes…</div>;
  const itens = data?.itens ?? [];
  return (
    <div>
      {erro && <div role="alert" className="mb-2 font-mono text-[11px] text-danger">{erro}</div>}
      {itens.length === 0 ? (
        <div className="font-mono text-[11px] text-faint">{abertos ? "Nenhum alarme aberto." : "Nenhum alarme."}</div>
      ) : (
        <ul className="space-y-2">
          {itens.map((a) => (
            <li key={a.id} className="rounded border border-hair bg-surface-2 px-3 py-2 font-mono text-[11px]">
              <div className="flex flex-wrap items-center gap-2">
                <Badge valor={a.severidade} />
                <button onClick={() => setAberto(a.id)} className="text-fg hover:underline cursor-pointer text-left">
                  {a.dados.resumo ?? `${a.regra} · ${a.site_nome ?? a.site}`}
                </button>
                <span className={`ml-auto ${ESTADO_COR[a.estado]}`}>{a.estado}</span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 text-faint text-[10px]">
                <span>{quando(a.ocorrido_em)}</span>
                {a.dispositivo && <span>{a.dispositivo}</span>}
                {a.ocorrencias > 1 && <span>{a.ocorrencias} ocorrências</span>}
                {a.dados.decidido_na_borda && <span>decidido na borda</span>}
                {a.dados.distancia_km !== undefined && <span>{a.dados.distancia_km} km</span>}
                {!compacta && a.reconhecido_por && <span>reconhecido por {a.reconhecido_por}</span>}
                {operar && a.estado === "ativo" && (
                  <button onClick={() => transicao(a.id, "reconhecer")} className="rounded border border-warn/50 px-2 py-0.5 text-warn hover:bg-warn/10 cursor-pointer">
                    RECONHECER
                  </button>
                )}
                {operar && a.estado !== "resolvido" && (
                  <button onClick={() => transicao(a.id, "resolver")} className="rounded border border-ok/50 px-2 py-0.5 text-ok hover:bg-ok/10 cursor-pointer">
                    RESOLVER
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {aberto && <DetalheAlarme id={aberto} onFechar={() => setAberto(null)} />}
    </div>
  );
}

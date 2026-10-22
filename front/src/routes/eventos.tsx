import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSessao } from "@/lib/auth";
import { usePainel, useTempoReal } from "@/lib/iot";
import type { Json, Pagina } from "@/lib/tipos";
import { ListaAlarmes } from "@/components/alarmes/ListaAlarmes";
import { Casco, Secao, quando } from "@/components/layout/Casco";

export const Route = createFileRoute("/eventos")({
  head: () => ({ meta: [{ title: "Eventos por local — SENTINELA" }] }),
  validateSearch: (busca: Record<string, unknown>): { site?: string | undefined } => ({
    site: typeof busca["site"] === "string" ? busca["site"] : undefined,
  }),
  component: Eventos,
});

interface Evento {
  tempo: string;
  dispositivo: string;
  site: string;
  tipo: string;
  dados: Json;
}

function resumo(e: Evento) {
  const d = e.dados;
  if (e.tipo === "varredura_firms") return `${d.focos ?? 0} foco(s) · FRP máx ${d.frp_max ?? 0} MW`;
  if (e.tipo === "alarme_local") return `${d.severidade} · ${d.regra} · decidido na borda`;
  if (d.acao) return `${d.acao}${d.comando_id ? ` · comando ${String(d.comando_id).slice(0, 8)}` : ""}`;
  return JSON.stringify(d).slice(0, 120);
}

/** "Logs por região": eventos dos dispositivos e alarmes de um local. */
function Eventos() {
  const { api } = useSessao();
  const busca = Route.useSearch();
  const navegar = Route.useNavigate();
  const queryClient = useQueryClient();
  const painel = usePainel();
  const site = busca.site ?? "";
  const [tipo, setTipo] = useState("");
  const filtros = new URLSearchParams({ tamanho: "100", ...(site ? { site } : {}), ...(tipo ? { tipo } : {}) });
  const { data, isLoading } = useQuery({
    queryKey: ["eventos", filtros.toString()],
    queryFn: () => api<Pagina<Evento>>(`/api/v1/eventos?${filtros}`),
  });
  const conectado = useTempoReal((m) => {
    if (m.tipo === "evento") queryClient.invalidateQueries({ queryKey: ["eventos"] });
  });

  return (
    <Casco titulo="Eventos por local" conectado={conectado}>
      <main className="flex-1 grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_380px] gap-4 px-5 py-4">
        <Secao
          titulo={`EVENTOS ${site ? `· ${site.toUpperCase()}` : "· TODOS OS LOCAIS"}`}
          acoes={
            <div className="flex flex-wrap gap-2 font-mono text-[10px]">
              <label className="flex items-center gap-1 text-faint">
                LOCAL
                <select
                  value={site}
                  onChange={(e) => navegar({ search: { site: e.target.value || undefined } })}
                  className="rounded border border-hair bg-ink px-2 py-1 text-fg"
                >
                  <option value="">todos</option>
                  {painel.data?.sites.map((s) => (
                    <option key={s.slug} value={s.slug}>
                      {s.nome}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-1 text-faint">
                TIPO
                <select value={tipo} onChange={(e) => setTipo(e.target.value)} className="rounded border border-hair bg-ink px-2 py-1 text-fg">
                  <option value="">todos</option>
                  <option value="alarme_local">alarme na borda</option>
                  <option value="executado">comando executado</option>
                  <option value="recebido">alerta recebido</option>
                  <option value="reconhecido">alerta reconhecido</option>
                  <option value="varredura_firms">varredura FIRMS</option>
                </select>
              </label>
            </div>
          }
        >
          {isLoading ? (
            <div className="font-mono text-[11px] text-faint">carregando…</div>
          ) : (data?.itens.length ?? 0) === 0 ? (
            <div className="font-mono text-[11px] text-faint">Nenhum evento nos últimos 7 dias.</div>
          ) : (
            <table className="w-full font-mono text-[11px]">
              <caption className="sr-only">Eventos dos dispositivos</caption>
              <thead>
                <tr className="text-left text-faint text-[10px]">
                  <th className="py-1 pr-3 font-normal">QUANDO</th>
                  <th className="py-1 pr-3 font-normal">LOCAL</th>
                  <th className="py-1 pr-3 font-normal">DISPOSITIVO</th>
                  <th className="py-1 pr-3 font-normal">EVENTO</th>
                  <th className="py-1 font-normal">DETALHE</th>
                </tr>
              </thead>
              <tbody>
                {data!.itens.map((e, i) => (
                  <tr key={`${e.tempo}-${e.dispositivo}-${i}`} className="border-t border-hair/60 align-top">
                    <td className="py-1.5 pr-3 text-faint whitespace-nowrap">{quando(e.tempo)}</td>
                    <td className="py-1.5 pr-3">{e.site}</td>
                    <td className="py-1.5 pr-3">{e.dispositivo}</td>
                    <td className="py-1.5 pr-3 text-ember">{e.tipo}</td>
                    <td className="py-1.5 text-faint break-all">{resumo(e)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Secao>
        <Secao titulo={`ALARMES ${site ? `· ${site.toUpperCase()}` : ""}`}>
          <ListaAlarmes key={site} site={site || undefined} compacta />
        </Secao>
      </main>
    </Casco>
  );
}

import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useSessao } from "@/lib/auth";
import { useClock, useIot, usePainel, useTempoReal, type Zone } from "@/lib/iot";
import { ZoneCard, ZoneHero } from "@/components/dashboard/ZoneCard";
import { SidePanel } from "@/components/dashboard/SidePanel";
import { ZoneModal } from "@/components/dashboard/ZoneModal";
import { StatusDot } from "@/components/dashboard/StatusDot";
import { ListaAlarmes } from "@/components/alarmes/ListaAlarmes";
import { Casco, Secao, quando } from "@/components/layout/Casco";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "SENTINELA — Plataforma IoT Integrada" },
      {
        name: "description",
        content:
          "Painel da Plataforma IoT Integrada: estações de detecção de incêndio por zona, decisão nos nós de borda, alarmes e alertas às prefeituras.",
      },
    ],
  }),
  component: Index,
});

function fmtUptime(sec: number) {
  const h = String(Math.floor(sec / 3600)).padStart(2, "0");
  const m = String(Math.floor((sec % 3600) / 60)).padStart(2, "0");
  const s = String(sec % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

function Uptime() {
  const { uptimeSec } = useClock();
  return <span className="tabular-nums">{fmtUptime(uptimeSec)}</span>;
}

function Index() {
  const { eu } = useSessao();
  return eu.cenario === "urbano" ? <PainelPrefeitura /> : <PainelRural />;
}

// ── Inquilino rural: as zonas monitoradas ────────────────────────────────────

function PainelRural() {
  const { pode } = useSessao();
  const state = useIot();
  const { zones } = state;
  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(null);

  const alertZones = zones.filter((z) => z.risk === "alto" || z.risk === "critico");
  const critical = zones.filter((z) => z.risk === "critico").length;
  const highSmoke = zones.filter((z) => z.online && z.smoke >= 60).length;
  const offline = zones.filter((z) => !z.online).length;

  const hero = zones.find((z) => z.risk === "critico") ?? zones.find((z) => z.risk === "alto");
  const rest = zones.filter((z) => z !== hero);
  const selectedZone: Zone | undefined = selectedZoneId ? zones.find((z) => z.id === selectedZoneId) : undefined;

  return (
    <Casco titulo="Painel de zonas monitoradas" conectado={state.mqttConnected}>
      {/* MISSION STRIP */}
      <section className="border-b border-hair px-5 py-3">
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
          <div className="flex items-center gap-4">
            <span className="font-mono text-[10px] tracking-[0.25em] text-faint">01 — VISÃO DE CONJUNTO</span>
            <div className="flex items-center gap-2.5" aria-live="polite">
              {alertZones.length > 0 ? (
                <>
                  <span className="size-2 rounded-full bg-danger" style={{ animation: "softpulse 1.2s ease-in-out infinite" }} />
                  <span className="font-mono text-[13px] text-danger font-medium tracking-wide">
                    {alertZones.length} ZONA{alertZones.length > 1 ? "S" : ""} EM ALERTA
                    {critical > 0 ? ` · ${critical} CRÍTICA` : ""}
                  </span>
                </>
              ) : (
                <>
                  <span className="size-2 rounded-full bg-ok" style={{ animation: "softpulse 2s ease-in-out infinite" }} />
                  <span className="font-mono text-[13px] text-ok font-medium tracking-wide">TODAS AS ZONAS ESTÁVEIS</span>
                </>
              )}
            </div>
          </div>
          <div className="flex items-center gap-4 sm:gap-6 font-mono text-[11px]">
            <div>
              <span className="text-faint">ZONAS</span> <span className="text-fg tabular-nums ml-1">{zones.length}</span>
            </div>
            <div>
              <span className="text-faint">ÍNDICE ALTO</span> <span className="text-warn tabular-nums ml-1">{highSmoke}</span>
            </div>
            <div>
              <span className="text-faint">OFFLINE</span>{" "}
              <span className={`tabular-nums ml-1 ${offline > 0 ? "text-danger" : "text-ok"}`}>{offline}</span>
            </div>
          </div>
        </div>
      </section>

      {/* GRID */}
      <main className="flex-1 grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_340px] gap-4 px-5 py-4">
        <section aria-label="Zonas">
          {state.erro && <div role="alert" className="mb-3 font-mono text-[11px] text-danger">{state.erro}</div>}
          {state.carregando && <div className="font-mono text-[11px] text-faint">carregando zonas…</div>}
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
            {hero && <ZoneHero zone={hero} index={0} onSelect={(z) => setSelectedZoneId(z.id)} />}
            {rest.map((z, i) => (
              <ZoneCard key={z.id} zone={z} index={i + 1} onSelect={(zz) => setSelectedZoneId(zz.id)} />
            ))}
          </div>
        </section>
        <SidePanel state={state} podeComandar={pode("operador", "gestor")} />
      </main>

      <footer className="border-t border-hair px-5 py-2.5 flex items-center justify-between font-mono text-[10px] text-faint">
        <span>SENTINELA · dados da central (API + tempo real) · risco classificado pelas regras do inquilino</span>
        <span className="tabular-nums">
          sessão <Uptime /> · {zones.length} zonas · {critical} crítica
        </span>
      </footer>

      {selectedZone && <ZoneModal zone={selectedZone} state={state} onClose={() => setSelectedZoneId(null)} />}
    </Casco>
  );
}

// ── Inquilino urbano (prefeitura): alertas recebidos e terminais ─────────────

function PainelPrefeitura() {
  const { data, isLoading } = usePainel();
  const conectado = useTempoReal();
  const terminais = (data?.sites ?? []).flatMap((s) =>
    s.dispositivos.filter((d) => d.tipo === "terminal").map((d) => ({ ...d, local: s.nome })),
  );
  return (
    <Casco titulo="Painel da prefeitura" conectado={conectado}>
      <main className="flex-1 grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px] items-start gap-4 px-5 py-4">
        <Secao titulo="01 — ALERTAS RECEBIDOS (ASSINATURAS)">
          <ListaAlarmes abertos />
        </Secao>
        <div className="flex flex-col gap-4">
          <Secao titulo="02 — TERMINAIS DE ALERTA">
            {isLoading ? (
              <div className="font-mono text-[11px] text-faint">carregando…</div>
            ) : terminais.length === 0 ? (
              <div className="font-mono text-[11px] text-faint">Nenhum terminal cadastrado.</div>
            ) : (
              <ul className="space-y-2 font-mono text-[11px]">
                {terminais.map((t) => (
                  <li key={t.slug} className="flex items-center gap-2 rounded border border-hair bg-surface-2 px-3 py-2">
                    <StatusDot color={t.online ? "bg-ok" : "bg-danger"} duration={2} />
                    <span className="text-fg">{t.slug}</span>
                    <span className="text-faint">{t.local}</span>
                    <span className="ml-auto text-faint text-[10px]">{t.online ? "online" : `offline · ${quando(t.ultimo_contato_em)}`}</span>
                  </li>
                ))}
              </ul>
            )}
          </Secao>
          <Secao titulo="03 — HISTÓRICO">
            <ListaAlarmes abertos={false} compacta />
          </Secao>
        </div>
      </main>
    </Casco>
  );
}

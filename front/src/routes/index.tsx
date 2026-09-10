import { createFileRoute } from "@tanstack/react-router";
import { useIot } from "@/lib/iot";
import { StatusDot } from "@/components/dashboard/StatusDot";
import { ZoneCard, ZoneHero } from "@/components/dashboard/ZoneCard";
import { SidePanel } from "@/components/dashboard/SidePanel";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "SENTINELA — Monitoramento de Incêndios Florestais" },
      {
        name: "description",
        content:
          "Dashboard IoT de monitoramento de incêndios: sensores de temperatura, umidade e fumaça por zona, regras de risco, confirmação via satélite NASA FIRMS e comandos MQTT.",
      },
      { property: "og:title", content: "SENTINELA — Monitoramento de Incêndios Florestais" },
      {
        property: "og:description",
        content:
          "Painel em tempo real de sensores IoT por zona, avaliação de risco de incêndio e alertas via MQTT com confirmação por satélite.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
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

function Index() {
  const state = useIot();
  const { zones } = state;

  const alertZones = zones.filter((z) => z.risk === "alto" || z.risk === "critico");
  const critical = zones.filter((z) => z.risk === "critico").length;
  const highSmoke = zones.filter((z) => z.online && z.smoke >= 60).length;
  const offline = zones.filter((z) => !z.online).length;

  const hero = zones.find((z) => z.risk === "critico") ?? zones.find((z) => z.risk === "alto");
  const rest = zones.filter((z) => z !== hero);

  return (
    <div className="min-h-screen bg-ink text-fg font-body text-sm antialiased">
      {/* SYSTEM BAR */}
      <header className="sticky top-0 z-10 border-b border-hair bg-ink/95 backdrop-blur-sm">
        <div className="flex items-center gap-6 px-5 py-2.5">
          <div className="flex items-baseline gap-2.5">
            <span className="font-display text-[19px] tracking-wide text-fg leading-none">SENTINELA</span>
            <span className="font-mono text-[10px] text-ember tracking-[0.15em]">REDE SENSORES v0.3</span>
          </div>
          <div className="hidden lg:flex items-center gap-2 font-mono text-[10px] text-faint tracking-[0.2em]">
            <span>POSTO DE VIGILÂNCIA</span>
            <span className="text-hair">/</span>
            <span>GO · CHAPADA DOS VEADEIROS</span>
          </div>
          <div className="ml-auto flex items-center gap-4 sm:gap-5 font-mono text-[11px]">
            <div className="flex items-center gap-1.5">
              <StatusDot color={state.mqttConnected ? "bg-ok" : "bg-danger"} duration={2} />
              <span className="text-fg">MQTT</span>
              <span className="text-faint hidden sm:inline">{state.mqttConnected ? `conectado · ${state.dataMode}` : "desconectado"}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <StatusDot color="bg-ok" duration={2.4} />
              <span className="text-fg">GATEWAY</span>
              <span className="text-faint hidden sm:inline">ativo</span>
            </div>
            <div className="flex items-center gap-1.5">
              <StatusDot color="bg-warn" duration={1.6} />
              <span className="text-fg">NASA FIRMS</span>
              <span className="text-faint hidden sm:inline">sincronizando</span>
            </div>
            <div className="hidden md:block pl-4 border-l border-hair text-faint tabular-nums">{state.clock}</div>
          </div>
        </div>
      </header>

      {/* MISSION STRIP */}
      <section className="border-b border-hair px-5 py-3">
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
          <div className="flex items-center gap-4">
            <span className="font-mono text-[10px] tracking-[0.25em] text-faint">01 — VISÃO DE CONJUNTO</span>
            <div className="flex items-center gap-2.5">
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
              <span className="text-faint">FUMAÇA ALTA</span>{" "}
              <span className="text-warn tabular-nums ml-1">{highSmoke}</span>
            </div>
            <div>
              <span className="text-faint">FALHAS</span>{" "}
              <span className={`tabular-nums ml-1 ${offline > 0 ? "text-danger" : "text-ok"}`}>{offline}</span>
            </div>
          </div>
        </div>
      </section>

      {/* GRID */}
      <main className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_340px] gap-4 px-5 py-4">
        <section>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
            {hero && <ZoneHero zone={hero} index={0} />}
            {rest.map((z, i) => (
              <ZoneCard key={z.id} zone={z} index={i + 1} />
            ))}
          </div>
        </section>
        <SidePanel state={state} />
      </main>

      {/* FOOTER */}
      <footer className="border-t border-hair px-5 py-2.5 flex items-center justify-between font-mono text-[10px] text-faint">
        <span>SENTINELA · protocolo iot · dados {state.dataMode === "mqtt" ? "MQTT em tempo real" : "simulados no front"}</span>
        <span className="tabular-nums">
          broker 12ms · uptime {fmtUptime(state.uptimeSec)} · {zones.length} zonas · {critical} crítica
        </span>
      </footer>
    </div>
  );
}

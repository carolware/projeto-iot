import { sendCommand, type FeedEvent, type SimState, type Zone } from "@/lib/sim";
import { StatusDot } from "./StatusDot";

function SatellitePanel({ zone }: { zone: Zone | undefined }) {
  const confirmed = zone?.firmsConfirmed ?? false;
  return (
    <section
      className="rounded-md border border-hair bg-surface p-3.5"
      style={{ animation: "enter .5s cubic-bezier(0.32,0.72,0,1) both", animationDelay: "200ms" }}
    >
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] tracking-[0.2em] text-faint">02 — CONFIRMAÇÃO SATÉLITE</span>
        <span className="font-mono text-[9px] text-teal">NASA FIRMS</span>
      </div>
      <div className="mt-3 rounded border border-hair bg-ink/70 aspect-[4/3] grid place-items-center relative overflow-hidden">
        <svg viewBox="0 0 200 150" className="absolute inset-0 w-full h-full" preserveAspectRatio="none" fill="none">
          <g stroke="var(--color-hair)" strokeWidth="1">
            <line x1="0" y1="37" x2="200" y2="37" />
            <line x1="0" y1="75" x2="200" y2="75" />
            <line x1="0" y1="112" x2="200" y2="112" />
            <line x1="50" y1="0" x2="50" y2="150" />
            <line x1="100" y1="0" x2="100" y2="150" />
            <line x1="150" y1="0" x2="150" y2="150" />
          </g>
          <circle cx="100" cy="75" r="46" stroke="var(--color-teal)" strokeOpacity="0.25" strokeWidth="1" strokeDasharray="3 5" style={{ animation: "sweep 6s linear infinite" }} />
          <circle cx="100" cy="75" r="24" stroke="var(--color-teal)" strokeOpacity="0.35" strokeWidth="1" strokeDasharray="3 5" style={{ animation: "sweep 4s linear infinite reverse" }} />
          <circle cx="100" cy="75" r="2" fill="var(--color-teal)" />
        </svg>
        <div className="relative z-10 flex flex-col items-center">
          {confirmed ? (
            <>
              <span className="size-3 rounded-full bg-danger" style={{ animation: "softpulse 1.2s ease-in-out infinite" }} />
              <span className="mt-2 font-mono text-[10px] text-fg tracking-wide">FOCO CONFIRMADO</span>
              <span className="font-mono text-[9px] text-faint">
                {zone?.coords} · conf {zone?.firmsConf}
              </span>
            </>
          ) : (
            <>
              <span className="size-3 rounded-full bg-teal/60" style={{ animation: "softpulse 2s ease-in-out infinite" }} />
              <span className="mt-2 font-mono text-[10px] text-fg tracking-wide">VARREDURA ATIVA</span>
              <span className="font-mono text-[9px] text-faint">sem foco confirmado na área</span>
            </>
          )}
        </div>
      </div>
      <div className="mt-2 font-mono text-[9px] text-faint leading-relaxed">
        {confirmed ? "3 focos na área · FRP 2140 W" : "0 focos na área"} · atualizado {new Date().toLocaleTimeString("pt-BR", { hour12: false, hour: "2-digit", minute: "2-digit" })}
      </div>
    </section>
  );
}

const KIND_STYLE: Record<FeedEvent["kind"], { border: string; bg: string; time: string }> = {
  critico: { border: "border-danger", bg: "bg-danger/10", time: "text-danger" },
  atuador: { border: "border-ember", bg: "bg-ember/10", time: "text-ember" },
  regra: { border: "border-warn", bg: "bg-warn/5", time: "text-warn" },
  satelite: { border: "border-teal", bg: "bg-teal/5", time: "text-teal" },
  telemetria: { border: "border-hair", bg: "bg-surface-2", time: "text-faint" },
  sistema: { border: "border-hair", bg: "bg-surface-2", time: "text-faint" },
};

function AlertsFeed({ feed }: { feed: FeedEvent[] }) {
  return (
    <section
      className="rounded-md border border-hair bg-surface p-3.5"
      style={{ animation: "enter .5s cubic-bezier(0.32,0.72,0,1) both", animationDelay: "280ms" }}
    >
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] tracking-[0.2em] text-faint">03 — FEED DE ALERTAS</span>
        <span className="font-mono text-[9px] text-ok flex items-center gap-1.5">
          <StatusDot color="bg-ok" duration={1.4} />
          LIVE
        </span>
      </div>
      <ul className="mt-3 space-y-2 font-mono text-[11px]">
        {feed.map((e) => {
          const s = KIND_STYLE[e.kind];
          return (
            <li
              key={e.id}
              className={`rounded border-l-2 ${s.border} ${s.bg} px-2.5 py-1.5`}
              style={{ animation: "tick .4s cubic-bezier(0.32,0.72,0,1) both" }}
            >
              <div className="flex items-center gap-2">
                <span className={s.time}>{e.time}</span>
                <span className="text-fg">{e.title}</span>
              </div>
              <div className="text-faint text-[10px] mt-0.5 break-all">{e.detail}</div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function MqttCommands({ zone }: { zone: Zone | undefined }) {
  const target = zone?.id ?? "serra-leste";
  return (
    <section
      className="rounded-md border border-hair bg-surface p-3.5"
      style={{ animation: "enter .5s cubic-bezier(0.32,0.72,0,1) both", animationDelay: "360ms" }}
    >
      <span className="font-mono text-[10px] tracking-[0.2em] text-faint">04 — COMANDOS MQTT</span>
      <div className="mt-3 rounded bg-ink/70 border border-hair px-2.5 py-2 font-mono text-[11px] break-all">
        <span className="text-teal">MQTT&gt;</span>{" "}
        <span className="text-fg">incendio/{target}/alerta</span>{" "}
        <span className="text-faint">{'{"risco":"critico","acao":"acionar_brigada"}'}</span>
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2">
        <button
          onClick={() => sendCommand(target, "acionar_brigada")}
          className="rounded border border-ember/50 bg-ember/10 hover:bg-ember/20 py-2 font-mono text-[10px] text-ember transition-colors cursor-pointer"
        >
          ACIONAR
        </button>
        <button
          onClick={() => sendCommand(target, "reforcar_monitoramento")}
          className="rounded border border-hair bg-surface-2 hover:bg-surface py-2 font-mono text-[10px] text-fg transition-colors cursor-pointer"
        >
          REFORÇAR
        </button>
        <button
          onClick={() => sendCommand(target, "silenciar_alerta")}
          className="rounded border border-hair bg-surface-2 hover:bg-surface py-2 font-mono text-[10px] text-dim transition-colors cursor-pointer"
        >
          SILENCIAR
        </button>
      </div>
    </section>
  );
}

function EventLog({ log }: { log: string[] }) {
  return (
    <section
      className="rounded-md border border-hair bg-surface p-3.5"
      style={{ animation: "enter .5s cubic-bezier(0.32,0.72,0,1) both", animationDelay: "440ms" }}
    >
      <span className="font-mono text-[10px] tracking-[0.2em] text-faint">05 — LOG DE EVENTOS</span>
      <div className="mt-3 font-mono text-[10px] text-faint leading-relaxed tabular-nums">
        {log.map((line, i) => {
          const tag = line.slice(0, 6);
          const rest = line.slice(6);
          const tagCls = tag.includes("OK")
            ? "text-ok"
            : tag.includes("!")
              ? "text-warn"
              : tag.includes("SAT")
                ? "text-teal"
                : tag.includes("TX")
                  ? "text-ember"
                  : "text-faint";
          return (
            <div key={`${i}-${line}`}>
              <span className={tagCls}>{tag}</span>
              {rest}
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function SidePanel({ state }: { state: SimState }) {
  const hottest = state.zones.find((z) => z.risk === "critico") ?? state.zones.find((z) => z.risk === "alto");
  return (
    <aside className="lg:sticky lg:top-[57px] flex flex-col gap-4">
      <SatellitePanel zone={hottest} />
      <AlertsFeed feed={state.feed} />
      <MqttCommands zone={hottest} />
      <EventLog log={state.log} />
    </aside>
  );
}

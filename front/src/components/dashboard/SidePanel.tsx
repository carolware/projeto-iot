import { useState } from "react";
import { sendIotCommand, type FeedEvent, type SimState, type Zone } from "@/lib/iot";
import { StatusDot } from "./StatusDot";
import { FireMap } from "./FireMap";

/** Seta que gira conforme o painel está aberto/fechado. */
function CollapseToggle({ open, onClick }: { open: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      aria-label={open ? "Minimizar painel" : "Expandir painel"}
      className="text-faint hover:text-fg transition-colors cursor-pointer font-mono text-[10px] px-1 -mr-1"
    >
      <span style={{ display: "inline-block", transform: open ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform .2s" }}>
        ▾
      </span>
    </button>
  );
}

function MapPanel({ zones, hotZone }: { zones: Zone[]; hotZone: Zone | undefined }) {
  return (
    <section
      className="rounded-md border border-hair bg-surface p-3.5"
      style={{ animation: "enter .5s cubic-bezier(0.32,0.72,0,1) both", animationDelay: "200ms" }}
    >
      <div className="flex items-center justify-between mb-3">
        <span className="font-mono text-[10px] tracking-[0.2em] text-faint">02 — MAPA DE RISCO</span>
        <span className="font-mono text-[9px] text-teal">OSM + NASA FIRMS</span>
      </div>
      <FireMap zones={zones} />
      {hotZone?.firmsConfirmed && (
        <div className="mt-2 font-mono text-[9px] text-danger flex items-center gap-1.5">
          <span className="size-1.5 rounded-full bg-danger inline-block" style={{ animation: "softpulse 1.2s ease-in-out infinite" }} />
          FIRMS: {hotZone.name} — foco satelital confirmado
        </div>
      )}
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
  const [open, setOpen] = useState(true);
  return (
    <section
      className="rounded-md border border-hair bg-surface p-3.5"
      style={{ animation: "enter .5s cubic-bezier(0.32,0.72,0,1) both", animationDelay: "280ms" }}
    >
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] tracking-[0.2em] text-faint">03 — FEED DE ALERTAS</span>
        <div className="flex items-center gap-3">
          <span className="font-mono text-[9px] text-ok flex items-center gap-1.5">
            <StatusDot color="bg-ok" duration={1.4} />
            LIVE
          </span>
          <CollapseToggle open={open} onClick={() => setOpen((o) => !o)} />
        </div>
      </div>
      {open && (
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
      )}
    </section>
  );
}

function MqttCommands({ zone }: { zone: Zone | undefined }) {
  const [open, setOpen] = useState(true);
  const target = zone?.id ?? "serra-leste";
  return (
    <section
      className="rounded-md border border-hair bg-surface p-3.5"
      style={{ animation: "enter .5s cubic-bezier(0.32,0.72,0,1) both", animationDelay: "360ms" }}
    >
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] tracking-[0.2em] text-faint">04 — COMANDOS MQTT</span>
        <CollapseToggle open={open} onClick={() => setOpen((o) => !o)} />
      </div>
      {open && (
        <>
          <div className="mt-3 rounded bg-ink/70 border border-hair px-2.5 py-2 font-mono text-[11px] break-all">
            <span className="text-teal">MQTT&gt;</span>{" "}
            <span className="text-fg">{zone?.topic.replace("/sensor/+", "/atuador/alerta")}</span>{" "}
            <span className="text-faint">{'{"risco":"critico","acao":"acionar_brigada"}'}</span>
          </div>
          <div className="mt-2 grid grid-cols-3 gap-2">
            <button
              onClick={() => sendIotCommand(target, "acionar_brigada")}
              className="rounded border border-ember/50 bg-ember/10 hover:bg-ember/20 py-2 font-mono text-[10px] text-ember transition-colors cursor-pointer"
            >
              ACIONAR
            </button>
            <button
              onClick={() => sendIotCommand(target, "reforcar_monitoramento")}
              className="rounded border border-hair bg-surface-2 hover:bg-surface py-2 font-mono text-[10px] text-fg transition-colors cursor-pointer"
            >
              REFORÇAR
            </button>
            <button
              onClick={() => sendIotCommand(target, "silenciar_alerta")}
              className="rounded border border-hair bg-surface-2 hover:bg-surface py-2 font-mono text-[10px] text-dim transition-colors cursor-pointer"
            >
              SILENCIAR
            </button>
          </div>
        </>
      )}
    </section>
  );
}

function EventLog({ log }: { log: string[] }) {
  const [open, setOpen] = useState(true);
  return (
    <section
      className="rounded-md border border-hair bg-surface p-3.5"
      style={{ animation: "enter .5s cubic-bezier(0.32,0.72,0,1) both", animationDelay: "440ms" }}
    >
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] tracking-[0.2em] text-faint">05 — LOG DE EVENTOS</span>
        <CollapseToggle open={open} onClick={() => setOpen((o) => !o)} />
      </div>
      {open && (
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
      )}
    </section>
  );
}

export function SidePanel({ state }: { state: SimState }) {
  const hottest = state.zones.find((z) => z.risk === "critico") ?? state.zones.find((z) => z.risk === "alto");
  return (
    <aside className="lg:sticky lg:top-[57px] flex flex-col gap-4">
      <MapPanel zones={state.zones} hotZone={hottest} />
      <AlertsFeed feed={state.feed} />
      <MqttCommands zone={hottest} />
      <EventLog log={state.log} />
    </aside>
  );
}

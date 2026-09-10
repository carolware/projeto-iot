import { RISK_LABEL, type Zone } from "@/lib/sim";
import { StatusDot } from "./StatusDot";

function delta(cur: number, prev: number, unit = "") {
  const d = Math.round((cur - prev) * 10) / 10;
  if (d > 0) return { text: `▲ +${d}${unit}`, cls: "text-warn" };
  if (d < 0) return { text: `▼ ${d}${unit}`, cls: "text-faint" };
  return { text: `— 0${unit}`, cls: "text-faint" };
}

const RISK_BADGE: Record<string, string> = {
  baixo: "bg-ok/15 text-ok",
  medio: "bg-warn/20 text-warn",
  alto: "bg-warn text-ink",
  critico: "bg-danger text-ink",
  offline: "bg-hair text-dim",
};

function Metric({
  label,
  value,
  unit,
  tone,
  dot,
}: {
  label: string;
  value: string;
  unit: string;
  tone: string;
  dot: string;
}) {
  return (
    <div className="rounded bg-ink/60 border border-hair px-3 py-2">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] text-faint">{label}</span>
        <StatusDot color={dot} duration={1} />
      </div>
      <div className={`mt-1 font-mono text-[28px] leading-none font-medium tabular-nums ${tone}`}>
        {value}
        <span className="text-[14px] text-faint">{unit}</span>
      </div>
    </div>
  );
}

function smokeTone(z: Zone) {
  return z.smoke >= 85 ? "text-danger" : z.smoke >= 60 ? "text-warn" : "text-fg";
}
function humTone(z: Zone) {
  return z.humidity < 25 ? "text-warn" : "text-fg";
}
function tempTone(z: Zone) {
  return z.temp >= 40 ? "text-danger" : "text-fg";
}

export function ZoneHero({ zone, index }: { zone: Zone; index: number }) {
  const dT = delta(zone.temp, zone.prevTemp, "°");
  const dH = delta(zone.humidity, zone.prevHumidity, "%");
  const dS = delta(zone.smoke, zone.prevSmoke);
  const max = Math.max(...zone.smokeHistory, 1);

  return (
    <article
      className="sm:col-span-2 xl:col-span-3 rounded-md border border-danger/60 bg-surface ring-1 ring-danger/30 p-4"
      style={{ animation: "enter .5s cubic-bezier(0.32,0.72,0,1) both" }}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] text-danger tracking-[0.2em]">●</span>
            <h2 className="font-display text-[26px] leading-none tracking-wide text-fg">{zone.name}</h2>
            <span className={`rounded-[3px] px-2 py-0.5 font-mono text-[10px] font-bold tracking-widest ${RISK_BADGE[zone.risk]}`}>
              {RISK_LABEL[zone.risk]}
            </span>
          </div>
          <div className="mt-1 font-mono text-[10px] text-faint">
            SENSOR {zone.sensorId} · {zone.coords} · ALT 1180m
          </div>
        </div>
        <div className="text-right font-mono text-[10px] text-faint leading-relaxed">
          <div>
            MQTT <span className="text-fg">{zone.topic}</span>
          </div>
          <div>
            ÚLT. PING <span className="text-ok">há {zone.lastPingSec}s</span>
          </div>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-3 gap-3">
        <div>
          <Metric label="TEMP" value={zone.temp.toFixed(1)} unit="°C" tone={tempTone(zone)} dot="bg-danger" />
          <div className={`mt-1 font-mono text-[10px] ${dT.cls}`}>{dT.text}</div>
        </div>
        <div>
          <Metric label="UMIDADE" value={String(zone.humidity)} unit="%" tone={humTone(zone)} dot="bg-warn" />
          <div className={`mt-1 font-mono text-[10px] ${dH.cls}`}>{dH.text}</div>
        </div>
        <div>
          <Metric label="ÍNDICE FOGO" value={String(zone.smoke)} unit=" pts" tone={smokeTone(zone)} dot="bg-danger" />
          <div className={`mt-1 font-mono text-[10px] ${dS.cls}`}>{dS.text}</div>
        </div>
      </div>

      <div className="mt-3 flex flex-col md:flex-row md:items-center gap-3">
        <div className="flex-1 flex items-center justify-between">
          <span className="font-mono text-[10px] text-faint">ÍNDICE DE FOGO · ÚLTIMAS LEITURAS</span>
          <div className="flex items-end gap-[3px] h-8">
            {zone.smokeHistory.map((v, i) => (
              <span
                key={`${index}-${i}-${v}`}
                className={`w-1.5 rounded-sm ${v >= 85 ? "bg-danger" : v >= 60 ? "bg-ember" : "bg-ember/40"}`}
                style={{ height: `${Math.max(12, (v / max) * 100)}%` }}
              />
            ))}
          </div>
        </div>
        {(zone.risk === "alto" || zone.risk === "critico") && (
          <div className="rounded border border-warn/40 bg-warn/10 px-3 py-2">
            <div className="font-mono text-[10px] text-warn tracking-wide">
              ⚠ REGRA DISPARADA · ÍNDICE {zone.smoke} + UMIDADE {zone.humidity}%
            </div>
            <div className="mt-1 font-mono text-[10px] text-faint">
              → FIRMS {zone.firmsConfirmed ? `${zone.firmsHotspots.length} foco(s) detectado(s)` : "sem foco na última varredura"} · {zone.coords}
            </div>
          </div>
        )}
      </div>
    </article>
  );
}

export function ZoneCard({ zone, index }: { zone: Zone; index: number }) {
  if (!zone.online) {
    return (
      <article
        className="rounded-md border border-hair bg-surface p-3.5 opacity-70"
        style={{ animation: `enter .5s cubic-bezier(0.32,0.72,0,1) both`, animationDelay: `${index * 60}ms` }}
      >
        <div className="flex items-center justify-between">
          <h3 className="font-display text-[16px] tracking-wide text-fg">{zone.name}</h3>
          <span className={`rounded-[3px] px-1.5 py-0.5 font-mono text-[9px] font-bold tracking-widest ${RISK_BADGE["offline"]}`}>
            OFFLINE
          </span>
        </div>
        <div className="mt-0.5 font-mono text-[9px] text-faint">
          {zone.sensorId} · {zone.coords}
        </div>
        <div className="mt-3 grid grid-cols-3 gap-2 font-mono tabular-nums text-faint">
          {["TEMP", "UMID.", "ÍNDICE"].map((l) => (
            <div key={l} className="text-[10px]">
              {l}
              <div className="text-[15px] mt-0.5">—</div>
            </div>
          ))}
        </div>
        <div className="mt-2 font-mono text-[9px] text-danger">sem sinal há {zone.lastPingSec}s · retry 3/5</div>
      </article>
    );
  }

  const border =
    zone.risk === "alto" || zone.risk === "critico" ? "border-warn/30" : "border-hair";

  return (
    <article
      className={`rounded-md border ${border} bg-surface p-3.5`}
      style={{ animation: `enter .5s cubic-bezier(0.32,0.72,0,1) both`, animationDelay: `${index * 60}ms` }}
    >
      <div className="flex items-center justify-between">
        <h3 className="font-display text-[16px] tracking-wide text-fg">{zone.name}</h3>
        <span className={`rounded-[3px] px-1.5 py-0.5 font-mono text-[9px] font-bold tracking-widest ${RISK_BADGE[zone.risk]}`}>
          {RISK_LABEL[zone.risk]}
        </span>
      </div>
      <div className="mt-0.5 font-mono text-[9px] text-faint">
        {zone.sensorId} · {zone.coords}
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2 font-mono tabular-nums">
        <div className="text-[10px] text-faint">
          TEMP
          <div className={`text-[15px] mt-0.5 ${tempTone(zone)}`}>{zone.temp.toFixed(1)}°</div>
        </div>
        <div className="text-[10px] text-faint">
          UMID.
          <div className={`text-[15px] mt-0.5 ${humTone(zone)}`}>{zone.humidity}%</div>
        </div>
        <div className="text-[10px] text-faint">
          ÍNDICE
          <div className={`text-[15px] mt-0.5 ${smokeTone(zone)}`}>{zone.smoke}</div>
        </div>
      </div>
    </article>
  );
}

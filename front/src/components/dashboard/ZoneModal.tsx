import { useEffect } from "react";
import { RISK_LABEL, type FeedEvent, type SimState, type Zone } from "@/lib/sim";

const RISK_BADGE: Record<string, string> = {
  baixo: "bg-ok/15 text-ok",
  medio: "bg-warn/20 text-warn",
  alto: "bg-warn text-ink",
  critico: "bg-danger text-ink",
  offline: "bg-hair text-dim",
};

const KIND_STYLE: Record<FeedEvent["kind"], { border: string; bg: string; time: string }> = {
  critico: { border: "border-danger", bg: "bg-danger/10", time: "text-danger" },
  atuador: { border: "border-ember", bg: "bg-ember/10", time: "text-ember" },
  regra: { border: "border-warn", bg: "bg-warn/5", time: "text-warn" },
  satelite: { border: "border-teal", bg: "bg-teal/5", time: "text-teal" },
  telemetria: { border: "border-hair", bg: "bg-surface-2", time: "text-faint" },
  sistema: { border: "border-hair", bg: "bg-surface-2", time: "text-faint" },
};

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-hair/60 py-1.5 last:border-0">
      <span className="text-faint">{label}</span>
      <span className="text-fg text-right">{value}</span>
    </div>
  );
}

function MiniMetric({ label, value, unit, tone }: { label: string; value: string; unit: string; tone: string }) {
  return (
    <div className="rounded bg-ink/60 border border-hair px-3 py-2">
      <div className="font-mono text-[10px] text-faint">{label}</div>
      <div className={`mt-1 font-mono text-[22px] leading-none font-medium tabular-nums ${tone}`}>
        {value}
        <span className="text-[12px] text-faint">{unit}</span>
      </div>
    </div>
  );
}

export function ZoneModal({ zone, state, onClose }: { zone: Zone; state: SimState; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const zoneFeed = state.feed.filter((e) => e.detail.includes(zone.id) || e.title.includes(zone.name));
  const zoneLog = state.log.filter((line) => line.includes(zone.id));
  const tempTone = zone.temp >= 40 ? "text-danger" : "text-fg";
  const humTone = zone.humidity < 25 ? "text-warn" : "text-fg";
  const smokeTone = zone.smoke >= 85 ? "text-danger" : zone.smoke >= 60 ? "text-warn" : "text-fg";

  return (
    <div
      className="fixed inset-0 z-50 bg-black/85 flex items-center justify-center p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Detalhes da zona ${zone.name}`}
    >
      <div
        className="w-full max-w-2xl max-h-[85vh] overflow-y-auto rounded-md border border-hair bg-surface p-5"
        onClick={(e) => e.stopPropagation()}
        style={{ animation: "enter .3s cubic-bezier(0.32,0.72,0,1) both" }}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-display text-[24px] leading-none tracking-wide text-fg">{zone.name}</h2>
              <span className={`rounded-[3px] px-2 py-0.5 font-mono text-[10px] font-bold tracking-widest ${RISK_BADGE[zone.risk]}`}>
                {RISK_LABEL[zone.risk]}
              </span>
            </div>
            <div className="mt-1 font-mono text-[10px] text-faint">
              SENSOR {zone.sensorId} · {zone.coords}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Fechar"
            className="font-mono text-[13px] text-faint hover:text-fg transition-colors cursor-pointer px-2"
          >
            ✕
          </button>
        </div>

        {/* Métricas atuais */}
        <div className="mt-4 grid grid-cols-3 gap-3">
          <MiniMetric label="TEMP" value={zone.temp.toFixed(1)} unit="°C" tone={tempTone} />
          <MiniMetric label="UMIDADE" value={String(zone.humidity)} unit="%" tone={humTone} />
          <MiniMetric label="ÍNDICE FOGO" value={String(zone.smoke)} unit=" pts" tone={smokeTone} />
        </div>

        {/* Informações da zona */}
        <div className="mt-4 rounded bg-ink/40 border border-hair px-3 py-2 font-mono text-[11px]">
          <InfoRow label="STATUS" value={zone.online ? "ONLINE" : "OFFLINE"} />
          <InfoRow label="TÓPICO MQTT" value={zone.topic} />
          <InfoRow label="COORDENADAS" value={zone.coords} />
          <InfoRow
            label="FIRMS"
            value={zone.firmsConfirmed ? `${zone.firmsHotspots.length} foco(s) confirmado(s)` : "sem foco ativo"}
          />
          {zone.firmsUpdatedAt && (
            <InfoRow label="FIRMS ATUALIZADO" value={new Date(zone.firmsUpdatedAt).toLocaleTimeString("pt-BR")} />
          )}
          <InfoRow label="RAIO DE BUSCA FIRMS" value={`${zone.firmsRadiusKm} km`} />
        </div>

        {/* Focos FIRMS detectados */}
        {zone.firmsHotspots.length > 0 && (
          <div className="mt-4">
            <div className="font-mono text-[10px] tracking-[0.2em] text-faint mb-2">FOCOS DETECTADOS</div>
            <div className="space-y-1.5">
              {zone.firmsHotspots.map((h) => (
                <div key={h.id} className="rounded border border-danger/30 bg-danger/5 px-2.5 py-1.5 font-mono text-[10px]">
                  <div className="flex items-center justify-between">
                    <span className="text-danger font-bold">{h.frp} MW</span>
                    <span className="text-faint">{h.distanceKm} km do centro</span>
                  </div>
                  <div className="text-faint mt-0.5">
                    {h.latitude}, {h.longitude} · {h.satellite} · confiança {h.confidence}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Histórico de fumaça */}
        <div className="mt-4">
          <div className="font-mono text-[10px] tracking-[0.2em] text-faint mb-2">ÍNDICE DE FOGO · ÚLTIMAS LEITURAS</div>
          <div className="flex items-end gap-1 h-10">
            {zone.smokeHistory.map((v, i) => {
              const max = Math.max(...zone.smokeHistory, 1);
              return (
                <span
                  key={i}
                  className={`flex-1 rounded-sm ${v >= 85 ? "bg-danger" : v >= 60 ? "bg-ember" : "bg-ember/40"}`}
                  style={{ height: `${Math.max(12, (v / max) * 100)}%` }}
                />
              );
            })}
          </div>
        </div>

        {/* Log específico da zona */}
        <div className="mt-4">
          <div className="font-mono text-[10px] tracking-[0.2em] text-faint mb-2">
            REGISTROS DE {zone.name} ({zoneFeed.length})
          </div>
          {zoneFeed.length === 0 ? (
            <div className="font-mono text-[10px] text-faint">Nenhum registro recente para esta zona.</div>
          ) : (
            <ul className="space-y-1.5 font-mono text-[10px] max-h-48 overflow-y-auto">
              {zoneFeed.map((e) => {
                const s = KIND_STYLE[e.kind];
                return (
                  <li key={e.id} className={`rounded border-l-2 ${s.border} ${s.bg} px-2 py-1`}>
                    <div className="flex items-center gap-2">
                      <span className={s.time}>{e.time}</span>
                      <span className="text-fg">{e.title}</span>
                    </div>
                    <div className="text-faint mt-0.5 break-all">{e.detail}</div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {zoneLog.length > 0 && (
          <div className="mt-4">
            <div className="font-mono text-[10px] tracking-[0.2em] text-faint mb-2">LOG BRUTO</div>
            <div className="font-mono text-[10px] text-faint leading-relaxed">
              {zoneLog.map((line, i) => (
                <div key={i}>{line}</div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

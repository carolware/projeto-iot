/**
 * FireMap — wrapper SSR-safe para o mapa Leaflet.
 *
 * O Leaflet acessa `window` ao ser importado, quebrando o SSR (Node.js).
 * Para evitar isso, usamos duas técnicas combinadas:
 *   1. `React.lazy()` — o módulo LeafletMapCore só é carregado quando o
 *      componente for renderizado pela primeira vez.
 *   2. Guard `mounted` via useEffect — o componente só renderiza o mapa
 *      depois que o React confirma que estamos no browser.
 *
 * Resultado: o servidor renderiza apenas o placeholder, e o mapa real
 * é hidratado e inicializado exclusivamente no cliente.
 */
import { useState, useEffect, lazy, Suspense } from "react";
import type { Zone } from "@/lib/iot";

// Não importar nada de LeafletMapCore aqui (seria avaliado no servidor)
const FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@-12,-51,6z";

// Dynamic import: LeafletMapCore nunca é avaliado no servidor
const LeafletMapCore = lazy(() => import("./LeafletMapCore"));

const RISK_COLOR: Record<string, string> = {
  baixo:   "#22c55e",
  medio:   "#f59e0b",
  alto:    "#f97316",
  critico: "#ef4444",
  offline: "#6b7280",
};

const RISK_LABEL: Record<string, string> = {
  baixo: "BAIXO", medio: "MÉDIO", alto: "ALTO", critico: "CRÍTICO",
};

function MapPlaceholder() {
  return (
    <div className="w-full rounded border border-hair bg-ink/70 aspect-[4/3] flex items-center justify-center">
      <span className="font-mono text-[10px] text-faint animate-pulse">carregando mapa...</span>
    </div>
  );
}

function RiskLegend() {
  return (
    <div className="flex items-center gap-3 mt-1.5 px-0.5 flex-wrap">
      {(["baixo", "medio", "alto", "critico"] as const).map((r) => (
        <div key={r} className="flex items-center gap-1">
          <span className="size-2 rounded-full inline-block" style={{ background: RISK_COLOR[r] }} />
          <span className="font-mono text-[9px] text-faint uppercase">{RISK_LABEL[r]}</span>
        </div>
      ))}
    </div>
  );
}

export function FireMap({ zones }: { zones: Zone[] }) {
  const [mounted,  setMounted]  = useState(false);
  const [expanded, setExpanded] = useState(false);

  // Só executa no browser — garante que o Leaflet não é renderizado no servidor
  useEffect(() => { setMounted(true); }, []);

  if (!mounted) {
    return (
      <>
        <MapPlaceholder />
        <RiskLegend />
      </>
    );
  }

  return (
    <>
      {/* ── Mapa pequeno (sidebar) ── */}
      <div
        className="relative w-full rounded border border-hair overflow-hidden aspect-[4/3] cursor-pointer group"
        onClick={() => setExpanded(true)}
        title="Clique para expandir"
      >
        <Suspense fallback={<MapPlaceholder />}>
          <LeafletMapCore zones={zones} zoom={5} interactive={false} />
        </Suspense>

        {/* Overlay hover */}
        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/35 transition-colors flex items-end justify-center pb-3 pointer-events-none">
          <span className="opacity-0 group-hover:opacity-100 font-mono text-[9px] text-white bg-black/70 px-2 py-1 rounded border border-white/20 transition-opacity">
            EXPANDIR MAPA
          </span>
        </div>

        {/* Botão NASA FIRMS */}
        <a
          href={FIRMS_URL}
          target="_blank"
          rel="noreferrer"
          className="absolute top-2 right-2 z-[1000] font-mono text-[8px] bg-black/80 text-teal px-1.5 py-0.5 rounded border border-teal/30 hover:bg-teal/20 transition-colors"
          onClick={(e) => e.stopPropagation()}
        >
          NASA FIRMS ↗
        </a>
      </div>

      <RiskLegend />

      {/* ── Modal expandido (tela cheia) ── */}
      {expanded && (
        <div
          className="fixed inset-0 z-50 bg-black/85 flex flex-col"
          onClick={() => setExpanded(false)}
        >
          {/* Barra superior */}
          <div className="flex items-center justify-between px-4 py-2.5 bg-surface border-b border-hair shrink-0">
            <div className="flex items-center gap-4">
              <span className="font-mono text-[11px] text-fg tracking-widest">
                MAPA DE RISCO — SENTINELA IoT
              </span>
              <span className="font-mono text-[9px] text-faint">
                Goiás · Tocantins · Amazônia (PA)
              </span>
            </div>
            <div className="flex items-center gap-5 flex-wrap justify-end">
              <div className="flex items-center gap-3">
                {(["baixo", "medio", "alto", "critico"] as const).map((r) => (
                  <div key={r} className="flex items-center gap-1.5">
                    <span className="size-2.5 rounded-full" style={{ background: RISK_COLOR[r] }} />
                    <span className="font-mono text-[9px] text-faint uppercase">{RISK_LABEL[r]}</span>
                  </div>
                ))}
              </div>
              <a
                href={FIRMS_URL}
                target="_blank"
                rel="noreferrer"
                className="font-mono text-[10px] text-teal hover:underline"
                onClick={(e) => e.stopPropagation()}
              >
                VER NASA FIRMS ↗
              </a>
              <button
                className="font-mono text-[11px] text-faint hover:text-fg transition-colors"
                onClick={() => setExpanded(false)}
              >
                ✕ FECHAR
              </button>
            </div>
          </div>

          {/* Mapa interativo em tela cheia */}
          <div className="flex-1" onClick={(e) => e.stopPropagation()}>
            <Suspense fallback={<div className="w-full h-full flex items-center justify-center"><span className="text-faint font-mono text-sm">carregando...</span></div>}>
              <LeafletMapCore zones={zones} zoom={6} interactive />
            </Suspense>
          </div>
        </div>
      )}
    </>
  );
}

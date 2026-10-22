/**
 * iot.ts — camada de dados do painel, sobre a API da central:
 *
 *   GET /api/v1/painel        estado consolidado por local (react-query)
 *   WS  /api/v1/tempo-real    telemetria, eventos e alarmes do inquilino; cada
 *                             mensagem alimenta o feed e pede um painel novo
 *   POST /dispositivos/{atuador}/comandos   comandos do operador
 *
 * O risco de cada zona vem da central (classificado pelas regras do
 * inquilino); o front não decide nada sozinho.
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSessao } from "./auth";
import { WS_URL } from "./config";
import type { FeedEvent, FirmsHotspot, Json, Painel, SimState, SitePainel, Zone } from "./tipos";

export type { FeedEvent, FirmsHotspot, Risk, SimState, Zone } from "./tipos";
export { RISK_LABEL } from "./tipos";

const HISTORICO = 7;

function agora() {
  return new Date().toLocaleTimeString("pt-BR", { hour12: false });
}

// ── Relógio/uptime: store próprio, só o cabeçalho re-renderiza a cada 1 s ────
const inicio = Date.now();
let relogio = { clock: "", uptimeSec: 0 };
const ouvintesRelogio = new Set<() => void>();
let relogioLigado = false;

export function useClock(): { clock: string; uptimeSec: number } {
  return useSyncExternalStore(
    (cb) => {
      ouvintesRelogio.add(cb);
      if (!relogioLigado && typeof window !== "undefined") {
        relogioLigado = true;
        window.setInterval(() => {
          relogio = { clock: agora(), uptimeSec: Math.floor((Date.now() - inicio) / 1000) };
          ouvintesRelogio.forEach((l) => l());
        }, 1000);
      }
      return () => ouvintesRelogio.delete(cb);
    },
    () => relogio,
    () => relogio,
  );
}

// ── Painel → zonas ────────────────────────────────────────────────────────────

function hotspots(brutos: Json[] = []): FirmsHotspot[] {
  return brutos.flatMap((h) =>
    Number.isFinite(Number(h.latitude)) && Number.isFinite(Number(h.longitude))
      ? [
          {
            id: String(h.id ?? `${h.latitude}-${h.longitude}`),
            latitude: Number(h.latitude),
            longitude: Number(h.longitude),
            frp: Number(h.frp) || 0,
            confidence: String(h.confidence ?? "n/d"),
            satellite: String(h.satellite ?? "n/d"),
            instrument: String(h.instrument ?? "n/d"),
            acquiredAt: h.acquired_at ? String(h.acquired_at) : null,
            daynight: String(h.daynight ?? "n/d"),
            distanceKm: Number(h.distance_km) || 0,
          },
        ]
      : [],
  );
}

function paraZona(site: SitePainel, inquilino: string, anterior?: Zone): Zone | null {
  const estacao = site.dispositivos.find((d) => d.tipo === "estacao");
  if (!estacao) return null;
  const sat = site.dispositivos.find((d) => d.tipo === "conector")?.satelite ?? null;
  const v = (g: string) => estacao.leituras[g]?.valor ?? 0;
  const historico = estacao.historico_fumaca.slice(-HISTORICO);
  const contato = estacao.ultimo_contato_em ? Date.parse(estacao.ultimo_contato_em) : undefined;
  const pontos = hotspots(sat?.hotspots);
  return {
    id: site.slug,
    name: site.nome.toUpperCase(),
    sensorId: estacao.slug,
    coords: `${site.lat},${site.lon}`,
    topic: `${inquilino}/${site.slug}/${estacao.dominio}/${estacao.slug}/telemetria`,
    temp: v("temperatura"),
    humidity: v("umidade"),
    smoke: v("fumaca"),
    prevTemp: anterior?.temp ?? v("temperatura"),
    prevHumidity: anterior?.humidity ?? v("umidade"),
    prevSmoke: anterior?.smoke ?? v("fumaca"),
    online: estacao.online,
    risk: site.risco,
    alarmesAbertos: site.alarmes_abertos,
    firmsConfirmed: Boolean(sat?.confirmado),
    firmsConf: pontos.length,
    firmsHotspots: pontos,
    firmsUpdatedAt: sat?.atualizado_em ?? null,
    firmsRadiusKm: sat?.raio_km ?? site.raio_km ?? 16.7,
    firmsWindowHours: sat?.janela_horas ?? 24,
    smokeHistory: [...Array(Math.max(0, HISTORICO - historico.length)).fill(0), ...historico],
    lastPingSec: contato ? Math.max(0, Math.floor((Date.now() - contato) / 1000)) : 0,
    lastSeenAt: contato,
  };
}

// ── Mensagens do tempo real → feed ────────────────────────────────────────────

let sequencia = 0;

function paraFeed(m: Json): { evento: FeedEvent; linha: string } | null {
  const novo = (kind: FeedEvent["kind"], title: string, detail: string, linha: string) => ({
    evento: { id: ++sequencia, time: agora(), kind, title, detail },
    linha,
  });
  if (m.tipo === "alarme") {
    const a = m.alarme;
    const onde = a.site ?? a.dados?.origem?.site ?? "—";
    const titulo = `ALARME ${String(a.severidade).toUpperCase()} · ${m.evento}`;
    const kind = m.evento === "aberto" && a.severidade === "critico" ? "critico" : "regra";
    return novo(kind, titulo, `${onde} · ${a.regra ?? a.dados?.resumo ?? ""}`, `[ ! ] ${onde} → alarme ${m.evento}`);
  }
  const msg = m.mensagem;
  if (!msg) return null;
  const site = msg.contexto?.site ?? "—";
  if (m.tipo === "evento") {
    const dados = msg.dados ?? {};
    if (dados.evento === "varredura_firms") {
      return novo("satelite", dados.focos ? `FIRMS · ${dados.focos} foco(s)` : "FIRMS · nenhum foco", site, `[ SAT] ${site} → ${dados.focos ?? 0} foco(s)`);
    }
    if (dados.evento === "alarme_local") {
      return novo("critico", `BORDA · alarme ${dados.severidade}`, `${site} · ${msg.via ?? ""} · ${dados.regra}`, `[ ! ] ${site} → decisão local`);
    }
    return novo("atuador", `EVENTO · ${dados.evento ?? "evento"}`, `${msg.dispositivo} · ${dados.acao ?? ""}`, `[ OK ] ${msg.dispositivo} → ${dados.evento}`);
  }
  if (m.tipo === "telemetria" && msg.dispositivo?.startsWith("est-")) {
    const valores: Json = Object.fromEntries((msg.medidas ?? []).map((x: Json) => [x.grandeza, x.valor]));
    return novo(
      "telemetria",
      "TELEMETRIA recebida",
      `${site} {t:${valores.temperatura},h:${valores.umidade},f:${valores.fumaca}}${msg.via ? ` via ${msg.via}` : ""}`,
      `[ OK ] ${site} → leitura`,
    );
  }
  return null;
}

// ── Hook principal ────────────────────────────────────────────────────────────

export function usePainel() {
  const { api } = useSessao();
  return useQuery({
    queryKey: ["painel"],
    queryFn: () => api<Painel>("/api/v1/painel"),
    refetchInterval: 15_000,
  });
}

// ── Tempo real: UMA conexão WebSocket por aba, compartilhada pelos componentes ──

type Ouvinte = (m: Json) => void;
const tempoReal = {
  ws: null as WebSocket | null,
  ouvintes: new Set<Ouvinte>(),
  estado: new Set<(c: boolean) => void>(),
  conectado: false,
  espera: 1000,
  timer: undefined as number | undefined,
  token: null as null | (() => Promise<string>),
};

function avisarEstado(conectado: boolean) {
  tempoReal.conectado = conectado;
  tempoReal.estado.forEach((f) => f(conectado));
}

async function abrirTempoReal() {
  if (tempoReal.ws || !tempoReal.token || tempoReal.ouvintes.size === 0) return;
  let ws: WebSocket;
  try {
    ws = new WebSocket(`${WS_URL}/api/v1/tempo-real?token=${encodeURIComponent(await tempoReal.token())}`);
  } catch {
    tempoReal.timer = window.setTimeout(abrirTempoReal, tempoReal.espera);
    return;
  }
  tempoReal.ws = ws;
  ws.onopen = () => {
    tempoReal.espera = 1000;
    avisarEstado(true);
  };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    tempoReal.ouvintes.forEach((f) => f(m));
  };
  ws.onclose = () => {
    tempoReal.ws = null;
    avisarEstado(false);
    if (tempoReal.ouvintes.size > 0) {
      // reconecta com espera crescente (até 30 s), sempre com token renovado
      tempoReal.timer = window.setTimeout(abrirTempoReal, tempoReal.espera);
      tempoReal.espera = Math.min(tempoReal.espera * 2, 30_000);
    }
  };
}

/** Assina as mensagens do tempo real (telemetria, eventos, alarmes do inquilino); devolve se está conectado. */
export function useTempoReal(aoReceber?: Ouvinte): boolean {
  const { token } = useSessao();
  const [conectado, setConectado] = useState(tempoReal.conectado);
  const callback = useRef(aoReceber);
  callback.current = aoReceber;

  useEffect(() => {
    const ouvinte: Ouvinte = (m) => callback.current?.(m);
    tempoReal.token = token;
    tempoReal.ouvintes.add(ouvinte);
    tempoReal.estado.add(setConectado);
    setConectado(tempoReal.conectado);
    abrirTempoReal();
    return () => {
      tempoReal.ouvintes.delete(ouvinte);
      tempoReal.estado.delete(setConectado);
      if (tempoReal.ouvintes.size === 0) {
        window.clearTimeout(tempoReal.timer);
        tempoReal.ws?.close();
      }
    };
  }, [token]);
  return conectado;
}

export function useIot(): SimState {
  const { api, eu } = useSessao();
  const consulta = usePainel();
  const queryClient = useQueryClient();
  const [feed, setFeed] = useState<FeedEvent[]>([]);
  const [log, setLog] = useState<string[]>([]);
  const anteriores = useRef<Map<string, Zone>>(new Map());
  const recarga = useRef<number | undefined>(undefined);

  const registrar = useCallback((evento: FeedEvent, linha: string) => {
    setFeed((f) => [evento, ...f].slice(0, 12));
    setLog((l) => [linha, ...l].slice(0, 8));
  }, []);

  const conectado = useTempoReal((m) => {
    const item = paraFeed(m);
    if (item) registrar(item.evento, item.linha);
    if (m.tipo !== "conectado" && recarga.current === undefined) {
      // várias leituras chegam juntas: um painel novo a cada 2 s no máximo
      recarga.current = window.setTimeout(() => {
        recarga.current = undefined;
        queryClient.invalidateQueries({ queryKey: ["painel"] });
      }, 2000);
    }
  });

  const zonas = useMemo(() => {
    const painel = consulta.data;
    if (!painel) return [];
    const lista = painel.sites.flatMap((s) => {
      const z = paraZona(s, painel.inquilino, anteriores.current.get(s.slug));
      return z ? [z] : [];
    });
    anteriores.current = new Map(lista.map((z) => [z.id, z]));
    return lista;
  }, [consulta.data]);

  const comandar = useCallback(
    async (zoneId: string, acao: string) => {
      const atuador = `atuador-${zoneId}-01`;
      try {
        await api(`/api/v1/dispositivos/${atuador}/comandos`, { metodo: "POST", corpo: { acao } });
        registrar(
          { id: ++sequencia, time: agora(), kind: "atuador", title: `COMANDO · ${acao.toUpperCase()}`, detail: `${atuador} · por ${eu.usuario}` },
          `[ TX ] ${zoneId} → ${acao}`,
        );
      } catch (e) {
        registrar(
          { id: ++sequencia, time: agora(), kind: "sistema", title: "COMANDO recusado", detail: e instanceof Error ? e.message : String(e) },
          `[ ! ] ${zoneId} → comando recusado`,
        );
      }
    },
    [api, eu.usuario, registrar],
  );

  return {
    zones: zonas,
    feed,
    log,
    mqttConnected: conectado,
    dataMode: "api",
    carregando: consulta.isLoading,
    erro: consulta.error ? String(consulta.error) : null,
    comandar,
  };
}

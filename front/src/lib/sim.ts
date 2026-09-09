import { useSyncExternalStore } from "react";

/**
 * Simulação do fluxo IoT:
 * sensor (temp/umidade/fumaça por zona) -> tópico MQTT da zona -> gateway
 * avalia regra (fumaça alta + umidade baixa) -> consulta NASA FIRMS ->
 * publica comando de alerta no tópico do atuador.
 *
 * Quando o backend existir, basta trocar `tick()` por assinaturas MQTT reais
 * (ex.: mqtt.js sobre WebSocket) mantendo os mesmos tipos abaixo.
 */

export type Risk = "baixo" | "medio" | "alto" | "critico" | "offline";

export interface Zone {
  id: string;
  name: string;
  sensorId: string;
  coords: string;
  topic: string;
  temp: number;
  humidity: number;
  smoke: number;
  prevTemp: number;
  prevHumidity: number;
  prevSmoke: number;
  online: boolean;
  risk: Risk;
  firmsConfirmed: boolean;
  firmsConf: number;
  smokeHistory: number[];
  lastPingSec: number;
}

export interface FeedEvent {
  id: number;
  time: string;
  kind: "critico" | "regra" | "satelite" | "telemetria" | "sistema" | "atuador";
  title: string;
  detail: string;
}

export interface SimState {
  zones: Zone[];
  feed: FeedEvent[];
  log: string[];
  clock: string;
  lastReadSec: number;
  uptimeSec: number;
  commandsSent: number;
}

const ZONE_DEFS = [
  { id: "serra-leste", name: "SERRA DO LESTE", sensorId: "ZL-04", coords: "-22.817,-43.291", temp: 40, humidity: 14, smoke: 88 },
  { id: "zona-norte", name: "ZONA NORTE", sensorId: "ZN-01", coords: "-22.790,-43.312", temp: 33, humidity: 19, smoke: 70 },
  { id: "campo-sul", name: "CAMPO SUL", sensorId: "ZS-06", coords: "-22.858,-43.205", temp: 32, humidity: 21, smoke: 65 },
  { id: "varzea-oeste", name: "VÁRZEA OESTE", sensorId: "ZO-02", coords: "-22.801,-43.355", temp: 28, humidity: 35, smoke: 46 },
  { id: "mata-central", name: "MATA CENTRAL", sensorId: "ZC-03", coords: "-22.806,-43.276", temp: 24, humidity: 58, smoke: 9 },
  { id: "riacho-noroeste", name: "RIACHO NOROESTE", sensorId: "RZ-05", coords: "-22.764,-43.340", temp: 26, humidity: 45, smoke: 20 },
];

function evaluateRisk(smoke: number, humidity: number, temp: number): Risk {
  // regra do gateway: fumaça alta + umidade baixa = risco alto
  if (smoke >= 85 && humidity < 15) return "critico";
  if (smoke >= 60 && humidity < 25) return "alto";
  if (smoke >= 40 || humidity < 35 || temp > 33) return "medio";
  return "baixo";
}

function now(): string {
  return new Date().toLocaleTimeString("pt-BR", { hour12: false });
}

let eventId = 0;
function evt(kind: FeedEvent["kind"], title: string, detail: string): FeedEvent {
  return { id: ++eventId, time: now(), kind, title, detail };
}

function walk(v: number, step: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, v + (Math.random() - 0.5) * 2 * step));
}

const startedAt = Date.now();

function initZones(): Zone[] {
  return ZONE_DEFS.map((z, i) => {
    const online = z.id !== "riacho-noroeste";
    const risk = online ? evaluateRisk(z.smoke, z.humidity, z.temp) : "offline";
    return {
      ...z,
      topic: `incendio/${z.id}/telemetria`,
      prevTemp: z.temp,
      prevHumidity: z.humidity,
      prevSmoke: z.smoke,
      online,
      risk,
      firmsConfirmed: risk === "critico" || risk === "alto",
      firmsConf: risk === "critico" ? 0.94 : risk === "alto" ? 0.81 : 0,
      smokeHistory: Array.from({ length: 7 }, (_, k) =>
        Math.max(2, z.smoke - (6 - k) * (3 + i) + Math.random() * 4),
      ),
      lastPingSec: online ? 3 : 42,
    };
  });
}

let state: SimState = {
  zones: initZones(),
  feed: [
    evt("critico", "ALERTA CRÍTICO", "incendio/serra-leste/alerta · risco=critico"),
    evt("regra", "REGRA · risco alto", "incendio/zona-norte/alerta · fumaca=71 umid=18"),
    evt("satelite", "SATÉLITE · foco", "firms/confirm · conf=0.94 dist=1.2km"),
    evt("telemetria", "TELEMETRIA ok", "incendio/mata-central/telemetria"),
    evt("sistema", "GATEWAY · boot", "broker=tcp://192.168.0.10:1883"),
  ],
  log: [
    "[ OK ] serra-leste → risco=critico",
    "[ ! ] zona-norte → risco=alto",
    "[ SAT] firms → 3 focos",
    "[ .. ] riacho-nw → sem resposta",
  ],
  clock: now(),
  lastReadSec: 2,
  uptimeSec: 0,
  commandsSent: 1,
};

const listeners = new Set<() => void>();
function emit() {
  listeners.forEach((l) => l());
}

function tick() {
  const events: FeedEvent[] = [];
  const logLines: string[] = [];

  const zones = state.zones.map((z) => {
    const nz = { ...z };
    if (!nz.online) {
      // sensor às vezes volta
      if (Math.random() < 0.06) {
        nz.online = true;
        nz.lastPingSec = 1;
        events.push(evt("sistema", "SENSOR reconectou", `${nz.topic} · sinal restabelecido`));
        logLines.push(`[ OK ] ${nz.id} → reconectado`);
      } else {
        nz.lastPingSec += 2;
        return nz;
      }
    }

    nz.prevTemp = nz.temp;
    nz.prevHumidity = nz.humidity;
    nz.prevSmoke = nz.smoke;
    nz.temp = Math.round(walk(nz.temp, 0.9, 18, 52) * 10) / 10;
    nz.humidity = Math.round(walk(nz.humidity, 2.2, 5, 85));
    nz.smoke = Math.round(walk(nz.smoke, 4, 1, 99));
    nz.smokeHistory = [...nz.smokeHistory.slice(1), nz.smoke];
    nz.lastPingSec = Math.floor(Math.random() * 6) + 1;

    const prevRisk = nz.risk;
    nz.risk = evaluateRisk(nz.smoke, nz.humidity, nz.temp);

    const rank: Risk[] = ["baixo", "medio", "alto", "critico"];
    if (rank.indexOf(nz.risk) > rank.indexOf(prevRisk)) {
      if (nz.risk === "critico") {
        events.push(evt("critico", "ALERTA CRÍTICO", `incendio/${nz.id}/alerta · risco=critico`));
        logLines.push(`[ OK ] ${nz.id} → risco=critico`);
      } else if (nz.risk === "alto") {
        events.push(
          evt("regra", "REGRA · risco alto", `incendio/${nz.id}/alerta · fumaca=${nz.smoke} umid=${nz.humidity}`),
        );
        logLines.push(`[ ! ] ${nz.id} → risco=alto`);
      } else {
        logLines.push(`[ .. ] ${nz.id} → risco=${nz.risk}`);
      }
    }

    // confirmação NASA FIRMS quando risco >= médio
    if (nz.risk === "alto" || nz.risk === "critico") {
      if (!nz.firmsConfirmed && Math.random() < 0.35) {
        nz.firmsConfirmed = true;
        nz.firmsConf = Math.round((0.75 + Math.random() * 0.24) * 100) / 100;
        events.push(
          evt("satelite", "SATÉLITE · foco", `firms/confirm · conf=${nz.firmsConf} dist=${(0.4 + Math.random() * 2).toFixed(1)}km`),
        );
        logLines.push(`[ SAT] firms → foco ${nz.id}`);
      }
    } else if (nz.firmsConfirmed && Math.random() < 0.2) {
      nz.firmsConfirmed = false;
      nz.firmsConf = 0;
    }

    if (Math.random() < 0.12) {
      events.push(
        evt("telemetria", "TELEMETRIA ok", `incendio/${nz.id}/telemetria {t:${nz.temp},h:${nz.humidity},f:${nz.smoke}}`),
      );
    }
    return nz;
  });

  state = {
    ...state,
    zones,
    feed: [...events.reverse(), ...state.feed].slice(0, 9),
    log: [...logLines, ...state.log].slice(0, 6),
    clock: now(),
    lastReadSec: 2,
    uptimeSec: Math.floor((Date.now() - startedAt) / 1000),
  };
  emit();
}

let timer: ReturnType<typeof setInterval> | null = null;
function ensureTimer() {
  if (timer === null) timer = setInterval(tick, 2000);
}

export function useSim(): SimState {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      ensureTimer();
      return () => {
        listeners.delete(cb);
        if (listeners.size === 0 && timer !== null) {
          clearInterval(timer);
          timer = null;
        }
      };
    },
    () => state,
    () => state,
  );
}

export function sendCommand(zoneId: string, action: string) {
  const payload = `incendio/${zoneId}/alerta {"acao":"${action}"}`;
  state = {
    ...state,
    commandsSent: state.commandsSent + 1,
    feed: [evt("atuador", `COMANDO · ${action.toUpperCase()}`, payload), ...state.feed].slice(0, 9),
    log: [`[ TX ] ${zoneId} → ${action}`, ...state.log].slice(0, 6),
  };
  emit();
}

export const RISK_LABEL: Record<Risk, string> = {
  baixo: "BAIXO",
  medio: "MÉDIO",
  alto: "ALTO",
  critico: "CRÍTICO",
  offline: "OFFLINE",
};

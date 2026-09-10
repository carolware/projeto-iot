/**
 * iot.ts — Camada de dados: MQTT real (WSS) com fallback para simulação.
 *
 * O pacote `mqtt` é carregado via dynamic import SOMENTE no browser,
 * evitando o crash "window is not defined" no SSR (Node.js).
 *
 * DATA_MODE (variável de ambiente VITE_DATA_MODE):
 *   "mqtt" (padrão) → conecta ao broker via WebSocket, recebe dados reais
 *   "sim"           → usa simulação local (sem rede)
 */
import { useSyncExternalStore } from "react";
import type { MqttClient } from "mqtt";   // import de tipo: apagado no build, seguro no SSR
import { sendCommand as sendSimCommand, useSim, type FeedEvent, type Risk, type SimState, type Zone } from "./sim";

export type { FeedEvent, Risk, SimState, Zone } from "./sim";

const DATA_MODE = import.meta.env.VITE_DATA_MODE ?? "mqtt";
const MQTT_URL  = import.meta.env.VITE_MQTT_URL  ?? "wss://broker.hivemq.com:8884/mqtt";
const TOPIC_BASE = import.meta.env.VITE_MQTT_TOPIC_BASE ?? "sentinela-iot-2026-joao-carol/monitoramento-br";

const ZONE_DEFS = [
  { id: "anapolis",       name: "ANÁPOLIS",      sensorId: "GO-AN-01", coords: "-16.3267,-48.9530" },
  { id: "formosa",        name: "FORMOSA",        sensorId: "GO-FO-02", coords: "-15.5372,-47.3372" },
  { id: "pirinopolis",    name: "PIRENÓPOLIS",    sensorId: "GO-PI-03", coords: "-15.8558,-48.9597" },
  { id: "sandolandia",    name: "SANDOLÂNDIA",    sensorId: "TO-SA-04", coords: "-12.5408,-49.9192" },
  { id: "novo-progresso", name: "NOVO PROGRESSO", sensorId: "PA-NP-05", coords: "-7.1261,-55.3853"  },
];

function now() {
  return new Date().toLocaleTimeString("pt-BR", { hour12: false });
}

function evaluateRisk(smoke: number, humidity: number, temp: number): Risk {
  if (smoke >= 85 && humidity < 15) return "critico";
  if (smoke >= 60 && humidity < 25) return "alto";
  if (smoke >= 40 || humidity < 35 || temp > 33) return "medio";
  return "baixo";
}

function initZones(): Zone[] {
  return ZONE_DEFS.map((zone) => ({
    ...zone,
    topic: `${TOPIC_BASE}/${zone.id}/sensor/+`,
    temp: 0,
    humidity: 0,
    smoke: 0,
    prevTemp: 0,
    prevHumidity: 0,
    prevSmoke: 0,
    online: false,
    risk: "offline" as Risk,
    firmsConfirmed: false,
    firmsConf: 0,
    smokeHistory: Array(7).fill(0),
    lastPingSec: 0,
  }));
}

const startedAt = Date.now();
let eventId = 0;
// `mqtt` é carregado dinamicamente — nunca nulo no servidor, null até import()
let mqttClient: MqttClient | null = null;
let initialized = false;
const lastSeen = new Map<string, number>();
const listeners = new Set<() => void>();

let mqttState: SimState = {
  zones: initZones(),
  feed: [],
  log: ["[ .. ] aguardando conexão MQTT"],
  // clock começa vazio para que o SSR e a hidratação do cliente
  // produzam o mesmo HTML — o setInterval atualiza após a montagem
  clock: "",
  lastReadSec: 0,
  uptimeSec: 0,
  commandsSent: 0,
  mqttConnected: false,
  dataMode: "mqtt",
};

function evt(kind: FeedEvent["kind"], title: string, detail: string): FeedEvent {
  return { id: ++eventId, time: now(), kind, title, detail };
}

function emit() {
  listeners.forEach((l) => l());
}

function addEvent(event: FeedEvent, logLine: string) {
  mqttState = {
    ...mqttState,
    feed: [event, ...mqttState.feed].slice(0, 9),
    log: [logLine, ...mqttState.log].slice(0, 6),
  };
}

function updateTelemetry(zoneId: string, metric: string, value: number) {
  lastSeen.set(zoneId, Date.now());
  let changedZone: Zone | undefined;
  const zones = mqttState.zones.map((zone) => {
    if (zone.id !== zoneId) return zone;
    const next = { ...zone, online: true, lastPingSec: 0 };
    if (metric === "temperatura") {
      next.prevTemp = next.temp;
      next.temp = value;
    } else if (metric === "umidade") {
      next.prevHumidity = next.humidity;
      next.humidity = value;
    } else if (metric === "fumaca") {
      next.prevSmoke = next.smoke;
      next.smoke = value;
      next.smokeHistory = [...next.smokeHistory.slice(1), value];
    }
    next.risk = evaluateRisk(next.smoke, next.humidity, next.temp);
    changedZone = next;
    return next;
  });
  mqttState = { ...mqttState, zones, lastReadSec: 0 };
  if (metric === "fumaca" && changedZone) {
    addEvent(
      evt("telemetria", "TELEMETRIA recebida",
          `${TOPIC_BASE}/${zoneId}/sensor/+ {t:${changedZone.temp},h:${changedZone.humidity},f:${changedZone.smoke}}`),
      `[ OK ] ${zoneId} → risco=${changedZone.risk}`,
    );
  }
  emit();
}

function updateAlert(zoneId: string, payload: Record<string, unknown>) {
  const risk = String(payload.risco ?? "alto") as Risk;
  const confirmed = payload.firms_confirmado === true;
  const zones = mqttState.zones.map((zone) =>
    zone.id === zoneId ? { ...zone, risk, firmsConfirmed: confirmed, firmsConf: confirmed ? 1 : 0 } : zone,
  );
  const kind: FeedEvent["kind"] = risk === "critico" ? "critico" : "regra";
  mqttState = { ...mqttState, zones };
  addEvent(
    evt(kind, risk === "critico" ? "ALERTA CRÍTICO" : "REGRA · risco alto",
        `${TOPIC_BASE}/${zoneId}/atuador/alerta · FIRMS=${confirmed ? "confirmado" : "não confirmado"}`),
    confirmed ? `[ SAT] firms → foco ${zoneId}` : `[ ! ] ${zoneId} → risco=${risk}`,
  );
  emit();
}

function handleMessage(topic: string, raw: Uint8Array) {
  const parts = topic.split("/");
  if (parts.length < 5 || !topic.startsWith(`${TOPIC_BASE}/`)) return;
  const zoneId  = parts.at(-3)!;
  try {
    const payload = JSON.parse(new TextDecoder().decode(raw)) as Record<string, unknown>;
    if (parts.at(-2) === "sensor") {
      const value = Number(payload.valor);
      if (Number.isFinite(value)) updateTelemetry(zoneId, parts.at(-1)!, value);
    } else if (parts.at(-2) === "atuador" && parts.at(-1) === "alerta") {
      updateAlert(zoneId, payload);
    }
  } catch {
    addEvent(evt("sistema", "MQTT · payload inválido", topic), `[ ! ] payload inválido → ${topic}`);
    emit();
  }
}

/**
 * Carrega o pacote mqtt dinamicamente (apenas no browser, nunca no servidor).
 * Usa import() em vez de import estático para evitar avaliação no SSR.
 */
function initializeMqtt() {
  if (initialized || typeof window === "undefined" || DATA_MODE !== "mqtt") return;
  initialized = true;

  import("mqtt")
    .then(({ default: mqttLib }) => {
      mqttClient = mqttLib.connect(MQTT_URL, {
        clientId:       `sentinela-front-${crypto.randomUUID()}`,
        clean:          true,
        reconnectPeriod: 3000,
        connectTimeout:  10000,
      });

      mqttClient.on("connect", () => {
        mqttClient!.subscribe(
          [`${TOPIC_BASE}/+/sensor/+`, `${TOPIC_BASE}/+/atuador/alerta`],
          { qos: 0 },
        );
        mqttState = { ...mqttState, mqttConnected: true };
        addEvent(evt("sistema", "MQTT · conectado", MQTT_URL), "[ OK ] dashboard → broker conectado");
        emit();
      });

      mqttClient.on("message", handleMessage);

      mqttClient.on("reconnect", () => {
        mqttState = { ...mqttState, mqttConnected: false };
        emit();
      });

      mqttClient.on("close", () => {
        mqttState = { ...mqttState, mqttConnected: false };
        emit();
      });

      mqttClient.on("error", (error: Error) => {
        addEvent(evt("sistema", "MQTT · erro", error.message), `[ ! ] mqtt → ${error.message}`);
        emit();
      });
    })
    .catch((err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      addEvent(evt("sistema", "MQTT · falha ao carregar", msg), `[ ! ] mqtt → import failed`);
      emit();
    });

  // Ticker: uptime + detecção de zonas offline (roda independente do MQTT)
  window.setInterval(() => {
    const current = Date.now();
    const zones = mqttState.zones.map((zone) => {
      const seen = lastSeen.get(zone.id);
      if (!seen) return zone;
      const lastPingSec = Math.floor((current - seen) / 1000);
      return {
        ...zone,
        lastPingSec,
        online: lastPingSec < 15,
        risk: lastPingSec < 15 ? zone.risk : ("offline" as Risk),
      };
    });
    mqttState = {
      ...mqttState,
      zones,
      clock:       now(),
      lastReadSec: Math.min(999, mqttState.lastReadSec + 1),
      uptimeSec:   Math.floor((current - startedAt) / 1000),
    };
    emit();
  }, 1000);
}

function useMqtt(): SimState {
  return useSyncExternalStore(
    (callback) => {
      listeners.add(callback);
      initializeMqtt();
      return () => listeners.delete(callback);
    },
    () => mqttState,
    () => mqttState,
  );
}

export function useIot(): SimState {
  // Em modo sim, usa apenas a simulação local
  if (DATA_MODE === "sim") {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const simulated = useSim();
    return { ...simulated, mqttConnected: true, dataMode: "sim" };
  }
  // Em modo mqtt, conecta ao broker real
  // eslint-disable-next-line react-hooks/rules-of-hooks
  return useMqtt();
}

export function sendIotCommand(zoneId: string, action: string) {
  if (DATA_MODE === "sim") {
    sendSimCommand(zoneId, action);
    return;
  }
  const topic   = `${TOPIC_BASE}/${zoneId}/atuador/alerta`;
  const payload = JSON.stringify({
    zona:      zoneId,
    risco:     "alto",
    acao:      action,
    origem:    "dashboard",
    timestamp: new Date().toISOString(),
  });
  mqttClient?.publish(topic, payload, { qos: 1 });
  mqttState = { ...mqttState, commandsSent: mqttState.commandsSent + 1 };
  addEvent(
    evt("atuador", `COMANDO · ${action.toUpperCase()}`, `${topic} ${payload}`),
    `[ TX ] ${zoneId} → ${action}`,
  );
  emit();
}

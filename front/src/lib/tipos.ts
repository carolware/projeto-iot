/**
 * tipos.ts — modelo de dados do painel. Os componentes do dashboard trabalham
 * com `Zone` (um local monitorado); quem monta as zonas a partir da API da
 * central é `iot.ts`.
 */

export type Risk = "baixo" | "medio" | "alto" | "critico" | "offline";
export type Severidade = "medio" | "alto" | "critico";

export const RISK_LABEL: Record<Risk, string> = {
  baixo: "BAIXO",
  medio: "MÉDIO",
  alto: "ALTO",
  critico: "CRÍTICO",
  offline: "OFFLINE",
};

export interface FirmsHotspot {
  id: string;
  latitude: number;
  longitude: number;
  frp: number;
  confidence: string;
  satellite: string;
  instrument: string;
  acquiredAt: string | null;
  daynight: string;
  distanceKm: number;
}

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
  alarmesAbertos: number;
  firmsConfirmed: boolean;
  firmsConf: number;
  firmsHotspots: FirmsHotspot[];
  firmsUpdatedAt: string | null;
  firmsRadiusKm: number;
  firmsWindowHours: number;
  smokeHistory: number[];
  lastPingSec: number;
  /** epoch ms da última leitura — o card calcula "há Xs" localmente */
  lastSeenAt?: number | undefined;
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
  /** WebSocket de tempo real da central conectado */
  mqttConnected: boolean;
  dataMode: "api";
  carregando: boolean;
  erro: string | null;
  comandar: (zoneId: string, acao: string) => Promise<void>;
}

// ── Formatos da API da central ────────────────────────────────────────────────

export interface Leitura {
  valor: number;
  unidade: string;
  suspeita: boolean;
  tempo: string;
}

export interface DispositivoPainel {
  slug: string;
  nome: string;
  tipo: "estacao" | "terminal" | "atuador" | "conector";
  dominio: string;
  online: boolean;
  revogado: boolean;
  ultimo_contato_em: string | null;
  leituras: Record<string, Leitura>;
  risco: Severidade | null;
  historico_fumaca: number[];
  satelite: {
    focos: number;
    confirmado: boolean;
    frp_max: number;
    raio_km: number;
    janela_horas: number;
    atualizado_em: string;
    hotspots: Json[];
  } | null;
}

export interface SitePainel {
  slug: string;
  nome: string;
  lat: number;
  lon: number;
  raio_km: number | null;
  risco: Risk;
  alarmes_abertos: number;
  dispositivos: DispositivoPainel[];
}

export interface Painel {
  inquilino: string;
  cenario: "rural" | "urbano";
  gerado_em: string;
  sites: SitePainel[];
}

/** JSON livre vindo da API/tempo real (formato definido pelo contrato da central). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;

export interface DadosAlarme {
  resumo?: string;
  regra_nome?: string;
  origem?: Json;
  decidido_na_borda?: boolean;
  via?: string | null;
  satelite?: { focos: number; frp_max: number; confirmado: boolean } | null;
  leituras?: Record<string, number>;
  distancia_km?: number;
}

export interface Alarme {
  id: string;
  inquilino: string;
  severidade: Severidade;
  estado: "ativo" | "reconhecido" | "resolvido";
  regra: string | null;
  dispositivo: string | null;
  site: string | null;
  site_nome?: string | null;
  dominio: string | null;
  ocorrido_em: string;
  reconhecido_em: string | null;
  reconhecido_por: string | null;
  resolvido_em: string | null;
  resolvido_por: string | null;
  ocorrencias: number;
  origem_alarme_id: string | null;
  dados: DadosAlarme;
}

export interface Pagina<T> {
  itens: T[];
  pagina: number;
  tamanho: number;
  total: number;
}

export interface Eu {
  usuario: string;
  papeis: string[];
  inquilino: string | null;
  cenario: "rural" | "urbano" | null;
}

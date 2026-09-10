/**
 * LeafletMapCore — importado dinamicamente por FireMap (React.lazy).
 * Só é carregado no browser; nunca é avaliado durante SSR, evitando
 * o erro "window is not defined" que o Leaflet lança no Node.js.
 */
import { Fragment } from "react";
import { Circle, CircleMarker, MapContainer, Popup, TileLayer, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import type { Zone } from "@/lib/iot";

const TILE_URL =
  "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com">CARTO</a>';

const RISK_COLOR: Record<string, string> = {
  baixo:   "#22c55e",
  medio:   "#f59e0b",
  alto:    "#f97316",
  critico: "#ef4444",
  offline: "#6b7280",
};

const RISK_LABEL: Record<string, string> = {
  baixo: "BAIXO", medio: "MÉDIO", alto: "ALTO", critico: "CRÍTICO", offline: "OFFLINE",
};

// Ponto central: Brasil Central ↔ Amazônia
const MAP_CENTER: [number, number] = [-11.3, -49.5];

function ZoneMarkers({ zones, windowHours }: { zones: Zone[]; windowHours: number }) {
  return (
    <>
      {zones.map((zone) => {
        if (!zone.coords) return null;
        const parts = zone.coords.split(",").map(Number);
        if (parts.length < 2 || !Number.isFinite(parts[0]) || !Number.isFinite(parts[1])) return null;
        const [lat, lon] = parts as [number, number];
        const color   = RISK_COLOR[zone.risk] ?? RISK_COLOR.offline;
        const radius  = zone.risk === "critico" ? 14 : zone.risk === "alto" ? 11 : 8;
        const fillOp  = zone.online ? (zone.risk === "critico" ? 0.88 : 0.68) : 0.35;
        const cutoff = Date.now() - windowHours * 60 * 60 * 1000;
        const visibleHotspots = zone.firmsHotspots.filter((hotspot) => {
          const acquiredMs = hotspot.acquiredAt ? Date.parse(hotspot.acquiredAt) : Number.NaN;
          return !Number.isFinite(acquiredMs) || acquiredMs >= cutoff;
        });

        return (
          <Fragment key={zone.id}>
            <Circle
              center={[lat, lon]}
              radius={zone.firmsRadiusKm * 1000}
              pathOptions={{
                color,
                fillColor: color,
                weight: 1,
                opacity: 0.45,
                fillOpacity: 0.06,
                dashArray: "5 5",
              }}
            />
            <CircleMarker
              center={[lat, lon]}
            radius={radius}
            pathOptions={{
              fillColor: color,
              color: zone.risk === "critico" ? "#ff6666" : color,
              weight: zone.risk === "critico" ? 3 : 2,
              fillOpacity: fillOp,
              opacity: 0.95,
            }}
          >
            <Popup>
              <div style={{ fontFamily: "monospace", fontSize: 12, lineHeight: 1.6, minWidth: 160 }}>
                <div style={{ fontWeight: "bold", marginBottom: 4 }}>{zone.name}</div>
                <div>
                  Risco:{" "}
                  <b style={{ color }}>{RISK_LABEL[zone.risk] ?? zone.risk.toUpperCase()}</b>
                </div>
                <div>Temperatura: {zone.temp} °C</div>
                <div>Umidade: {zone.humidity} %</div>
                <div>Índice de fogo: {zone.smoke} pontos</div>
                <div style={{ marginTop: 4, fontSize: 11 }}>Sensor: {zone.sensorId}</div>
                <div style={{ marginTop: 4 }}>Área FIRMS: raio de {zone.firmsRadiusKm} km</div>
                <div style={{ color: visibleHotspots.length ? "#ef4444" : "#16a34a", fontWeight: "bold" }}>
                  {visibleHotspots.length
                    ? `${visibleHotspots.length} foco(s) na janela selecionada`
                    : "Nenhum foco FIRMS na área"}
                </div>
              </div>
            </Popup>
          </CircleMarker>
          </Fragment>
        );
      })}
    </>
  );
}

function FirmsMarkers({ zones, windowHours }: { zones: Zone[]; windowHours: number }) {
  const cutoff = Date.now() - windowHours * 60 * 60 * 1000;
  const unique = new Map<string, { hotspot: Zone["firmsHotspots"][number]; zoneName: string }>();

  zones.forEach((zone) => {
    zone.firmsHotspots.forEach((hotspot) => {
      const acquiredMs = hotspot.acquiredAt ? Date.parse(hotspot.acquiredAt) : Number.NaN;
      if (Number.isFinite(acquiredMs) && acquiredMs < cutoff) return;
      const key = `${hotspot.latitude.toFixed(4)}:${hotspot.longitude.toFixed(4)}:${hotspot.acquiredAt ?? hotspot.id}`;
      const previous = unique.get(key);
      if (!previous || hotspot.distanceKm < previous.hotspot.distanceKm) {
        unique.set(key, { hotspot, zoneName: zone.name });
      }
    });
  });

  return (
    <>
      {[...unique.values()].map(({ hotspot, zoneName }) => {
        const color = hotspot.frp >= 100 ? "#ef4444" : hotspot.frp >= 30 ? "#f97316" : "#facc15";
        const radius = Math.min(14, 5 + Math.sqrt(Math.max(0, hotspot.frp)) * 0.65);
        const acquired = hotspot.acquiredAt
          ? new Date(hotspot.acquiredAt).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" })
          : "não informado";
        return (
          <CircleMarker
            key={`${hotspot.latitude}:${hotspot.longitude}:${hotspot.acquiredAt ?? hotspot.id}`}
            center={[hotspot.latitude, hotspot.longitude]}
            radius={radius}
            pathOptions={{ color: "#fff", weight: 1, fillColor: color, fillOpacity: 0.92 }}
          >
            <Tooltip direction="top" offset={[0, -4]} opacity={0.95}>
              Foco FIRMS · {hotspot.frp} MW
            </Tooltip>
            <Popup>
              <div style={{ fontFamily: "monospace", fontSize: 12, lineHeight: 1.6, minWidth: 210 }}>
                <div style={{ color, fontWeight: "bold", marginBottom: 5 }}>FOCO DE CALOR NASA FIRMS</div>
                <div>Zona mais próxima: {zoneName}</div>
                <div>Coordenadas: {hotspot.latitude}, {hotspot.longitude}</div>
                <div>Detectado em: {acquired}</div>
                <div>FRP: <b>{hotspot.frp} MW</b></div>
                <div>Confiança: {hotspot.confidence}</div>
                <div>Satélite: {hotspot.satellite}</div>
                <div>Instrumento: {hotspot.instrument}</div>
                <div>Período: {hotspot.daynight === "D" ? "diurno" : hotspot.daynight === "N" ? "noturno" : hotspot.daynight}</div>
                <div>Distância do centro: {hotspot.distanceKm} km</div>
              </div>
            </Popup>
          </CircleMarker>
        );
      })}
    </>
  );
}

export interface LeafletMapProps {
  zones: Zone[];
  zoom?: number;
  interactive?: boolean;
  windowHours?: number;
}

export default function LeafletMapCore({ zones, zoom = 5, interactive = false, windowHours = 24 }: LeafletMapProps) {
  return (
    <MapContainer
      center={MAP_CENTER}
      zoom={zoom}
      className="w-full h-full"
      zoomControl={interactive}
      attributionControl={interactive}
      dragging={interactive}
      scrollWheelZoom={interactive}
      doubleClickZoom={interactive}
      touchZoom={interactive}
      style={{ background: "#0d1117" }}
    >
      <TileLayer url={TILE_URL} attribution={TILE_ATTR} />
      <ZoneMarkers zones={zones} windowHours={windowHours} />
      <FirmsMarkers zones={zones} windowHours={windowHours} />
    </MapContainer>
  );
}

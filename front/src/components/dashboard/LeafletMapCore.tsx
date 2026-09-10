/**
 * LeafletMapCore — importado dinamicamente por FireMap (React.lazy).
 * Só é carregado no browser; nunca é avaliado durante SSR, evitando
 * o erro "window is not defined" que o Leaflet lança no Node.js.
 */
import { MapContainer, TileLayer, CircleMarker, Popup } from "react-leaflet";
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
export const MAP_CENTER: [number, number] = [-12.8, -51.5];
export const FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@-12,-51,6z";

function ZoneMarkers({ zones }: { zones: Zone[] }) {
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

        return (
          <CircleMarker
            key={zone.id}
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
                <div>Fumaça: {zone.smoke} %</div>
                <div style={{ marginTop: 4, fontSize: 11 }}>Sensor: {zone.sensorId}</div>
                {zone.firmsConfirmed && (
                  <div style={{ color: "#ef4444", marginTop: 4, fontWeight: "bold" }}>
                    ⚠ FIRMS: foco confirmado por satélite
                  </div>
                )}
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
}

export default function LeafletMapCore({ zones, zoom = 5, interactive = false }: LeafletMapProps) {
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
      <ZoneMarkers zones={zones} />
    </MapContainer>
  );
}

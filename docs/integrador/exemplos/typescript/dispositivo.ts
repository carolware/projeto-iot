// Dispositivo de teste em TypeScript (Node 22.18+ roda .ts direto):
// só a biblioteca genérica `mqtt` e o fetch nativo.
//
//   npm install
//   SLUG=sensor-teste-01 CREDENCIAL=... node dispositivo.ts
import { readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import mqtt from "mqtt";

const env = (nome: string, padrao?: string): string => {
  const valor = process.env[nome] ?? padrao;
  if (valor === undefined) throw new Error(`defina ${nome}`);
  return valor;
};

const SLUG = env("SLUG");
const CREDENCIAL = env("CREDENCIAL");
const BASE = `${env("INQUILINO", "brigada-cerrado")}/${env("SITE", "mirador")}/${env("DOMINIO", "incendio")}/${SLUG}`;
const API = env("API", "http://localhost:8000");

type Medida = { grandeza: string; valor: number; unidade: string };

// Mensagem canônica v1.0 (contratos/mensagem-canonica.v1.schema.json)
function mensagem(tipo: string, extra: { medidas?: Medida[]; dados?: object }) {
  return {
    versao: "1.0",
    id_mensagem: randomUUID(), // gerado na origem: reenviar o mesmo id não duplica
    dispositivo: SLUG,
    tipo,
    timestamp: new Date().toISOString(),
    ...extra,
  };
}

const leitura = (temperatura: number) =>
  mensagem("telemetria", {
    medidas: [
      { grandeza: "temperatura", valor: temperatura, unidade: "Cel" },
      { grandeza: "umidade", valor: 40, unidade: "%" },
      { grandeza: "fumaca", valor: 5, unidade: "1" },
    ],
  });

const cliente = await mqtt.connectAsync({
  host: env("MQTT_HOST", "localhost"),
  port: Number(env("MQTT_PORT", "8883")),
  protocol: "mqtts",
  ca: readFileSync(env("MQTT_CA", "plataforma/infra/pki/gerado/ca.crt")),
  clientId: SLUG,
  username: SLUG,
  password: CREDENCIAL,
});
console.log("MQTT conectado");

await cliente.subscribeAsync(`${BASE}/comando`, { qos: 1 });
cliente.on("message", (_topico, carga) => {
  const comando = JSON.parse(carga.toString());
  console.log("comando recebido:", comando.dados);
  const resposta = mensagem("evento", { dados: { evento: "executado", comando_id: comando.id_mensagem } });
  cliente.publish(`${BASE}/evento`, JSON.stringify(resposta), { qos: 1 });
});

for (const temperatura of [24, 25.5, 26.1]) {
  await cliente.publishAsync(`${BASE}/telemetria`, JSON.stringify(leitura(temperatura)), { qos: 1 });
  console.log(`MQTT: telemetria publicada (${temperatura} °C)`);
}

const resposta = await fetch(`${API}/api/v1/ingestao`, {
  method: "POST",
  headers: {
    "content-type": "application/json",
    authorization: "Basic " + Buffer.from(`${SLUG}:${CREDENCIAL}`).toString("base64"),
  },
  body: JSON.stringify(leitura(27)),
});
console.log(`HTTP: ${resposta.status}`, await resposta.json());

console.log("aguardando comandos por 15 s…");
await new Promise((fim) => setTimeout(fim, 15_000));
await cliente.endAsync();

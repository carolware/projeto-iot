import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useTempoReal } from "@/lib/iot";
import { ListaAlarmes } from "@/components/alarmes/ListaAlarmes";
import { Casco, Secao } from "@/components/layout/Casco";

export const Route = createFileRoute("/alarmes")({
  head: () => ({ meta: [{ title: "Alarmes — SENTINELA" }] }),
  component: Alarmes,
});

function Alarmes() {
  const conectado = useTempoReal();
  const [abertos, setAbertos] = useState<boolean | undefined>(true);
  const filtro = (rotulo: string, valor: boolean | undefined) => (
    <button
      onClick={() => setAbertos(valor)}
      aria-pressed={abertos === valor}
      className={`rounded border px-2 py-1 font-mono text-[10px] cursor-pointer ${abertos === valor ? "border-fg text-fg" : "border-hair text-faint hover:text-fg"}`}
    >
      {rotulo}
    </button>
  );
  return (
    <Casco titulo="Alarmes" conectado={conectado}>
      <main className="flex-1 px-5 py-4 max-w-5xl w-full mx-auto">
        <Secao
          titulo="ALARMES · ATIVO → RECONHECIDO → RESOLVIDO"
          acoes={
            <div className="flex gap-2">
              {filtro("ABERTOS", true)}
              {filtro("RESOLVIDOS", false)}
              {filtro("TODOS", undefined)}
            </div>
          }
        >
          <ListaAlarmes key={String(abertos)} abertos={abertos} />
        </Secao>
      </main>
    </Casco>
  );
}

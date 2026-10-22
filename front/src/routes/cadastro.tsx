import { useState, type FormEvent, type ReactNode } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSessao } from "@/lib/auth";
import type { Pagina } from "@/lib/tipos";
import { Casco, Secao } from "@/components/layout/Casco";

export const Route = createFileRoute("/cadastro")({
  head: () => ({ meta: [{ title: "Cadastro — SENTINELA" }] }),
  component: Cadastro,
});

const entrada = "mt-1 w-full rounded border border-hair bg-ink px-2 py-1.5 font-mono text-[11px] text-fg";

function Campo({ rotulo, children }: { rotulo: string; children: ReactNode }) {
  return (
    <label className="block font-mono text-[10px] text-faint">
      {rotulo}
      {children}
    </label>
  );
}

/** Formulário genérico: envia, mostra erro da API ou o resultado. */
function Formulario({
  titulo,
  enviar,
  sucesso,
  permitido,
  children,
}: {
  titulo: string;
  enviar: () => Promise<unknown>;
  sucesso: (r: any) => ReactNode;
  permitido: boolean;
  children: ReactNode;
}) {
  const [resultado, setResultado] = useState<unknown>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const aoEnviar = async (e: FormEvent) => {
    e.preventDefault();
    setErro(null);
    setResultado(null);
    setEnviando(true);
    try {
      setResultado(await enviar());
    } catch (x) {
      setErro(x instanceof Error ? x.message : String(x));
    } finally {
      setEnviando(false);
    }
  };
  return (
    <Secao titulo={titulo}>
      <form onSubmit={aoEnviar} className="space-y-2">
        {children}
        <button
          type="submit"
          disabled={!permitido || enviando}
          className="rounded border border-ember/50 bg-ember/10 px-3 py-1.5 font-mono text-[10px] text-ember hover:bg-ember/20 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {enviando ? "ENVIANDO…" : "CADASTRAR"}
        </button>
        {!permitido && <div className="font-mono text-[10px] text-faint">Requer papel gestor.</div>}
        {erro && <div role="alert" className="font-mono text-[10px] text-danger break-all">{erro}</div>}
        {resultado !== null && <div role="status" className="font-mono text-[10px] text-ok break-all">{sucesso(resultado)}</div>}
      </form>
    </Secao>
  );
}

function Cadastro() {
  const { api, pode } = useSessao();
  const queryClient = useQueryClient();
  const dominios = useQuery({ queryKey: ["dominios"], queryFn: () => api<Pagina<{ slug: string; nome: string }>>("/api/v1/catalogo/dominios?tamanho=200") });
  const perfis = useQuery({ queryKey: ["perfis"], queryFn: () => api<Pagina<{ dominio: string; slug: string; nome: string }>>("/api/v1/catalogo/perfis?tamanho=200") });
  const sites = useQuery({ queryKey: ["sites"], queryFn: () => api<Pagina<{ slug: string; nome: string; lat: number; lon: number }>>("/api/v1/sites?tamanho=200") });
  const atualizar = () => queryClient.invalidateQueries();

  const [dominio, setDominio] = useState({ slug: "", nome: "", descricao: "" });
  const [disp, setDisp] = useState({ slug: "", nome: "", tipo: "estacao", perfil: "", site: "" });
  const [regra, setRegra] = useState({
    dominio: "",
    nome: "",
    severidade: "alto",
    escopo: "central",
    janela_s: "0",
    condicao: '{"todas": [{"grandeza": "temperatura", "op": ">", "valor": 30}]}',
    acoes: '[{"tipo": "alarme"}]',
  });
  const [assinatura, setAssinatura] = useState({ nome: "", dominio: "incendio", lat: "", lon: "", raio_km: "80", severidade_min: "alto" });

  const gestor = pode("gestor");
  const [perfilDominio, perfilSlug] = disp.perfil.split("/");
  const json = (texto: string, campo: string) => {
    try {
      return JSON.parse(texto);
    } catch {
      throw new Error(`${campo}: JSON inválido`);
    }
  };

  return (
    <Casco titulo="Cadastro">
      <main className="flex-1 grid grid-cols-1 lg:grid-cols-2 gap-4 px-5 py-4 max-w-6xl w-full mx-auto">
        <Formulario
          titulo="DOMÍNIO NOVO (SÓ CONFIGURAÇÃO, SEM CÓDIGO)"
          permitido={gestor}
          enviar={async () => (await api("/api/v1/catalogo/dominios", { metodo: "POST", corpo: dominio }), atualizar())}
          sucesso={() => `domínio ${dominio.slug} criado`}
        >
          <Campo rotulo="SLUG"><input required className={entrada} value={dominio.slug} onChange={(e) => setDominio({ ...dominio, slug: e.target.value })} placeholder="recursos_hidricos" /></Campo>
          <Campo rotulo="NOME"><input required className={entrada} value={dominio.nome} onChange={(e) => setDominio({ ...dominio, nome: e.target.value })} /></Campo>
          <Campo rotulo="DESCRIÇÃO"><input className={entrada} value={dominio.descricao} onChange={(e) => setDominio({ ...dominio, descricao: e.target.value })} /></Campo>
        </Formulario>

        <Formulario
          titulo="DISPOSITIVO"
          permitido={pode("gestor", "integrador")}
          enviar={() =>
            api("/api/v1/dispositivos", {
              metodo: "POST",
              corpo: { slug: disp.slug, nome: disp.nome, tipo: disp.tipo, dominio: perfilDominio, perfil: perfilSlug, site: disp.site },
            })
          }
          sucesso={(r) => (
            <>
              dispositivo {r.slug} criado. <strong>Credencial (mostrada só agora):</strong>{" "}
              <code className="text-fg select-all">{r.credencial}</code>
            </>
          )}
        >
          <Campo rotulo="SLUG"><input required className={entrada} value={disp.slug} onChange={(e) => setDisp({ ...disp, slug: e.target.value })} placeholder="est-nova-01" /></Campo>
          <Campo rotulo="NOME"><input required className={entrada} value={disp.nome} onChange={(e) => setDisp({ ...disp, nome: e.target.value })} /></Campo>
          <Campo rotulo="TIPO">
            <select className={entrada} value={disp.tipo} onChange={(e) => setDisp({ ...disp, tipo: e.target.value })}>
              {["estacao", "atuador", "terminal", "conector"].map((t) => <option key={t}>{t}</option>)}
            </select>
          </Campo>
          <Campo rotulo="PERFIL">
            <select required className={entrada} value={disp.perfil} onChange={(e) => setDisp({ ...disp, perfil: e.target.value })}>
              <option value="">—</option>
              {perfis.data?.itens.map((p) => <option key={`${p.dominio}/${p.slug}`} value={`${p.dominio}/${p.slug}`}>{p.dominio} / {p.nome}</option>)}
            </select>
          </Campo>
          <Campo rotulo="LOCAL">
            <select required className={entrada} value={disp.site} onChange={(e) => setDisp({ ...disp, site: e.target.value })}>
              <option value="">—</option>
              {sites.data?.itens.map((s) => <option key={s.slug} value={s.slug}>{s.nome}</option>)}
            </select>
          </Campo>
        </Formulario>

        <Formulario
          titulo="REGRA (CONDIÇÃO → AÇÃO)"
          permitido={gestor}
          enviar={() =>
            api("/api/v1/regras", {
              metodo: "POST",
              corpo: {
                ...regra,
                janela_s: Number(regra.janela_s),
                condicao: json(regra.condicao, "condição"),
                acoes: json(regra.acoes, "ações"),
              },
            })
          }
          sucesso={(r) => `regra ${r.nome} criada (escopo ${r.escopo})`}
        >
          <div className="grid grid-cols-2 gap-2">
            <Campo rotulo="DOMÍNIO">
              <select required className={entrada} value={regra.dominio} onChange={(e) => setRegra({ ...regra, dominio: e.target.value })}>
                <option value="">—</option>
                {dominios.data?.itens.map((d) => <option key={d.slug} value={d.slug}>{d.nome}</option>)}
              </select>
            </Campo>
            <Campo rotulo="NOME"><input required className={entrada} value={regra.nome} onChange={(e) => setRegra({ ...regra, nome: e.target.value })} placeholder="silo-quente" /></Campo>
            <Campo rotulo="SEVERIDADE">
              <select className={entrada} value={regra.severidade} onChange={(e) => setRegra({ ...regra, severidade: e.target.value })}>
                {["medio", "alto", "critico"].map((s) => <option key={s}>{s}</option>)}
              </select>
            </Campo>
            <Campo rotulo="ESCOPO">
              <select className={entrada} value={regra.escopo} onChange={(e) => setRegra({ ...regra, escopo: e.target.value })}>
                <option value="central">central</option>
                <option value="borda">borda (perto do dispositivo)</option>
              </select>
            </Campo>
          </div>
          <Campo rotulo="JANELA (s) — a condição precisa valer durante toda a janela">
            <input type="number" min={0} className={entrada} value={regra.janela_s} onChange={(e) => setRegra({ ...regra, janela_s: e.target.value })} />
          </Campo>
          <Campo rotulo="CONDIÇÃO (JSON: todas / alguma / ausencia)">
            <textarea rows={3} className={entrada} value={regra.condicao} onChange={(e) => setRegra({ ...regra, condicao: e.target.value })} />
          </Campo>
          <Campo rotulo="AÇÕES (JSON: alarme, comando, webhook, publicar, notificar)">
            <textarea rows={3} className={entrada} value={regra.acoes} onChange={(e) => setRegra({ ...regra, acoes: e.target.value })} />
          </Campo>
        </Formulario>

        <Formulario
          titulo="ASSINATURA DE ALERTA (RECEBER ALARMES DE OUTROS INQUILINOS)"
          permitido={gestor}
          enviar={() =>
            api("/api/v1/assinaturas", {
              metodo: "POST",
              corpo: { ...assinatura, lat: Number(assinatura.lat), lon: Number(assinatura.lon), raio_km: Number(assinatura.raio_km) },
            })
          }
          sucesso={(r) => `assinatura "${r.nome}" ativa: ${r.dominio} num raio de ${r.raio_km} km`}
        >
          <Campo rotulo="NOME"><input required className={entrada} value={assinatura.nome} onChange={(e) => setAssinatura({ ...assinatura, nome: e.target.value })} /></Campo>
          <div className="grid grid-cols-2 gap-2">
            <Campo rotulo="DOMÍNIO ACOMPANHADO"><input required className={entrada} value={assinatura.dominio} onChange={(e) => setAssinatura({ ...assinatura, dominio: e.target.value })} /></Campo>
            <Campo rotulo="SEVERIDADE MÍNIMA">
              <select className={entrada} value={assinatura.severidade_min} onChange={(e) => setAssinatura({ ...assinatura, severidade_min: e.target.value })}>
                {["medio", "alto", "critico"].map((s) => <option key={s}>{s}</option>)}
              </select>
            </Campo>
            <Campo rotulo="LATITUDE"><input required inputMode="decimal" className={entrada} value={assinatura.lat} onChange={(e) => setAssinatura({ ...assinatura, lat: e.target.value })} /></Campo>
            <Campo rotulo="LONGITUDE"><input required inputMode="decimal" className={entrada} value={assinatura.lon} onChange={(e) => setAssinatura({ ...assinatura, lon: e.target.value })} /></Campo>
          </div>
          <Campo rotulo="RAIO (km)"><input required type="number" min={1} className={entrada} value={assinatura.raio_km} onChange={(e) => setAssinatura({ ...assinatura, raio_km: e.target.value })} /></Campo>
        </Formulario>
      </main>
    </Casco>
  );
}

import ScrollReveal from "./ScrollReveal";
import { Truck, Shield, Gem, Users } from "lucide-react";

const items = [
  { icon: Gem, title: "Pedras Selecionadas", desc: "Material de primeira qualidade, extraído diretamente da nossa pedreira em Pirenópolis." },
  { icon: Truck, title: "Entrega em Todo Brasil", desc: "Logística eficiente para atender obras em qualquer região do país." },
  { icon: Shield, title: "Garantia de Qualidade", desc: "Controle rigoroso desde a extração até a entrega final do produto." },
  { icon: Users, title: "Atendimento Especializado", desc: "Equipe técnica para auxiliar na escolha ideal para o seu projeto." },
];

const DifferentialsSection = () => {
  return (
    <section id="diferenciais" className="py-20 md:py-28 bg-background">
      <div className="container mx-auto px-4">
        <ScrollReveal>
          <div className="text-center mb-14">
            <span className="text-primary font-semibold text-sm uppercase tracking-widest">Por que Pedras Nobres?</span>
            <h2 className="font-heading text-3xl md:text-4xl lg:text-5xl font-bold text-foreground mt-3">
              Nossos Diferenciais
            </h2>
          </div>
        </ScrollReveal>

        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-8">
          {items.map((item, i) => (
            <ScrollReveal key={item.title} delay={i * 0.15}>
              <div className="text-center p-6 rounded-xl bg-card border border-border hover:border-primary/30 transition-colors duration-300">
                <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-xl bg-primary/10">
                  <item.icon className="h-7 w-7 text-primary" />
                </div>
                <h3 className="font-heading text-lg font-semibold text-foreground mb-2">{item.title}</h3>
                <p className="text-sm text-muted-foreground leading-relaxed">{item.desc}</p>
              </div>
            </ScrollReveal>
          ))}
        </div>
      </div>
    </section>
  );
};

export default DifferentialsSection;

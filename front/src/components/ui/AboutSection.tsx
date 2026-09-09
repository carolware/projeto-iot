import ScrollReveal from "./ScrollReveal";
import aboutImg from "@/assets/img4.jpeg";
import { MapPin } from "lucide-react";

const AboutSection = () => {
  return (
    <section id="sobre" className="py-20 md:py-28 bg-background">
      <div className="container mx-auto px-4">
        <div className="grid md:grid-cols-2 gap-12 lg:gap-20 items-center">
          <ScrollReveal direction="left">
            <div className="relative overflow-hidden rounded-2xl">
              <img src={aboutImg} alt="Pedreira Pedras Nobres" className="w-full h-[400px] md:h-[500px] object-cover" />
              <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-stone_dark/60 to-transparent p-6">
                <div className="flex items-center gap-2 text-primary-foreground">
                  <MapPin className="h-5 w-5" />
                  <span className="text-sm font-medium">Pirenópolis – GO</span>
                </div>
              </div>
            </div>
          </ScrollReveal>

          <ScrollReveal direction="right" delay={0.2}>
            <span className="text-primary font-semibold text-sm uppercase tracking-widest">Sobre Nós</span>
            <h2 className="font-heading text-3xl md:text-4xl lg:text-5xl font-bold text-foreground mt-3 mb-6">
              Tradição e Excelência em Pedras Naturais
            </h2>
            <p className="text-muted-foreground leading-relaxed mb-4">
              A Pedras Nobres é referência na extração e comercialização de pedras naturais diretamente da pedreira em Pirenópolis, Goiás. Com anos de experiência, oferecemos materiais de alta qualidade para revestimentos, pisos, fachadas e projetos paisagísticos.
            </p>
            <p className="text-muted-foreground leading-relaxed mb-6">
              Trabalhamos com pedras selecionadas que garantem beleza, resistência e durabilidade, atendendo desde obras residenciais até grandes empreendimentos em todo o Brasil.
            </p>
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <MapPin className="h-4 w-4 text-primary" />
              <span>GO-225, Km 7.7 – Morro Alto, Pirenópolis – GO, CEP: 72980-000</span>
            </div>
          </ScrollReveal>
        </div>
      </div>
    </section>
  );
};

export default AboutSection;

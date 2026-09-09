import ScrollReveal from "./ScrollReveal";
import { MessageCircle } from "lucide-react";

const CTASection = () => {
  return (
    <section id="contato" className="py-20 md:py-28 bg-primary relative overflow-hidden">
      <div className="absolute inset-0 opacity-10">
        <div className="absolute top-0 left-0 w-96 h-96 bg-primary-foreground rounded-full -translate-x-1/2 -translate-y-1/2" />
        <div className="absolute bottom-0 right-0 w-72 h-72 bg-primary-foreground rounded-full translate-x-1/3 translate-y-1/3" />
      </div>

      <div className="container relative z-10 mx-auto px-4 text-center">
        <ScrollReveal>
          <h2 className="font-heading text-3xl md:text-4xl lg:text-5xl font-bold text-primary-foreground mb-6">
            Transforme Seu Projeto com Pedras Nobres
          </h2>
          <p className="text-primary-foreground/80 text-lg max-w-2xl mx-auto mb-10">
            Entre em contato agora mesmo e receba um orçamento personalizado. Atendemos todo o Brasil com qualidade e agilidade.
          </p>
          <a
            href="https://wa.me/5562982572304?text=Olá!%20Gostaria%20de%20solicitar%20um%20orçamento%20para%20meu%20projeto."
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-3 rounded-lg bg-primary-foreground px-10 py-4 text-lg font-bold text-primary transition-transform hover:scale-105"
          >
            <MessageCircle className="h-6 w-6" />
            Falar no WhatsApp
          </a>
        </ScrollReveal>
      </div>
    </section>
  );
};

export default CTASection;

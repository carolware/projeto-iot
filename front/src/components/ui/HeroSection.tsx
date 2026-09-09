import { motion } from "framer-motion";
import heroImg from "@/assets/img1.jpeg";

const HeroSection = () => {
  return (
    <section id="hero" className="relative min-h-screen flex items-center overflow-hidden">
      <div className="absolute inset-0">
        <img src={heroImg} alt="Fachada com pedras nobres" className="h-full w-full object-cover" />
        <div className="absolute inset-0 bg-gradient-to-r from-stone_dark/80 via-stone_dark/50 to-transparent" />
      </div>

      <div className="container relative z-10 mx-auto px-4 py-32 md:py-0">
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.2 }}
          className="max-w-2xl"
        >
          <span className="inline-block rounded-full bg-primary/20 px-4 py-1.5 text-sm font-medium text-primary-foreground backdrop-blur-sm mb-6">
            Qualidade direto da pedreira
          </span>
          <h1 className="font-heading text-4xl md:text-6xl lg:text-7xl font-bold text-primary-foreground leading-tight mb-6">
            Beleza Natural que Transforma Ambientes
          </h1>
          <p className="text-lg md:text-xl text-primary-foreground/80 mb-8 max-w-lg font-body">
            Pedras nobres selecionadas para revestimentos, pisos e fachadas. Elegância e durabilidade para sua obra.
          </p>
          <div className="flex flex-col sm:flex-row gap-4">
            <a
              href="https://wa.me/5562982572304?text=Olá!%20Gostaria%20de%20solicitar%20um%20orçamento."
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-8 py-3.5 text-base font-semibold text-primary-foreground transition-transform hover:scale-105"
            >
              Solicitar Orçamento
            </a>
            <a
              href="#produtos"
              className="inline-flex items-center justify-center gap-2 rounded-lg border-2 border-primary-foreground/30 px-8 py-3.5 text-base font-semibold text-primary-foreground backdrop-blur-sm transition-colors hover:bg-primary-foreground/10"
            >
              Ver Produtos
            </a>
          </div>
        </motion.div>
      </div>
    </section>
  );
};

export default HeroSection;

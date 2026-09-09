import ScrollReveal from "./ScrollReveal";
import img2 from "@/assets/img2.jpeg";
import img3 from "@/assets/img3.jpeg";
import img5 from "@/assets/img5.jpeg";
import img6 from "@/assets/img6.jpeg";
import img7 from "@/assets/img7.jpeg";
import img8 from "@/assets/img8.jpeg";

const products = [
  {
    name: "Pedra São Tomé Branca",
    desc: "Ideal para pisos e revestimentos externos",
    img: img6,
    details: [
      "Cor clara e uniforme",
      "Alta resistência a intempéries",
      "Antiderrapante natural",
      "Ideal para áreas externas e piscinas",
    ],
  },
  {
    name: "Pedra Miracema Cinza",
    desc: "Perfeita para fachadas e muros",
    img: img7,
    details: [
      "Textura rústica e elegante",
      "Excelente aderência",
      "Durabilidade superior",
      "Versátil para pisos e paredes",
    ],
  },
  {
    name: "Pedra Madeira",
    desc: "Telhados e coberturas rústicas",
    img: img8,
    details: [
      "Aparência que imita madeira",
      "Leve e fácil de instalar",
      "Resistente ao calor",
      "Perfeita para áreas gourmet",
    ],
  },
  {
    name: "Pedra Caco São Tomé",
    desc: "Revestimento de paredes e pisos irregulares",
    img: img5,
    details: [
      "Formato irregular e único",
      "Encaixe tipo mosaico",
      "Ótimo custo-benefício",
      "Ideal para calçadas e jardins",
    ],
  },
  {
    name: "Pedra Natural para Fachada",
    desc: "Elegância moderna em fachadas",
    img: img3,
    details: [
      "Acabamento sofisticado",
      "Valoriza o imóvel",
      "Baixa manutenção",
      "Disponível em várias tonalidades",
    ],
  },
  {
    name: "Pedra para Muro de Arrimo",
    desc: "Resistência e beleza estrutural",
    img: img2,
    details: [
      "Alta resistência mecânica",
      "Suporta grandes cargas",
      "Drenagem natural",
      "Ideal para contenção de terrenos",
    ],
  },
];

const ProductsSection = () => {
  return (
    <section id="produtos" className="py-20 md:py-28 bg-muted">
      <div className="container mx-auto px-4">
        <ScrollReveal>
          <div className="text-center mb-14">
            <span className="text-primary font-semibold text-sm uppercase tracking-widest">Catálogo</span>
            <h2 className="font-heading text-3xl md:text-4xl lg:text-5xl font-bold text-foreground mt-3">
              Nossos Produtos
            </h2>
            <p className="text-muted-foreground mt-4 max-w-2xl mx-auto">
              Cada pedra tem características únicas que a tornam ideal para diferentes aplicações. Conheça os detalhes e encontre a perfeita para o seu projeto.
            </p>
          </div>
        </ScrollReveal>

        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-6">
          {products.map((product, i) => (
            <ScrollReveal key={product.name} delay={i * 0.1}>
              <div className="group overflow-hidden rounded-xl bg-card shadow-sm hover:shadow-lg transition-shadow duration-300">
                <div className="relative h-64 overflow-hidden">
                  <img
                    src={product.img}
                    alt={product.name}
                    className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-110"
                  />
                  <div className="absolute inset-0 bg-gradient-to-t from-stone_dark/50 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
                </div>
                <div className="p-5">
                  <h3 className="font-heading text-lg font-semibold text-foreground">{product.name}</h3>
                  <p className="text-sm text-muted-foreground mt-1">{product.desc}</p>
                  <ul className="mt-3 space-y-1.5">
                    {product.details.map((detail) => (
                      <li key={detail} className="flex items-center gap-2 text-sm text-muted-foreground">
                        <span className="w-1.5 h-1.5 rounded-full bg-primary flex-shrink-0" />
                        {detail}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </ScrollReveal>
          ))}
        </div>

        <ScrollReveal delay={0.3}>
          <div className="text-center mt-12">
            <a
              href="https://wa.me/5562982572304?text=Olá!%20Gostaria%20de%20saber%20mais%20sobre%20os%20produtos."
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-8 py-3.5 text-base font-semibold text-primary-foreground transition-transform hover:scale-105"
            >
              Solicitar Catálogo Completo
            </a>
          </div>
        </ScrollReveal>
      </div>
    </section>
  );
};

export default ProductsSection;

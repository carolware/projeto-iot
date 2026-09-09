import logo from "@/assets/logo.png";
import { Instagram, MapPin, Phone } from "lucide-react";

const Footer = () => {
  return (
    <footer className="bg-stone_dark py-12">
      <div className="container mx-auto px-4">
        <div className="grid md:grid-cols-3 gap-8 items-start">
          <div>
            <img src={logo} alt="Pedras Nobres" className="h-10 mb-4" />
            <p className="text-sm text-stone_warm">
              Pedras naturais de alta qualidade direto da pedreira em Pirenópolis, Goiás.
            </p>
          </div>

          <div>
            <h4 className="font-heading text-primary-foreground font-semibold mb-3">Contato</h4>
            <div className="space-y-2 text-sm text-stone_warm">
              <div className="flex items-center gap-2">
                <Phone className="h-4 w-4" />
                <span>(62) 98257-2304</span>
              </div>
              <div className="flex items-start gap-2">
                <MapPin className="h-4 w-4 mt-0.5 shrink-0" />
                <span>GO-225, Km 7.7 – Morro Alto, Pirenópolis – GO, CEP: 72980-000</span>
              </div>
            </div>
          </div>

          <div>
            <h4 className="font-heading text-primary-foreground font-semibold mb-3">Redes Sociais</h4>
            <a
              href="https://www.instagram.com/pedrasnobres"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 text-sm text-stone_warm hover:text-primary transition-colors"
            >
              <Instagram className="h-5 w-5" />
              @pedrasnobres
            </a>
          </div>
        </div>

        <div className="mt-10 pt-6 border-t border-primary-foreground/10 text-center text-xs text-stone_warm">
          <p>
            © {new Date().getFullYear()} Pedras Nobres. Todos os direitos reservados. Desenvolvido por{" "}
            <a
              href="https://www.flexwebsolutions.com.br/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline font-medium"
            >
              Flex Web
            </a>
          </p>
        </div>
      </div>
    </footer>
  );
};

export default Footer;

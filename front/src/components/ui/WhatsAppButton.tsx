import { MessageCircle } from "lucide-react";

const WHATSAPP_NUMBER = "5562982572304";
const MESSAGE = "Olá! Gostaria de saber mais sobre as pedras. Vim pelo site!";

const WhatsAppButton = () => {
  const url = `https://wa.me/${WHATSAPP_NUMBER}?text=${encodeURIComponent(MESSAGE)}`;

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      aria-label="Contato via WhatsApp"
      className="fixed bottom-6 right-6 z-50 flex h-14 w-14 items-center justify-center rounded-full bg-whatsapp shadow-lg transition-transform duration-200 hover:scale-110 md:h-16 md:w-16"
    >
      <MessageCircle className="h-7 w-7 text-primary-foreground md:h-8 md:w-8" />
    </a>
  );
};

export default WhatsAppButton;

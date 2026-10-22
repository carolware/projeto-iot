/** Endereços da plataforma (variáveis VITE_* embutidas no build). */
export const API_URL = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
export const WS_URL = API_URL.replace(/^http/, "ws");
export const KEYCLOAK_URL = import.meta.env.VITE_KEYCLOAK_URL ?? "http://localhost:8080";
export const KEYCLOAK_REALM = import.meta.env.VITE_KEYCLOAK_REALM ?? "sentinela";
export const KEYCLOAK_CLIENTE = import.meta.env.VITE_KEYCLOAK_CLIENTE ?? "painel";

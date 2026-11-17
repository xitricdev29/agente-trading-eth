import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// El proxy evita exponer el backend fuera de localhost y evita CORS en desarrollo.
// No se define ninguna variable VITE_* con secretos: la API key se escribe en la UI en tiempo de ejecución.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});

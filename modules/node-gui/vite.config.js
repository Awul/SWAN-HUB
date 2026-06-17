import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // allows access from any IP
    port: 5173,
    allowedHosts: ["swan-hub", "localhost", "host.docker.internal", "swan-hub.local"]
  }
});
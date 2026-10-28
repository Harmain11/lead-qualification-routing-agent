import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = fileURLToPath(new URL(".", import.meta.url));
const interFontRoot = resolve(frontendRoot, "../node_modules/@fontsource-variable/inter");

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8001";
  const mailpitProxyTarget = env.VITE_MAILPIT_PROXY_TARGET || "http://127.0.0.1:8025";
  const configuredHosts = env.VITE_ALLOWED_HOSTS
    ? env.VITE_ALLOWED_HOSTS.split(",").map((host) => host.trim()).filter(Boolean)
    : [];
  const allowedHosts = Array.from(
    new Set([...configuredHosts, "localhost", "lead-agent-frontend", "host.docker.internal"]),
  );

  return {
    plugins: [react()],
    server: {
      allowedHosts,
      port: 5173,
      fs: {
        allow: [frontendRoot, interFontRoot],
      },
      headers: {
        "Permissions-Policy":
          'camera=(), microphone=(self "https://phone.cloudtalk.io" "https://workspace.aircall.io" "https://apps.ringcentral.com"), geolocation=()',
        "Content-Security-Policy":
          "frame-src 'self' https://phone.cloudtalk.io https://workspace.aircall.io https://apps.ringcentral.com; frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
      },
      proxy: {
        "/v1": apiProxyTarget,
        "/health": apiProxyTarget,
        "/__mailpit": {
          target: mailpitProxyTarget,
          changeOrigin: true,
        },
      },
    },
  };
});

import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

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

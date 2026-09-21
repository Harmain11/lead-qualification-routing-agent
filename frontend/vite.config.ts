import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8000";
  const allowedHosts = env.VITE_ALLOWED_HOSTS
    ? env.VITE_ALLOWED_HOSTS.split(",").map((host) => host.trim()).filter(Boolean)
    : [];

  return {
    plugins: [react()],
    server: {
      allowedHosts,
      port: 5173,
      proxy: {
        "/v1": apiProxyTarget,
        "/health": apiProxyTarget,
      },
    },
  };
});

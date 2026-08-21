import { fileURLToPath, URL } from "node:url";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const projectRoot = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig(({ mode }) => {
  // Read the same root .env as the backend. The Docker build context is only
  // web/, so Compose supplies the public values as build arguments instead.
  const environment = loadEnv(mode, projectRoot, "");
  const browserValue = (name: string, fallbackName: string, fallback: string) =>
    environment[name] ?? environment[fallbackName] ?? fallback;

  return {
    envDir: projectRoot,
    plugins: [react()],
    define: {
      "import.meta.env.VITE_API_BASE_URL": JSON.stringify(
        environment.VITE_API_BASE_URL ?? "/api/v1"
      ),
      "import.meta.env.VITE_CSRF_COOKIE_NAME": JSON.stringify(
        browserValue("VITE_CSRF_COOKIE_NAME", "CSRF_COOKIE_NAME", "nairag_csrf")
      ),
      "import.meta.env.VITE_PRE_AUTH_CSRF_COOKIE_NAME": JSON.stringify(
        browserValue(
          "VITE_PRE_AUTH_CSRF_COOKIE_NAME",
          "PRE_AUTH_CSRF_COOKIE_NAME",
          "nairag_pre_auth_csrf"
        )
      ),
      "import.meta.env.VITE_LLM_MAX_CONVERSATION_MESSAGES": JSON.stringify(
        browserValue(
          "VITE_LLM_MAX_CONVERSATION_MESSAGES",
          "LLM_MAX_CONVERSATION_MESSAGES",
          "200"
        )
      ),
      "import.meta.env.VITE_LLM_MAX_CONVERSATION_CHARS": JSON.stringify(
        browserValue(
          "VITE_LLM_MAX_CONVERSATION_CHARS",
          "LLM_MAX_CONVERSATION_CHARS",
          "30000"
        )
      )
    },
    server: {
      proxy: {
        "/api": {
          target: environment.VITE_PROXY_TARGET ?? "http://127.0.0.1:8000",
          changeOrigin: true
        }
      }
    }
  };
});

import { defineConfig } from "vitest/config";

export default defineConfig({
  worker: { format: "es" },
  test: { include: ["test/**/*.test.ts"] },
});

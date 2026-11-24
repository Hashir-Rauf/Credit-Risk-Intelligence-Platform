import { defineConfig, devices } from "@playwright/test";

const WEB_PORT = 3100;
const MOCK_PORT = 8099;
export const E2E_KEY = "e2e-secret-key-should-never-reach-the-browser"; // gitleaks:allow (fake key, e2e only)

// Builds nothing itself: run `npm run build` first (CI does). Starts a mock API and the built app.
export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://127.0.0.1:${WEB_PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "node e2e/mock-api.mjs",
      port: MOCK_PORT,
      reuseExistingServer: !process.env.CI,
      env: { MOCK_API_PORT: String(MOCK_PORT), MOCK_API_KEY: E2E_KEY },
    },
    {
      command: `node e2e/serve.mjs`,
      port: WEB_PORT,
      reuseExistingServer: !process.env.CI,
      env: {
        PORT: String(WEB_PORT),
        HOSTNAME: "127.0.0.1",
        API_URL: `http://127.0.0.1:${MOCK_PORT}`,
        API_KEY: E2E_KEY,
      },
    },
  ],
});

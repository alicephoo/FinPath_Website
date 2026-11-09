import { defineConfig, devices } from '@playwright/test'

/**
 * Browser E2E configuration.
 *
 * Run with:
 *   cd frontend && npm run test:e2e
 *
 * The `webServer` entries start the API against the dedicated test database
 * (see e2e/start-backend.sh) and the Vite dev server. Unit tests (Vitest) live
 * in src/ and tests/; Playwright only collects e2e/**\/*.spec.ts.
 *
 * Both the browser and the API use 127.0.0.1 so the HttpOnly refresh cookie
 * has one consistent host and stays same-site with the page.
 */
const backendPort = process.env.E2E_BACKEND_PORT ?? '8000'
const frontendPort = process.env.E2E_FRONTEND_PORT ?? '5173'
const host = '127.0.0.1'
const backendUrl = process.env.E2E_BACKEND_URL ?? `http://${host}:${backendPort}`
const frontendUrl = `http://${host}:${frontendPort}`

// Web server processes inherit this environment.
process.env.E2E_BACKEND_PORT = backendPort
process.env.E2E_BACKEND_URL = backendUrl
process.env.VITE_API_BASE_URL = `${backendUrl}/api/v1`

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  outputDir: './test-results',
  fullyParallel: false,
  // Shared test database and a single local API server: keep tests serial.
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: 'playwright-report' }],
  ],
  use: {
    baseURL: frontendUrl,
    actionTimeout: 10_000,
    navigationTimeout: 20_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: [
    {
      command: 'bash ./e2e/start-backend.sh',
      url: `${backendUrl}/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npm run dev -- --host ${host} --port ${frontendPort} --strictPort`,
      url: frontendUrl,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})

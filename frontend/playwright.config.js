import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 45000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.CITALENS_BASE_URL || 'http://127.0.0.1:5173',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    launchOptions: process.env.CITALENS_BROWSER_PATH ? { executablePath: process.env.CITALENS_BROWSER_PATH } : {},
  },
  webServer: process.env.CITALENS_E2E_LIVE ? undefined : {
    command: 'npm run dev -- --host 127.0.0.1 --port 5173',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: true,
  },
});

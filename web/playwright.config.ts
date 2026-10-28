import { defineConfig, devices } from '@playwright/test';
export default defineConfig({
  testDir: './e2e',
  retries: 0,
  workers: 1,
  forbidOnly: true,
  reporter: [['list'], ['html', { outputFolder: '../artifacts/playwright', open: 'never' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ],
});

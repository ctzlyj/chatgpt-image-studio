import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser',
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:8779', viewport: { width: 1440, height: 1000 },
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {} },
  webServer: { command: `${process.platform === 'win32' ? '".venv\\Scripts\\python.exe"' : '.venv/bin/python'} -m tests.e2e_server`, url: 'http://127.0.0.1:8779', reuseExistingServer: false, timeout: 30000 },
});

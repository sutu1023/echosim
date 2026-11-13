import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', proxy: { '/api': process.env.API_ORIGIN ?? 'http://127.0.0.1:8081', '/health': process.env.API_ORIGIN ?? 'http://127.0.0.1:8081' } },
  test: { environment: 'jsdom', setupFiles: ['./src/test-setup.ts'] },
});

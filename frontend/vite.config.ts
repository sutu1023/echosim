import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/health': process.env.API_ORIGIN ?? 'http://127.0.0.1:8081' } },
  test: { environment: 'jsdom', setupFiles: ['./src/test-setup.ts'] },
});

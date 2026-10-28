import { defineConfig } from 'vite';
export default defineConfig(({ command }) => {
  const target = process.env.API_UPSTREAM;
  if (command === 'serve' && !target)
    throw new Error('API_UPSTREAM must be configured for development');
  return { server: { proxy: target ? { '/health': { target } } : undefined } };
});

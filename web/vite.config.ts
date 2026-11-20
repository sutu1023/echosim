import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建产物固定输出到 web/dist，由 FastAPI 在同一 origin 下托管。
export default defineConfig({
  plugins: [vue()],
  base: '/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})

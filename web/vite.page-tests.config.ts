import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 页面级测试使用客户端编译产物，确保点击、请求与渲染都经过真实组件代码。
export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: '.page-build',
    emptyOutDir: true,
    cssCodeSplit: false,
    lib: {
      entry: 'tests/page-entry.mjs',
      formats: ['es'],
      fileName: () => 'page-entry.js',
    },
    rollupOptions: {
      external: ['vue', 'vue-router'],
    },
  },
})

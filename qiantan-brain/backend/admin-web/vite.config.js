import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
    // 目标现代浏览器以减小 polyfill 体积
    target: 'es2020',
    // CSS 代码分割（避免单个巨大 CSS 文件）
    cssCodeSplit: true,
    // 对异常大块保持严格告警；路由和组件由 Rollup 自然拆分。
    chunkSizeWarningLimit: 500,
    // Terser 压缩选项
    minify: 'terser',
    terserOptions: {
      compress: {
        drop_console: true,
        drop_debugger: true,
      },
    },
    rollupOptions: {
      output: {
        manualChunks(id) {
          // 仅对已知大型依赖显式拆包，其余由 Vite 自动处理
          if (id.includes('node_modules')) {
            if (
              id.includes('/react/') ||
              id.includes('/react-dom/') ||
              id.includes('/react-router') ||
              id.includes('/scheduler/')
            ) {
              return 'react-vendor'
            }
            if (id.includes('/recharts/') || id.includes('/d3-')) {
              return 'chart-vendor'
            }
            // antd 依赖 rc-* 单向调用，可安全拆出，避免 UI 主块过大。
            if (id.includes('/node_modules/rc-')) {
              return 'antd-runtime-vendor'
            }
            if (
              id.includes('/node_modules/@ant-design/cssinjs') ||
              id.includes('/node_modules/@ant-design/fast-color')
            ) {
              return 'antd-runtime-vendor'
            }
            if (
              id.includes('/node_modules/@ant-design/icons/') ||
              id.includes('/node_modules/@ant-design/icons-svg/')
            ) {
              return 'antd-icons-vendor'
            }
            if (id.includes('/node_modules/dayjs/')) {
              return 'date-vendor'
            }
          }
        },
        // 稳定 chunk 命名
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
})



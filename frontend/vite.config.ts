import path from 'node:path';

import react from '@vitejs/plugin-react';
import { visualizer } from 'rollup-plugin-visualizer';
import { defineConfig, loadEnv } from 'vite';


// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // Load all env keys so AGENT_API_TOKEN (non-VITE_) is available for the proxy only.
  const env = loadEnv(mode, process.cwd(), '');
  const rootEnv = loadEnv(mode, path.resolve(__dirname, '..'), '');
  const agentApiToken = env.AGENT_API_TOKEN || rootEnv.AGENT_API_TOKEN || '';
  const isAnalyze = mode === 'analyze';
  const isProd = mode === 'production';

  return {
    plugins: [
      react(),
      // 构建体积分析：npm run build:analyze
      isAnalyze &&
        visualizer({
          filename: 'dist/stats.html',
          template: 'treemap',
          gzipSize: true,
          brotliSize: true,
          open: true,
        }),
    ].filter(Boolean),

    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },

    // 路径基础：默认相对路径，部署到子路径时改 '/app/'
    base: './',

    // 开发服务器
    server: {
      port: 3001,
      host: true, // 允许局域网访问，便于真机调试
      open: false,
      proxy: {
        '/api/v1/agent': {
          target: env.VITE_PROXY_TARGET ?? 'http://localhost:8080',
          changeOrigin: true,
          // WebSocket 支持（SSE 流式响应需要）
          ws: true,
          // Inject Bearer token server-side — never expose via VITE_* to the browser.
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              if (agentApiToken) {
                proxyReq.setHeader('Authorization', `Bearer ${agentApiToken}`);
              }
            });
          },
        },
        // 性能埋点上报代理
        '/api/v1/monitor': {
          target: env.VITE_PROXY_TARGET ?? 'http://localhost:8080',
          changeOrigin: true,
        },
      },
    },

    // 构建优化
    build: {
      target: 'es2020',
      outDir: 'dist',
      assetsDir: 'assets',
      sourcemap: isProd ? 'hidden' : true, // 生产构建生成 sourcemap 但不在输出中引用（供 Sentry 上传）
      cssCodeSplit: true,
      // 警告包大小（KB），超过警告
      chunkSizeWarningLimit: 1000,
      // 启用 minify（默认 esbuild）
      minify: 'esbuild',
      // 进度报告
      reportCompressedSize: false,

      rollupOptions: {
        output: {
          // 入口文件名（带 contenthash）
          entryFileNames: 'assets/[name]-[hash:8].js',
          // 代码块文件名
          chunkFileNames: 'assets/[name]-[hash:8].js',
          // 静态资源
          assetFileNames: 'assets/[name]-[hash:8][extname]',
          // 手动拆分 vendor，提升缓存命中率
          // 注意：更具体的规则必须放在更宽泛的规则之前，避免误匹配
          // 循环依赖的包须合并到同一 chunk，避免 Circular chunk 警告
          manualChunks(id: string): string | undefined {
            if (id.includes('node_modules')) {
              // React 核心（用路径分隔符限定，避免误匹配 react-markdown 等）
              if (
                id.includes('/node_modules/react/') ||
                id.includes('/node_modules/react-dom/') ||
                id.includes('/node_modules/react-router') ||
                id.includes('/node_modules/scheduler/')
              ) {
                return 'vendor-react';
              }
              if (id.includes('/node_modules/zustand/')) {
                return 'vendor-state';
              }
              // markdown 处理链、@sentry 与通用 vendor 存在交叉依赖，
              // 合并到 vendor 避免循环 chunk
              return 'vendor';
            }
            return undefined;
          },
        },
      },
    },

    // esbuild 配置
    esbuild: {
      // 生产构建移除 console.log（保留 warn/error）
      drop: isProd ? ['debugger'] : [],
      legalComments: 'none',
    },

    // 静态资源处理
    assetsInclude: ['**/*.webp', '**/*.avif'],

    // 环境变量前缀（仅暴露 VITE_ 开头）
    envPrefix: 'VITE_',

    // define 全局常量
    define: {
      __APP_VERSION__: JSON.stringify(env.VITE_APP_VERSION ?? '0.0.0'),
    },
  };
});

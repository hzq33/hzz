/**
 * Vitest 单元测试配置
 * 注意：vite.config.ts 导出函数形式（依赖 mode），无法直接 mergeConfig
 * 这里复用必要的 resolve.alias 等配置，独立定义 test 字段
 */
import path from 'node:path';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    // 环境选择 jsdom（DOM 相关测试）
    environment: 'jsdom',
    // 测试文件匹配模式
    include: ['src/**/*.{test,spec}.{ts,tsx}', 'tests/unit/**/*.{test,spec}.{ts,tsx}'],
    // 排除 e2e（由 Playwright 处理）
    exclude: ['node_modules/**', 'dist/**', 'tests/e2e/**', 'tests/integration/**'],
    // 启用全局 API（describe/it/expect 等无需 import）
    globals: true,
    // 覆盖率配置
    coverage: {
      provider: 'v8',
      reporter: ['text', 'text-summary', 'html', 'lcov'],
      reportsDirectory: './coverage',
      // Bootstrap gate: reusable helpers (exclude heavy api.ts + optional monitor).
      include: [
        'src/lib/errors.ts',
        'src/lib/config.ts',
        'src/lib/sse.ts',
        'src/lib/pollJob.ts',
        'src/lib/constants.ts',
      ],
      thresholds: {
        statements: 40,
        branches: 0,
        functions: 30,
        lines: 40,
      },
      exclude: [
        'node_modules/**',
        'dist/**',
        'coverage/**',
        '**/*.config.{ts,js}',
        '**/*.d.ts',
        'src/main.tsx',
        'src/vite-env.d.ts',
      ],
    },
    // setupFiles: ['./tests/setup.ts'],
  },
});

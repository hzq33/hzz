// ESLint Flat Config — React + TypeScript 严格规则
// 文档: https://eslint.org/docs/latest/use/configure/configuration-files
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import importPlugin from 'eslint-plugin-import';

export default tseslint.config(
  /* ── 全局忽略 ── */
  {
    ignores: [
      'dist/**',
      'dist-electron/**',
      'node_modules/**',
      'coverage/**',
      'playwright-report/**',
      'test-results/**',
      'vite.config.ts.timestamp-*',
      '*.cjs',
      'eslint-report.json',
    ],
  },

  /* ── 基础推荐规则 ── */
  js.configs.recommended,

  /* ── TypeScript 严格类型检查 ──
   * 注意：recommendedTypeChecked 需要 project 信息，
   * 通过 languageOptions.parserOptions 显式指定
   */
  ...tseslint.configs.recommendedTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.json', './tsconfig.node.json', './electron/tsconfig.json'],
        tsconfigRootDir: import.meta.dirname,
        ecmaFeatures: { jsx: true },
        ecmaVersion: 2020,
        sourceType: 'module',
      },
    },
  },

  /* ── React 推荐规则（手动转 flat 风格，避免引入 eslintrc 风格的 parserOptions） ── */
  {
    files: ['**/*.{ts,tsx,jsx}'],
    plugins: {
      react,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
      import: importPlugin,
    },
    settings: {
      react: { version: 'detect' },
      'import/resolver': {
        typescript: { project: './tsconfig.json' },
      },
    },
    languageOptions: {
      globals: {
        // 浏览器全局变量
        window: 'readonly',
        document: 'readonly',
        localStorage: 'readonly',
        sessionStorage: 'readonly',
        navigator: 'readonly',
        fetch: 'readonly',
        crypto: 'readonly',
        URL: 'readonly',
        URLSearchParams: 'readonly',
        console: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
        setInterval: 'readonly',
        clearInterval: 'readonly',
        AbortController: 'readonly',
        ReadableStream: 'readonly',
        TextDecoder: 'readonly',
        TextEncoder: 'readonly',
        Event: 'readonly',
        EventTarget: 'readonly',
        ErrorEvent: 'readonly',
        PromiseRejectionEvent: 'readonly',
        HTMLElement: 'readonly',
        HTMLInputElement: 'readonly',
        HTMLTextAreaElement: 'readonly',
        HTMLButtonElement: 'readonly',
        HTMLFormElement: 'readonly',
        HTMLLabelElement: 'readonly',
        File: 'readonly',
        FormData: 'readonly',
        Blob: 'readonly',
        Location: 'readonly',
        History: 'readonly',
        IntersectionObserver: 'readonly',
        ResizeObserver: 'readonly',
        MutationObserver: 'readonly',
        WebSocket: 'readonly',
        ScrollToOptions: 'readonly',
      },
    },
    rules: {
      /* ── React 推荐规则（精选，不引入 eslintrc 风格） ── */
      'react/prop-types': 'off', // TS 已覆盖
      'react/react-in-jsx-scope': 'off',
      'react/jsx-uses-react': 'off',
      'react/jsx-uses-vars': 'error',
      'react/no-deprecated': 'error',
      'react/no-direct-mutation-state': 'error',
      'react/no-find-dom-node': 'error',
      'react/no-is-mounted': 'error',
      'react/no-render-return-value': 'error',
      'react/no-string-refs': 'error',
      'react/no-unknown-property': 'error',
      'react/no-unsafe': 'warn',
      'react/self-closing-comp': 'error',
      'react/jsx-boolean-value': ['error', 'never'],
      'react/jsx-fragments': ['error', 'syntax'],
      'react/jsx-no-useless-fragment': ['error', { allowExpressions: true }],
      'react/jsx-key': 'error',
      'react/no-array-index-key': 'warn',
      'react/no-unused-state': 'error',
      'react/prefer-es6-class': 'error',
      'react/display-name': 'warn',

      /* ── React Hooks ── */
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',

      /* ── React Refresh (HMR) ── */
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],

      /* ── Import 排序 ── */
      'import/order': [
        'error',
        {
          'newlines-between': 'always',
          alphabetize: { order: 'asc', caseInsensitive: true },
          groups: [
            'builtin',
            'external',
            'internal',
            'parent',
            'sibling',
            'index',
            'type',
          ],
          pathGroups: [
            { pattern: 'react', group: 'external', position: 'before' },
            { pattern: '@/**', group: 'internal', position: 'after' },
          ],
          pathGroupsExcludedImportTypes: ['react'],
        },
      ],
      'import/no-duplicates': 'error',
      'import/no-cycle': ['error', { maxDepth: 10 }],

      /* ── TypeScript 严格化 ──
       * 渐进迁移策略：
       * - 错误级：no-floating-promises / no-misused-promises（高频 Bug 来源）
       * - 警告级：unsafe-* 系列（fetch 返回 any 等历史代码，分批修复）
       */
      '@typescript-eslint/no-floating-promises': 'error',
      '@typescript-eslint/no-misused-promises': 'error',
      '@typescript-eslint/await-thenable': 'error',
      '@typescript-eslint/consistent-type-imports': [
        'error',
        { prefer: 'type-imports', fixStyle: 'inline-type-imports' },
      ],
      '@typescript-eslint/no-unused-vars': [
        'warn',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-unsafe-assignment': 'warn',
      '@typescript-eslint/no-unsafe-argument': 'warn',
      '@typescript-eslint/no-unsafe-member-access': 'warn',
      '@typescript-eslint/no-unsafe-call': 'warn',
      '@typescript-eslint/no-unsafe-return': 'warn',
      '@typescript-eslint/require-await': 'warn',

      /* ── 通用代码质量 ── */
      'no-console': ['warn', { allow: ['warn', 'error', 'info'] }],
      'no-debugger': 'error',
      'no-unused-private-class-members': 'error',
      'prefer-const': 'error',
      'no-var': 'error',
      eqeqeq: ['error', 'always', { null: 'ignore' }],
    },
  },

  /* ── 配置文件（vite/eslint/postcss/tailwind）特殊规则 ──
   * 这些 .js 文件不在 tsconfig project 中，
   * 用 disableTypeChecked 完全禁用 type-aware 规则
   */
  {
    files: [
      '*.config.js',
      'postcss.config.js',
      'tailwind.config.js',
      '.lintstagedrc.js',
      'commitlint.config.js',
    ],
    extends: [tseslint.configs.disableTypeChecked],
    languageOptions: {
      globals: {
        // Node.js CommonJS 全局变量（commitlint/postcss 等配置文件使用）
        module: 'readonly',
        require: 'readonly',
        process: 'readonly',
        __dirname: 'readonly',
        __filename: 'readonly',
        Buffer: 'readonly',
        global: 'readonly',
      },
    },
    rules: {
      '@typescript-eslint/no-var-requires': 'off',
      'import/no-default-export': 'off',
      'react-refresh/only-export-components': 'off',
    },
  },

  /* ── Playwright 配置 ──
   * @playwright/test 为可选依赖（未安装时类型无法解析），
   * 禁用 type-aware 规则避免误报
   */
  {
    files: ['playwright.config.ts'],
    extends: [tseslint.configs.disableTypeChecked],
    rules: {
      '@typescript-eslint/no-unsafe-call': 'off',
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/no-unsafe-member-access': 'off',
    },
  },

  /* ── Playwright E2E 测试 ──
   * tests/e2e 不在 tsconfig.json include 中（与 playwright.config.ts 同理），
   * 禁用 type-aware 规则避免 parserOptions.project 解析错误
   */
  {
    files: ['tests/e2e/**/*.ts'],
    extends: [tseslint.configs.disableTypeChecked],
  },

  /* ── 测试文件放宽 ── */
  {
    files: ['**/*.{test,spec}.{ts,tsx}', 'tests/**/*.{ts,tsx}'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-non-null-assertion': 'off',
      'react-refresh/only-export-components': 'off',
      '@typescript-eslint/no-floating-promises': 'off',
      '@typescript-eslint/no-misused-promises': 'off',
      // 测试中常用 vi.spyOn(obj, 'method') 解绑方法
      '@typescript-eslint/unbound-method': 'off',
    },
  },
);

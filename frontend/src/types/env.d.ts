/// <reference types="vite/client" />

/**
 * 环境变量类型声明
 * 所有以 VITE_ 开头的变量会通过 import.meta.env 暴露给客户端
 * 文档: https://vitejs.dev/guide/env-and-mode.html
 *
 * 注意：不要使用 `export`，否则会破坏与 vite/client 的全局声明合并，
 * 导致 import.meta.env 被推断为 any。
 */
interface ImportMetaEnv {
  /** 应用标题 */
  readonly VITE_APP_TITLE: string;
  /** 应用版本 */
  readonly VITE_APP_VERSION: string;
  /** API 基础路径 */
  readonly VITE_API_BASE_URL: string;
  /** Sentry DSN（留空则不启用） */
  readonly VITE_SENTRY_DSN: string;
  /** 性能采样率 0~1 */
  readonly VITE_TRACES_SAMPLE_RATE: string;
  /** 调试模式 */
  readonly VITE_DEBUG: string;
  /** 是否启用 PWA */
  readonly VITE_ENABLE_PWA: string;
  /** 是否启用 Mock */
  readonly VITE_ENABLE_MOCK: string;
  /** 默认语言 */
  readonly VITE_DEFAULT_LOCALE: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

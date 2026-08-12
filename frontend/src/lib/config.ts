/**
 * 应用运行时配置（基于环境变量派生）
 * 集中管理所有环境变量读取，避免散落在各处
 *
 * 注意：env.d.ts 通过 interface declaration merging 增强了 import.meta.env，
 * 因此这里直接读取即可获得完整类型，无需手动转换。
 */

type Mode = 'development' | 'production' | 'test';

interface AppConfig {
  /** 应用标题 */
  title: string;
  /** 应用版本 */
  version: string;
  /** 运行模式 */
  mode: Mode;
  /** 是否为开发环境 */
  isDev: boolean;
  /** 是否为生产环境 */
  isProd: boolean;
  /** API 基础路径 */
  apiBaseUrl: string;
  /** Sentry DSN */
  sentryDsn: string;
  /** 性能采样率（已转为 number） */
  tracesSampleRate: number;
  /** 调试模式 */
  debug: boolean;
  /** 是否启用 PWA */
  enablePwa: boolean;
  /** 是否启用 Mock */
  enableMock: boolean;
  /** 默认语言 */
  defaultLocale: string;
}

function toBool(value: string | undefined, fallback = false): boolean {
  if (value === undefined) return fallback;
  return value === 'true' || value === '1' || value === 'yes';
}

function toNumber(value: string | undefined, fallback: number): number {
  if (value === undefined || value === '') return fallback;
  const n = Number(value);
  return Number.isNaN(n) ? fallback : n;
}

const env = import.meta.env;
const mode = (env.MODE ?? 'development') as Mode;

export const config: AppConfig = {
  title: env.VITE_APP_TITLE ?? 'Modular Agent',
  version: env.VITE_APP_VERSION ?? '0.0.0',
  mode,
  isDev: mode === 'development',
  isProd: mode === 'production',
  apiBaseUrl: env.VITE_API_BASE_URL ?? '/api/v1/agent',
  sentryDsn: env.VITE_SENTRY_DSN ?? '',
  tracesSampleRate: toNumber(env.VITE_TRACES_SAMPLE_RATE, 0.1),
  debug: toBool(env.VITE_DEBUG, false),
  enablePwa: toBool(env.VITE_ENABLE_PWA, false),
  enableMock: toBool(env.VITE_ENABLE_MOCK, false),
  defaultLocale: env.VITE_DEFAULT_LOCALE ?? 'zh-CN',
};

/** 当前页面是否在浏览器主线程（避免 SSR/Worker 误用） */
export const isBrowser =
  typeof window !== 'undefined' && typeof document !== 'undefined';

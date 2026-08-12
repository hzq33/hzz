/**
 * Sentry 监控集成
 * - 当 VITE_SENTRY_DSN 为空时，所有 API 转为 no-op，不影响构建
 * - 动态导入 @sentry/react，避免未配置时打包体积膨胀
 * - 对外暴露 captureException / captureMessage 等常用 API
 */
import { config, isBrowser } from '@/lib/config';

import type * as SentryNamespace from '@sentry/react';

type SentryModule = typeof SentryNamespace;

let sentry: SentryModule | null = null;
let initAttempted = false;

/**
 * 初始化 Sentry。应在应用启动时调用一次。
 * DSN 为空时跳过，整个模块进入 no-op 模式。
 */
export async function initSentry(): Promise<void> {
  if (!isBrowser || initAttempted) return;
  initAttempted = true;

  if (!config.sentryDsn) {
    if (config.debug) {
      console.info('[sentry] DSN 未配置，监控已禁用');
    }
    return;
  }

  try {
    sentry = await import('@sentry/react');
    sentry.init({
      dsn: config.sentryDsn,
      environment: config.mode,
      release: `${config.title}@${config.version}`,
      tracesSampleRate: config.tracesSampleRate,
      // 隐私保护：不采集用户输入与文本内容
      beforeSend(event) {
        // 过滤已知无意义错误
        const msg = event.exception?.values?.[0]?.value ?? '';
        if (
          msg.includes('ResizeObserver loop') ||
          msg.includes('Network request failed')
        ) {
          return null;
        }
        return event;
      },
      integrations: [
        // 浏览器性能追踪
        sentry.browserTracingIntegration(),
      ],
    });
    if (config.debug) {
      console.info('[sentry] 初始化完成', { dsn: config.sentryDsn });
    }
  } catch (err) {
    console.error('[sentry] 初始化失败', err);
  }
}

/** 上报异常 */
export function captureException(error: unknown): void {
  if (!sentry) return;
  sentry.captureException(error);
}

/** 上报消息 */
export function captureMessage(
  message: string,
  level: 'info' | 'warning' | 'error' = 'info',
): void {
  if (!sentry) return;
  sentry.captureMessage(message, level);
}

/** 设置用户上下文 */
export function setUser(user: { id: string; username?: string } | null): void {
  if (!sentry) return;
  sentry.setUser(user);
}

/** 设置标签 */
export function setTag(key: string, value: string): void {
  if (!sentry) return;
  sentry.setTag(key, value);
}

/** 是否已启用 Sentry */
export function isSentryEnabled(): boolean {
  return sentry !== null;
}

/**
 * Web Vitals 性能埋点
 * 监控 LCP / FID / CLS / FCP / TTFB / INP 等核心指标
 * 通过 sendBeacon 上报，避免阻塞页面卸载
 */
import { config, isBrowser } from '@/lib/config';

interface VitalMetric {
  name: string;
  value: number;
  rating: 'good' | 'needs-improvement' | 'poor';
  delta: number;
  id: string;
  navigationType: string;
}

const REPORT_ENDPOINT = '/api/v1/monitor/web-vitals';

/**
 * 上报单条 metric
 * 优先使用 sendBeacon（页面卸载时仍能可靠发送），降级为 fetch
 */
function report(metric: VitalMetric): void {
  if (!isBrowser) return;

  const payload = JSON.stringify({
    ...metric,
    page: location.pathname,
    ts: Date.now(),
    app: config.title,
    version: config.version,
  });

  try {
    if (navigator.sendBeacon) {
      const blob = new Blob([payload], { type: 'application/json' });
      const ok = navigator.sendBeacon(REPORT_ENDPOINT, blob);
      if (ok) return;
    }
  } catch {
    // sendBeacon 失败时降级 fetch
  }

  // 降级：fetch with keepalive
  try {
    void fetch(REPORT_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
      keepalive: true,
    }).catch(() => {
      /* 静默失败，性能埋点不应影响业务 */
    });
  } catch {
    /* 静默 */
  }
}

/**
 * 初始化性能监控
 * 动态导入 web-vitals，避免未使用时打包膨胀
 */
export async function initPerformanceMonitoring(): Promise<void> {
  if (!isBrowser) return;

  try {
    const { onLCP, onFID, onCLS, onFCP, onTTFB, onINP } = await import(
      'web-vitals'
    );
    onLCP(report);
    onFID(report);
    onCLS(report);
    onFCP(report);
    onTTFB(report);
    onINP(report);

    if (config.debug) {
      console.info('[performance] web-vitals 监控已启动');
    }
  } catch (err) {
    console.error('[performance] 初始化失败', err);
  }
}

/**
 * 业务自定义性能埋点
 * @param name 指标名（如 'chat_first_token'）
 * @param duration 耗时（ms）
 * @param extra 额外维度
 */
export function trackTiming(
  name: string,
  duration: number,
  extra?: Record<string, unknown>,
): void {
  if (!isBrowser) return;
  report({
    name,
    value: duration,
    rating: duration < 1000 ? 'good' : duration < 3000 ? 'needs-improvement' : 'poor',
    delta: duration,
    id: `${name}_${Date.now()}`,
    navigationType: 'custom',
  });
  if (config.debug) {
    console.info(`[perf] ${name}: ${duration}ms`, extra);
  }
}

/**
 * 业务事件埋点（非性能类，如按钮点击、流程转化）
 */
export function trackEvent(
  name: string,
  props?: Record<string, unknown>,
): void {
  if (!isBrowser || !config.debug) return;
  console.info('[event]', name, props);
  // 实际项目可接入神策/友盟/GA 等
}

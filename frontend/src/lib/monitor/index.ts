/**
 * 监控层统一出口
 * - 错误监控: Sentry
 * - 性能监控: web-vitals
 * - 业务埋点: trackTiming / trackEvent
 */
export {
  initSentry,
  captureException,
  captureMessage,
  setUser,
  setTag,
  isSentryEnabled,
} from './sentry';

export {
  initPerformanceMonitoring,
  trackTiming,
  trackEvent,
} from './performance';

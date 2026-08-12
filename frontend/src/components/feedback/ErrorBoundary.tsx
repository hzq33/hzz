/**
 * 全局错误边界
 * - 捕获子组件树渲染期异常，避免白屏
 * - 自动上报到 Sentry
 * - 提供「重试」与「刷新」两种恢复路径
 */
import { Component, type ErrorInfo, type ReactNode } from 'react';

import { captureException } from '@/lib/monitor';

import { ErrorFallback } from './ErrorFallback';

interface ErrorBoundaryProps {
  children: ReactNode;
  /** 自定义兜底 UI；不传则使用默认 ErrorFallback */
  fallback?: (error: Error, reset: () => void) => ReactNode;
  /** 错误发生时的回调（如埋点） */
  onError?: (error: Error, info: ErrorInfo) => void;
  /** 重置键：当此数组变化时自动重置内部错误状态 */
  resetKeys?: ReadonlyArray<unknown>;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // 上报到 Sentry
    captureException(error);
    // 业务侧回调
    this.props.onError?.(error, info);
    // 控制台留痕（即便 no-console 也允许 error）
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  override componentDidUpdate(prevProps: ErrorBoundaryProps): void {
    const { resetKeys } = this.props;
    const { error } = this.state;
    if (error === null) return;
    if (prevProps.resetKeys === resetKeys) return;
    if (!resetKeys) return;
    // 任意 reset key 变化即重置
    const changed = resetKeys.some((k, i) => k !== prevProps.resetKeys?.[i]);
    if (changed) {
      this.reset();
    }
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  override render(): ReactNode {
    const { error } = this.state;
    const { children, fallback } = this.props;
    if (error !== null) {
      return fallback ? fallback(error, this.reset) : <ErrorFallback error={error} onRetry={this.reset} />;
    }
    return children;
  }
}

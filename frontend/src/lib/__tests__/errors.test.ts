/**
 * 错误文案与全局处理器单元测试
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { ApiError } from '@/api/http';
import {
  getErrorMessage,
  setupGlobalErrorHandler,
  toUserErrorMessage,
} from '@/lib/errors';

describe('getErrorMessage', () => {
  it('extracts Error.message', () => {
    expect(getErrorMessage(new Error('boom'), 'fallback')).toBe('boom');
  });

  it('returns fallback for unknown values', () => {
    expect(getErrorMessage(null, 'fallback')).toBe('fallback');
  });
});

describe('toUserErrorMessage', () => {
  it('maps AbortError', () => {
    const err = new DOMException('Aborted', 'AbortError');
    expect(toUserErrorMessage(err)).toBe('已停止生成');
  });

  it('maps 401 ApiError to actionable auth hint', () => {
    expect(toUserErrorMessage(new ApiError('unauthorized', 401))).toContain('鉴权失败');
  });

  it('maps 503 ApiError', () => {
    expect(toUserErrorMessage(new ApiError('unavailable', 503))).toContain('服务暂不可用');
  });

  it('keeps Chinese business detail for 500', () => {
    expect(toUserErrorMessage(new ApiError('无法加载角色列表', 500))).toBe('无法加载角色列表');
  });

  it('maps Failed to fetch network errors', () => {
    expect(toUserErrorMessage(new TypeError('Failed to fetch'))).toContain('无法连接服务');
  });

  it('maps known English upload message', () => {
    expect(toUserErrorMessage(new Error('Upload failed'))).toContain('导入失败');
  });

  it('maps orphan / shutdown job codes', () => {
    expect(toUserErrorMessage(new Error('orphan_after_restart'))).toContain('服务已重启');
    expect(toUserErrorMessage(new Error('cancelled_on_shutdown'))).toContain('服务正在关闭');
  });

  it('uses Chinese fallback when message is opaque English', () => {
    expect(toUserErrorMessage(new Error('xyzzy'), '请稍后重试')).toBe('请稍后重试');
  });
});

describe('setupGlobalErrorHandler', () => {
  let cleanup: (() => void) | undefined;
  let originalAddEventListener: typeof window.addEventListener;
  let originalRemoveEventListener: typeof window.removeEventListener;

  beforeEach(() => {
    originalAddEventListener = window.addEventListener;
    originalRemoveEventListener = window.removeEventListener;
    cleanup = undefined;
  });

  afterEach(() => {
    cleanup?.();
    vi.restoreAllMocks();
    window.addEventListener = originalAddEventListener;
    window.removeEventListener = originalRemoveEventListener;
  });

  it('应当注册 error 与 unhandledrejection 监听器', () => {
    const addSpy = vi.spyOn(window, 'addEventListener');
    cleanup = setupGlobalErrorHandler();

    expect(addSpy).toHaveBeenCalledWith('error', expect.any(Function));
    expect(addSpy).toHaveBeenCalledWith('unhandledrejection', expect.any(Function));
  });

  it('返回的 cleanup 函数应当移除监听器', () => {
    const removeSpy = vi.spyOn(window, 'removeEventListener');
    cleanup = setupGlobalErrorHandler();
    cleanup();

    expect(removeSpy).toHaveBeenCalledWith('error', expect.any(Function));
    expect(removeSpy).toHaveBeenCalledWith('unhandledrejection', expect.any(Function));
  });

  it('error 事件触发时应当调用 console.error', () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const handlers: Record<string, EventListener> = {};
    vi.spyOn(window, 'addEventListener').mockImplementation((type, listener) => {
      handlers[type] = listener as EventListener;
      return window;
    });

    cleanup = setupGlobalErrorHandler();
    const errorEvent = new ErrorEvent('error', {
      error: new Error('test error'),
      message: 'test error',
    });
    handlers.error?.(errorEvent);

    expect(consoleErrorSpy).toHaveBeenCalledWith('[global-error]', expect.any(Error));
    consoleErrorSpy.mockRestore();
  });
});

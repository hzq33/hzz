/**
 * 冒烟测试 — 验证测试基础设施正常工作
 */
import { describe, it, expect } from 'vitest';

import { config } from '@/lib/config';

describe('测试基础设施', () => {
  it('vitest 应当正常运行', () => {
    expect(1 + 1).toBe(2);
  });

  it('config 应当正确读取环境变量', () => {
    expect(config).toBeDefined();
    expect(typeof config.title).toBe('string');
    expect(typeof config.isDev).toBe('boolean');
    // 测试环境 MODE 应当是 'test'
    expect(config.mode).toBe('test');
  });

  it('config 默认值应当合理', () => {
    expect(config.apiBaseUrl.length).toBeGreaterThan(0);
    expect(config.tracesSampleRate).toBeGreaterThanOrEqual(0);
    expect(config.tracesSampleRate).toBeLessThanOrEqual(1);
  });
});

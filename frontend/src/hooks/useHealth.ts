/**
 * 健康状态 hook：轮询 /health，驱动侧栏状态灯。
 */
import { useCallback, useEffect, useState } from 'react';

import { fetchHealth } from '@/api/health';

export type HealthStatus = 'ok' | 'down' | 'loading';

export function useHealth(intervalMs = 20000): { status: HealthStatus; check: () => Promise<void> } {
  const [status, setStatus] = useState<HealthStatus>('loading');

  const check = useCallback(async () => {
    try {
      const h = await fetchHealth();
      // 后端 status：'ok'（简易）或 'ready'（就绪探针）都视为在线
      setStatus(h.status === 'ok' || h.status === 'ready' ? 'ok' : 'down');
    } catch {
      setStatus('down');
    }
  }, []);

  useEffect(() => {
    void check();
    const timer = setInterval(() => void check(), intervalMs);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs]);

  return { status, check };
}

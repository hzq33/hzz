/** 健康与可观测性链路：health / metrics / web-vitals */
import type { HealthResponse } from '@/types';

import { request } from './http';

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request('/api/v1/agent/health', { signal });
}

export async function fetchHealthLive(signal?: AbortSignal): Promise<Record<string, unknown>> {
  return request('/api/v1/agent/health/live', { signal });
}

export async function fetchHealthReady(signal?: AbortSignal): Promise<Record<string, unknown>> {
  return request('/api/v1/agent/health/ready', { signal });
}

export async function fetchMetrics(signal?: AbortSignal): Promise<string> {
  const res = await fetch('/metrics', { signal });
  if (!res.ok) throw new Error(`metrics HTTP ${res.status}`);
  return res.text();
}

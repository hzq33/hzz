/** LLM 配置链路：读取 / 保存端点 / 连接测试 */
import { API } from '@/lib/constants';
import type {
  LlmConfigResponse,
  LlmEndpointEdit,
  LlmTestResult,
  MemoryConfig,
} from '@/types';

import { request } from './http';

export async function fetchMemoryConfig(signal?: AbortSignal): Promise<MemoryConfig> {
  return request(API.MEMORY_CONFIG, { signal });
}

export async function saveMemoryConfig(
  cfg: Partial<MemoryConfig>,
): Promise<MemoryConfig> {
  return request(API.MEMORY_CONFIG, {
    method: 'PUT',
    body: cfg,
  });
}

export async function fetchLlmConfig(signal?: AbortSignal): Promise<LlmConfigResponse> {
  return request(API.LLM_CONFIG, { signal });
}

export async function saveLlmEndpoint(
  endpoint: string,
  config: LlmEndpointEdit,
): Promise<LlmConfigResponse> {
  return request(API.LLM_CONFIG, {
    method: 'PUT',
    body: { endpoint, config },
  });
}

export async function testLlmEndpoint(
  endpoint: string,
  config: LlmEndpointEdit,
): Promise<LlmTestResult> {
  return request(API.LLM_CONFIG_TEST, {
    method: 'POST',
    body: { endpoint, config },
  });
}

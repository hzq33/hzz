/**
 * HTTP 基础层：统一 fetch、错误解析、Abort、query 序列化。
 * 各域模块（chat/impersonation/novels/...）基于本层封装。
 */

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export async function extractErrorDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail
        .map((d: unknown) => String((d as { msg?: string })?.msg ?? d))
        .join('; ');
    }
    return `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  /** JSON body 或 FormData */
  body?: unknown;
  signal?: AbortSignal;
  /** 不抛错的 status 集合（调用方自行处理响应体） */
  okStatuses?: number[];
}

export async function request<T>(url: string, opts: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, okStatuses } = opts;

  const headers: Record<string, string> = {};
  let payload: BodyInit | undefined;

  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }

  const res = await fetch(url, { method, headers, body: payload, signal });

  if (!res.ok && !okStatuses?.includes(res.status)) {
    const detail = await extractErrorDetail(res);
    throw new ApiError(detail, res.status);
  }

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    return text as unknown as T;
  }
}

/** 带 query 序列化（跳过空值） */
export function qs(params: Record<string, unknown> | undefined): string {
  if (!params) return '';
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    if (typeof v === 'string') {
      sp.set(k, v);
    } else if (typeof v === 'number' || typeof v === 'boolean') {
      sp.set(k, String(v));
    }
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
}

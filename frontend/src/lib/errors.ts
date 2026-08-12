/**
 * 全局错误处理与用户可读文案
 * 捕获未处理的 Promise rejection 与同步异常，统一上报 Sentry
 * 应在 main.tsx 中尽早调用 setupGlobalErrorHandler()
 */
import { config, isBrowser } from '@/lib/config';
import { captureException, isSentryEnabled } from '@/lib/monitor';

const DEFAULT_FALLBACK = '操作失败，请稍后重试';

/** 已知英文/技术短语 → 中文可行动提示 */
const MESSAGE_MAP: Array<{ pattern: RegExp; message: string }> = [
  {
    pattern: /failed to fetch|networkerror|\bload failed\b|network request failed/i,
    message: '无法连接服务：请确认后端已启动（默认 http://localhost:8080），且前端代理配置正确。',
  },
  {
    pattern: /no response body/i,
    message: '服务未返回内容，请稍后重试；若持续出现请检查后端日志。',
  },
  {
    pattern: /stream error/i,
    message: '流式响应中断，请重试；若频繁出现请检查网络或后端状态。',
  },
  {
    pattern: /failed to fetch health/i,
    message: '健康检查失败：请确认后端已启动并可访问。',
  },
  {
    pattern: /failed to fetch tools/i,
    message: '无法获取工具列表，请确认后端已启动。',
  },
  {
    pattern: /failed to fetch characters/i,
    message: '无法加载角色列表，请稍后重试或检查工作台是否已导入小说。',
  },
  {
    pattern: /failed to fetch candidates/i,
    message: '无法加载候选角色，请确认已导入小说并选择系列。',
  },
  {
    pattern: /failed to fetch novels/i,
    message: '无法加载书目列表，请确认后端已启动。',
  },
  {
    pattern: /failed to fetch story analysis/i,
    message: '无法加载剧情分析，请稍后重试。',
  },
  {
    pattern: /failed to load/i,
    message: '加载失败：请检查网络与后端服务后重试。',
  },
  {
    pattern: /upload failed/i,
    message: '导入失败：请确认文件为 EPUB/TXT/MD，且后端服务正常。',
  },
  {
    pattern: /batch upload failed/i,
    message: '批量导入失败：请检查文件格式后重试。',
  },
  {
    pattern: /rename failed/i,
    message: '重命名失败，请稍后重试。',
  },
  {
    pattern: /delete failed/i,
    message: '删除失败，请稍后重试。',
  },
  {
    pattern: /build failed/i,
    message: '角色卡生成失败：可稍后重试，或换样本更充足的角色。',
  },
  {
    pattern: /story analysis failed/i,
    message: '剧情分析失败：请稍后重试，或确认该系列已完成导入。',
  },
  {
    pattern: /regenerate failed/i,
    message: '重新生成失败，请稍后重试。',
  },
  {
    pattern: /request failed/i,
    message: '请求失败，请稍后重试。',
  },
  {
    pattern: /角色卡生成超时/i,
    message:
      '角色卡生成超时：任务可能仍在后台排队，请稍后刷新角色列表；未完成可重新勾选生成。',
  },
  {
    pattern: /导入超时/i,
    message:
      '导入超时：大文件嵌入可能仍在后台进行，请稍后刷新书目查看；若未出现请重试导入。',
  },
  {
    pattern: /分析超时/i,
    message: '剧情分析超时：任务可能仍在后台运行，请稍后刷新查看。',
  },
  {
    pattern: /timeout|timed?\s*out|Job polling timed out/i,
    message: '操作超时：后台任务可能仍在运行，请稍后刷新查看进度；若未完成请重试。',
  },
  {
    pattern: /orphan_after_restart/i,
    message: '服务已重启，后台任务已中断，请重新触发导入或分析。',
  },
  {
    pattern: /cancelled_on_shutdown/i,
    message: '服务正在关闭，任务已取消，请稍后重新触发。',
  },
  {
    pattern: /budget|quota|token.?limit/i,
    message: '会话预算或额度已用尽，请清除会话后重试，或调整后端预算配置。',
  },
];

function getHttpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object' && 'status' in err) {
    const status = (err).status;
    if (typeof status === 'number' && Number.isFinite(status)) return status;
  }
  const raw = rawMessage(err);
  const m = raw.match(/\bHTTP\s*(\d{3})\b/i) || raw.match(/\b(\d{3})\b/);
  if (m) {
    const code = Number(m[1]);
    if (code >= 400 && code < 600) return code;
  }
  return undefined;
}

function rawMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === 'string') return err;
  return '';
}

function isAbortError(err: unknown): boolean {
  if (!err || typeof err !== 'object') return false;
  const name = (err as { name?: string }).name;
  if (name === 'AbortError') return true;
  return /aborted|abort/i.test(rawMessage(err));
}

function messageForStatus(status: number): string | null {
  switch (status) {
    case 401:
      return '鉴权失败：请检查后端 AGENT_API_TOKEN 是否已配置，并与前端代理注入的 Token 一致。';
    case 403:
      return '没有权限执行此操作。';
    case 404:
      return '请求的资源不存在，请刷新后重试。';
    case 408:
      return '请求超时，请稍后重试。';
    case 429:
      return '请求过于频繁或会话预算已用尽，请稍后再试。';
    case 502:
    case 504:
      return '网关错误：请确认后端服务正在运行。';
    case 503:
      return '服务暂不可用：请确认后端已启动，且已配置 AGENT_API_TOKEN。';
    default:
      if (status >= 500) return '服务端出错，请查看后端日志后重试。';
      return null;
  }
}

function mapKnownMessage(raw: string): string | null {
  for (const { pattern, message } of MESSAGE_MAP) {
    if (pattern.test(raw)) return message;
  }
  return null;
}

function looksChinese(text: string): boolean {
  return /[\u4e00-\u9fff]/.test(text);
}

/**
 * 从未知错误中提取原始消息字符串（不做本地化）。
 */
export function getErrorMessage(err: unknown, fallback = DEFAULT_FALLBACK): string {
  const raw = rawMessage(err);
  return raw || fallback;
}

/**
 * 将任意错误转为用户可读的中文可行动提示。
 * 优先：中止 → HTTP 状态 → 已知英文短语 → 已是中文的服务端 detail → fallback
 */
export function toUserErrorMessage(err: unknown, fallback = DEFAULT_FALLBACK): string {
  if (isAbortError(err)) return '已停止生成';

  const raw = rawMessage(err).trim();
  const status = getHttpStatus(err);

  if (status != null) {
    const byStatus = messageForStatus(status);
    // 鉴权 / 限流 / 服务不可用：优先状态文案（更可行动）
    if (
      byStatus &&
      (status === 401 || status === 403 || status === 429 || status === 503)
    ) {
      return byStatus;
    }
    // 已有中文业务 detail 时保留
    if (raw && looksChinese(raw) && !/^HTTP\s*\d+/i.test(raw)) {
      return raw;
    }
    if (byStatus) return byStatus;
  }

  if (!raw) return fallback;

  const mapped = mapKnownMessage(raw);
  if (mapped) return mapped;

  if (looksChinese(raw)) return raw;

  return fallback;
}

/**
 * 安装全局错误处理器
 * - window.addEventListener('error', ...) 捕获同步异常
 * - window.addEventListener('unhandledrejection', ...) 捕获未 await 的 Promise
 */
export function setupGlobalErrorHandler(): () => void {
  if (!isBrowser) return () => {};

  const onError = (event: ErrorEvent): void => {
    const error: unknown = event.error ?? new Error(event.message);
    console.error('[global-error]', error);
    captureException(error);
  };

  const onRejection = (event: PromiseRejectionEvent): void => {
    const reason: unknown = event.reason;
    const error: Error =
      reason instanceof Error
        ? reason
        : new Error(`Unhandled promise rejection: ${String(reason)}`);
    console.error('[unhandled-rejection]', error);
    captureException(error);
  };

  window.addEventListener('error', onError);
  window.addEventListener('unhandledrejection', onRejection);

  if (config.debug) {
    console.info(
      `[errors] 全局错误处理器已安装 (sentry=${isSentryEnabled() ? 'on' : 'off'})`,
    );
  }

  return () => {
    window.removeEventListener('error', onError);
    window.removeEventListener('unhandledrejection', onRejection);
  };
}

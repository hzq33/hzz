/** Shared SSE line parsing helpers for agent streams. */

export function parseJSONSafe<T>(text: string): T | null {
  try {
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}

/**
 * Consume an SSE byte stream, invoking ``onData`` for each ``data:`` payload.
 * Returns when the stream ends or the optional AbortSignal aborts.
 */
export async function readSSEStream(
  body: ReadableStream<Uint8Array>,
  onData: (json: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const abort = () => {
    void reader.cancel().catch(() => {
      /* noop */
    });
  };
  signal?.addEventListener('abort', abort, { once: true });

  try {
    while (true) {
      if (signal?.aborted) break;
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const jsonStr = line.slice(6).trim();
        if (!jsonStr) continue;
        onData(jsonStr);
      }
    }
  } finally {
    signal?.removeEventListener('abort', abort);
  }
}

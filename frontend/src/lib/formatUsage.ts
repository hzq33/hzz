/** Format LLM usage / latency for chat footers. */

export type TokenUsage = {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  cost_usd?: number;
  model?: string;
};

export function formatElapsedMs(elapsedMs?: number | null): string | null {
  if (elapsedMs == null || Number.isNaN(elapsedMs)) return null;
  return `${(elapsedMs / 1000).toFixed(1)}s`;
}

export function formatCostUsd(cost?: number | null): string | null {
  if (cost == null || Number.isNaN(cost) || cost < 0) return null;
  if (cost === 0) return '$0';
  if (cost < 0.0001) return `$${cost.toFixed(6)}`;
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  return `$${cost.toFixed(4)}`;
}

export function formatTokenUsage(usage?: TokenUsage | null): string | null {
  if (!usage) return null;
  const parts: string[] = [];
  const total = usage.total_tokens;
  if (total != null && total > 0) {
    parts.push(`${total} tok`);
    if (usage.prompt_tokens != null && usage.completion_tokens != null) {
      parts.push(`${usage.prompt_tokens}↑/${usage.completion_tokens}↓`);
    }
  } else if (usage.prompt_tokens != null || usage.completion_tokens != null) {
    const p = usage.prompt_tokens ?? 0;
    const c = usage.completion_tokens ?? 0;
    parts.push(`${p + c} tok · ${p}↑/${c}↓`);
  }
  const cost = formatCostUsd(usage.cost_usd);
  if (cost) parts.push(cost);
  return parts.length ? parts.join(' · ') : null;
}

export function formatMetaLine(opts: {
  elapsedMs?: number | null;
  usage?: TokenUsage | null;
}): string | null {
  const bits = [formatElapsedMs(opts.elapsedMs), formatTokenUsage(opts.usage)].filter(
    Boolean,
  ) as string[];
  return bits.length ? bits.join(' · ') : null;
}

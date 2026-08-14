import { useState } from 'react';

import { Badge } from '@/components/ui/aura';
import { evidenceRelevance, splitEvidenceByRole } from '@/lib/streamReducers';
import type { StoryEvidence } from '@/types';

export function CitationChips({ citations }: { citations: StoryEvidence[] }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const { fact, style } = splitEvidenceByRole(citations);
  if (citations.length === 0) return null;

  const renderChip = (c: StoryEvidence, i: number, tone: 'brand' | 'accent') => {
    const id = `${c.block_id || i}-${tone}`;
    const rel = evidenceRelevance(c);
    const open = openId === id;
    return (
      <div key={id} className="min-w-0">
        <button
          type="button"
          className="cite-chip"
          onClick={() => setOpenId(open ? null : id)}
        >
          <Badge tone={tone}>{c.channel || (tone === 'brand' ? '出处' : '口吻')}</Badge>
          <span className="truncate max-w-[140px]">{c.chapter_title || c.doc_id || '原文'}</span>
          {rel != null ? <span className="tabular-nums text-faint">{Math.round(rel * 100)}%</span> : null}
        </button>
        {open && c.snippet ? (
          <p className="mt-1 rounded-lg border border-line/60 bg-surface-2 px-2 py-1.5 text-[11px] text-ink leading-relaxed">
            {c.snippet}
          </p>
        ) : null}
      </div>
    );
  };

  return (
    <div className="mt-2 pt-2 border-t border-line space-y-1.5">
      {fact.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {fact.map((c, i) => renderChip(c, i, 'brand'))}
        </div>
      ) : null}
      {style.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {style.map((c, i) => renderChip(c, i, 'accent'))}
        </div>
      ) : null}
    </div>
  );
}

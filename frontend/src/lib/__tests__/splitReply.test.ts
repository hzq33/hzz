import { describe, expect, it } from 'vitest';

import { splitReplySegments } from '@/components/chat/splitReply';

describe('splitReplySegments', () => {
  it('splits on blank lines outside code fences', () => {
    expect(splitReplySegments('你好。\n\n我回来了。')).toEqual(['你好。', '我回来了。']);
  });

  it('keeps fenced code as one segment', () => {
    const src = '前言\n\n```js\nconst a = 1;\n\nconst b = 2;\n```\n\n结尾';
    const parts = splitReplySegments(src);
    expect(parts).toHaveLength(3);
    expect(parts[1]).toContain('```js');
    expect(parts[1]).toContain('const b');
  });
});
